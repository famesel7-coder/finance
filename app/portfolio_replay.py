import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx
import pandas as pd
from pydantic import BaseModel, Field

from app.backtest import maximum_drawdown
from app.broker import PaperAccount, apply_fill
from app.config import get_settings
from app.moex import MoexIssProvider


class Holding(BaseModel):
    shares: int = Field(gt=0)
    lot: int = Field(default=1, gt=0)


class Dividend(BaseModel):
    ticker: str
    eligible_on: date
    paid_on: date
    net_per_share: Decimal = Field(ge=0)


class Split(BaseModel):
    ticker: str
    effective_on: date
    new_shares: int = Field(gt=0)
    old_shares: int = Field(gt=0)
    lot: int | None = Field(default=None, gt=0)


class Rebalance(BaseModel):
    decided_on: date
    sell: list[str] = Field(min_length=1)
    buy: list[str] = Field(min_length=1)
    budget_rub: Decimal = Field(default=Decimal(30_000), gt=0)


class Scenario(BaseModel):
    as_of: date
    until: date
    cash_rub: Decimal = Field(default=Decimal(0), ge=0)
    positions: dict[str, Holding] = Field(min_length=1)
    dividends: list[Dividend] = Field(default_factory=list)
    splits: list[Split] = Field(default_factory=list)
    reinvest_in: list[str] = Field(default_factory=list)
    rebalance: Rebalance
    commission_bps: Decimal = Field(default=Decimal(30), ge=0, le=1000)
    spread_bps: Decimal = Field(default=Decimal(10), ge=0, le=1000)


@dataclass
class PaperPortfolio:
    account: PaperAccount
    lots: dict[str, int]
    fee_rate: Decimal
    half_spread: Decimal
    receivables: list[tuple[date, date, Decimal]] = field(default_factory=list)
    credited: Decimal = Decimal(0)
    reinvest_cash: Decimal = Decimal(0)
    expenses: Decimal = Decimal(0)
    turnover: Decimal = Decimal(0)

    def credit(self, day: date, include_today: bool) -> None:
        remaining = []
        for eligible_on, paid_on, amount in self.receivables:
            if paid_on < day or (include_today and paid_on == day):
                self.account.cash += amount
                self.credited += amount
                self.reinvest_cash += amount
            else:
                remaining.append((eligible_on, paid_on, amount))
        self.receivables = remaining

    def fill(
        self, ticker: str, side: Literal["buy", "sell"], shares: int, mark: Decimal
    ) -> Decimal:
        spread = self.half_spread if side == "buy" else -self.half_spread
        price = mark * (1 + spread)
        fee = apply_fill(self.account, ticker, side, shares, price, self.fee_rate)
        self.expenses += fee + abs(price - mark) * shares
        self.turnover += price * shares
        return price * shares + (fee if side == "buy" else -fee)

    def buy(self, tickers: list[str], quotes: dict[str, Decimal], budget: Decimal) -> Decimal:
        eligible = [ticker for ticker in tickers if ticker in quotes]
        if not eligible or budget <= 0:
            return Decimal(0)
        allocation = min(self.account.cash, budget) / len(eligible)
        spent = Decimal(0)
        for ticker in eligible:
            lot = self.lots[ticker]
            lot_cost = quotes[ticker] * lot * (1 + self.half_spread) * (1 + self.fee_rate)
            shares = int(allocation // lot_cost) * lot
            spent += self.fill(ticker, "buy", shares, quotes[ticker])
        return spent

    def rebalance(self, plan: Rebalance, quotes: dict[str, Decimal]) -> Decimal:
        remaining = plan.budget_rub
        proceeds = Decimal(0)
        for ticker in plan.sell:
            lot = self.lots[ticker]
            trade_price = quotes[ticker] * (1 - self.half_spread)
            held = self.account.positions.get(ticker, 0)
            shares = min(int(remaining // (trade_price * lot)), held // lot) * lot
            proceeds += self.fill(ticker, "sell", shares, quotes[ticker])
            remaining -= trade_price * shares
        self.buy(plan.buy, quotes, proceeds)
        return plan.budget_rub - remaining

    def valuation(self, marks: dict[str, Decimal], day: date) -> Decimal:
        return (
            self.account.cash
            + sum(marks[ticker] * shares for ticker, shares in self.account.positions.items())
            + sum(amount for eligible_on, _, amount in self.receivables if eligible_on < day)
        )


def compare_scenarios(
    plan: Scenario,
    market: pd.DataFrame,
    benchmark: pd.Series | None = None,
) -> dict:
    tickers = set(plan.positions)
    targets = list(dict.fromkeys(plan.reinvest_in or sorted(tickers)))
    sales, purchases = set(plan.rebalance.sell), set(plan.rebalance.buy)
    if not (set(targets) | sales | purchases) <= tickers or sales & purchases:
        raise ValueError("Use distinct sell and buy groups drawn from the existing portfolio")
    if len(sales) != len(plan.rebalance.sell) or len(purchases) != len(plan.rebalance.buy):
        raise ValueError("List each rebalance instrument once")
    if plan.until <= plan.as_of or plan.rebalance.decided_on < plan.as_of:
        raise ValueError(
            "Use a valid nonempty date range and a rebalance decision on or after as_of"
        )
    if any(d.ticker not in tickers or d.paid_on <= d.eligible_on for d in plan.dividends):
        raise ValueError(
            "Dividend eligibility and payment dates must be valid for held instruments"
        )
    if any(s.ticker not in tickers for s in plan.splits):
        raise ValueError("Splits must refer to held instruments")

    data = market.reset_index()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    data = data[data["ticker"].isin(tickers)].set_index(["date", "ticker"]).sort_index()
    if data.index.has_duplicates:
        raise ValueError("There must be one quote per ticker and trading session")
    start, end = pd.Timestamp(plan.as_of), pd.Timestamp(plan.until)
    opens = data["open"].unstack("ticker").loc[start:end]
    closes = data["close"].unstack("ticker").loc[start:end]
    missing = tickers - (set(_prices(closes.loc[start])) if start in closes.index else set())
    if missing:
        raise ValueError("No starting close for " + ", ".join(sorted(missing)))
    if len(closes) < 2:
        raise ValueError("At least one completed session after the starting snapshot is required")

    dividends = defaultdict(list)
    splits = defaultdict(list)
    sessions = set(closes.index.date)
    for event in plan.dividends:
        if plan.as_of < event.eligible_on <= closes.index[-1].date():
            if event.eligible_on not in sessions:
                raise ValueError("A dividend eligibility date must be a quoted trading session")
            dividends[event.eligible_on].append(event)
    for event in plan.splits:
        if plan.as_of < event.effective_on <= closes.index[-1].date():
            if event.effective_on not in sessions:
                raise ValueError("A split effective date must be a quoted trading session")
            splits[event.effective_on].append(event)

    result = {}
    for name in ("hold", "reinvest", "rebalance"):
        simulation = PaperPortfolio(
            PaperAccount(plan.cash_rub, {t: h.shares for t, h in plan.positions.items()}),
            {t: h.lot for t, h in plan.positions.items()},
            plan.commission_bps / 10_000,
            plan.spread_bps / 20_000,
        )
        marks = _prices(closes.loc[start])
        initial = simulation.valuation(marks, plan.as_of)
        values = [float(initial)]
        rebalance_on = None
        rebalance_value = Decimal(0)
        for session in closes.index[1:]:
            day = session.date()
            quotes = _prices(opens.loc[session])
            for split in splits[day]:
                quantity, fraction = divmod(
                    simulation.account.positions.get(split.ticker, 0) * split.new_shares,
                    split.old_shares,
                )
                if fraction:
                    raise ValueError("A fractional split entitlement needs separate cash data")
                if quantity:
                    simulation.account.positions[split.ticker] = quantity
                marks[split.ticker] *= Decimal(split.old_shares) / split.new_shares
                if split.lot is not None:
                    simulation.lots[split.ticker] = split.lot

            simulation.credit(day, include_today=False)
            if (
                name == "rebalance"
                and rebalance_on is None
                and day > plan.rebalance.decided_on
                and (sales | purchases) <= quotes.keys()
            ):
                rebalance_value = simulation.rebalance(plan.rebalance, quotes)
                rebalance_on = day

            if name != "hold":
                simulation.reinvest_cash -= simulation.buy(
                    targets, quotes, simulation.reinvest_cash
                )
            for event in dividends[day]:
                amount = event.net_per_share * simulation.account.positions.get(event.ticker, 0)
                simulation.receivables.append((event.eligible_on, event.paid_on, amount))
            simulation.credit(day, include_today=True)
            marks.update(_prices(closes.loc[session]))
            values.append(float(simulation.valuation(marks, day)))

        result[name] = {
            "net_dividends_rub": round(float(simulation.credited), 2),
            "total_return_pct": round((values[-1] / values[0] - 1) * 100, 4),
            "max_drawdown_pct": round(maximum_drawdown(values, values[0]) * 100, 4),
            "trade_expenses_rub": round(float(simulation.expenses), 2),
            "turnover_rub": round(float(simulation.turnover), 2),
        }
        if name == "rebalance":
            result[name]["rebalance_on"] = rebalance_on.isoformat() if rebalance_on else None
            result[name]["rebalance_sold_rub"] = round(float(rebalance_value), 2)

    report = {
        "as_of": plan.as_of.isoformat(),
        "last_complete_session": closes.index[-1].date().isoformat(),
        "sessions": len(closes),
        "scenarios": result,
        "basis": "Net dividends provided, with execution costs. Sales tax is outside this replay.",
    }
    if benchmark is not None:
        first = benchmark.get(start)
        last = benchmark.get(closes.index[-1])
        if (
            first is None
            or last is None
            or pd.isna(first)
            or pd.isna(last)
            or min(first, last) <= 0
        ):
            raise ValueError("MCFTR needs positive quotes on the starting and final sessions")
        report["mcftr_gross_return_pct"] = round((float(last) / float(first) - 1) * 100, 4)
    return report


def _prices(row: pd.Series) -> dict[str, Decimal]:
    return {str(t): Decimal(str(p)) for t, p in row.items() if pd.notna(p) and 0 < p < float("inf")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare three paper dividend scenarios.")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--quotes", type=Path, help="CSV with date,ticker,open,close")
    parser.add_argument("--benchmark", type=Path, help="MCFTR CSV with date,close")
    args = parser.parse_args()
    try:
        spec = Scenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
        if args.quotes:
            market = pd.read_csv(args.quotes, parse_dates=["date"]).set_index(["date", "ticker"])
        else:
            provider = MoexIssProvider(ca_file=get_settings().moex_ca_file)
            days = (pd.Timestamp.today().date() - spec.as_of).days + 2
            market = provider.history(sorted(spec.positions), f"{days}d")
        benchmark = None
        if args.benchmark:
            benchmark = pd.read_csv(args.benchmark, parse_dates=["date"]).set_index("date")["close"]
        elif not args.quotes:
            benchmark = provider.index_close(spec.as_of, spec.until)
        report = compare_scenarios(spec, market, benchmark)
    except (OSError, ValueError, httpx.HTTPError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
