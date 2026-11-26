"""Read-only portfolio risk and cash report from the broker journal."""

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from app.portfolio_journal import CASH_FLOW_FIELDS, money_value

MOSCOW = ZoneInfo("Europe/Moscow")
CASH_TRANSFERS = {
    "INPUT",
    "OUTPUT",
    "INPUT_SWIFT",
    "OUTPUT_SWIFT",
    "INPUT_ACQUIRING",
    "OUTPUT_ACQUIRING",
}


@dataclass(frozen=True)
class Valuation:
    captured_at: datetime
    operations_from: datetime
    nav: Decimal | None


def rubles(value: dict | None) -> Decimal | None:
    if not value or value.get("currency", "").lower() != "rub":
        return None
    return money_value(value)


def ruble_stock_marks(portfolio: dict, instruments: dict) -> list[dict]:
    marks = []
    by_figi = {item["figi"]: item for item in instruments.values() if item.get("figi")}
    for position in portfolio.get("positions", []):
        if position.get("instrumentType") != "share":
            continue
        figi = position.get("figi")
        key = position.get("instrumentUid") or figi
        instrument = instruments.get(key) or by_figi.get(figi) or {}
        price = rubles(position.get("currentPrice"))
        quantity = money_value(position.get("quantity") or {})
        if instrument.get("classCode") != "TQBR" or price is None or price <= 0 or quantity <= 0:
            continue
        average = rubles(position.get("averagePositionPriceFifo"))
        marks.append(
            {
                "ticker": instrument["ticker"],
                "market_value": quantity * price,
                "fifo_cost": quantity * average if average is not None and average > 0 else None,
            }
        )
    return marks


def cash_transfer(event: dict) -> Decimal | None:
    kind = event.get("type", "").removeprefix("OPERATION_TYPE_")
    if kind in CASH_TRANSFERS:
        amount = rubles(event.get("payment"))
        input_side = kind.startswith("INPUT")
        if amount is None or amount and (amount > 0) != input_side:
            return None
        return amount
    if (
        not kind
        or kind in {"BUY_CARD", "UNSPECIFIED"}
        or any(part in kind for part in ("INPUT", "OUTPUT", "TRANSFER"))
    ):
        return None
    return Decimal(0)


def performance(valuations: list[Valuation], events: list[tuple[datetime, dict]]) -> dict:
    """Link Modified Dietz returns between journal marks with priced cash flows."""
    first = valuations[0].captured_at
    latest_segment = first
    observations = 1
    resets = 0
    index = peak = Decimal(1)
    worst = Decimal(0)
    remaining = iter((stamp, event) for stamp, event in events if stamp > first)
    pending = next(remaining, None)
    for previous, current in pairwise(valuations):
        elapsed = Decimal(str((current.captured_at - previous.captured_at).total_seconds()))
        valid = elapsed > 0 and current.operations_from <= previous.captured_at
        net = weighted = Decimal(0)
        while pending is not None and pending[0] <= current.captured_at:
            timestamp, event = pending
            transfer = cash_transfer(event)
            if transfer is None:
                valid = False
            elif transfer and elapsed > 0:
                remaining_seconds = (current.captured_at - timestamp).total_seconds()
                net += transfer
                weighted += transfer * Decimal(str(remaining_seconds)) / elapsed
            pending = next(remaining, None)

        opening = previous.nav
        closing_nav = current.nav
        capital = opening + weighted if opening is not None else Decimal(0)
        change = None
        if (
            valid
            and opening is not None
            and opening > 0
            and closing_nav is not None
            and closing_nav > 0
            and capital > 0
        ):
            change = (closing_nav - opening - net) / capital
        if change is None or change <= -1:
            latest_segment = current.captured_at
            observations = 1
            resets += 1
            index = peak = Decimal(1)
            worst = Decimal(0)
            continue
        index *= 1 + change
        peak = max(peak, index)
        worst = min(worst, index / peak - 1)
        observations += 1
    measured = observations >= 2
    return {
        "from_msk": latest_segment.astimezone(MOSCOW).isoformat(timespec="seconds"),
        "observations": observations,
        "discontinuities": resets,
        "return_pct": (index - 1) * 100 if measured else None,
        "drawdown_pct": (index / peak - 1) * 100 if measured else None,
        "max_drawdown_pct": worst * 100 if measured else None,
    }


def build_report(
    database: Path,
    *,
    reserve_pct: Decimal = Decimal(5),
    weight_watch_pct: Decimal = Decimal(10),
    drawdown_watch_pct: Decimal = Decimal(5),
) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        snapshots = list(
            db.execute(
                "SELECT captured_at, operations_from, payload FROM portfolio_snapshots ORDER BY captured_at"
            )
        )
        if not snapshots:
            raise ValueError("Journal has no snapshots. Run sync first.")
        account_id = None
        daily = {}
        portfolio = instruments = {}
        for captured, operations_from, raw in snapshots:
            snapshot = json.loads(raw)
            instruments = snapshot["instruments"]
            accounts = {row["account"]["id"]: row["portfolio"] for row in snapshot["accounts"]}
            if account_id is None:
                stock_values = {
                    key: sum(
                        row["market_value"] for row in ruble_stock_marks(holdings, instruments)
                    )
                    for key, holdings in accounts.items()
                }
                account_id = max(stock_values, key=stock_values.get, default=None)
                if account_id is None or stock_values[account_id] <= 0:
                    account_id = None
                    continue
            portfolio = accounts.get(account_id, {})
            stamp = datetime.fromisoformat(captured)
            daily[stamp.astimezone(MOSCOW).date()] = Valuation(
                stamp,
                datetime.fromisoformat(operations_from),
                rubles(portfolio.get("totalAmountPortfolio")),
            )

        if account_id is None or not daily:
            raise ValueError("Journal has no account with priced TQBR shares.")
        valuations = list(daily.values())
        latest = valuations[-1]
        if latest.nav is None or latest.nav <= 0:
            raise ValueError("Primary account needs a positive RUB valuation.")
        nav = latest.nav
        begin = min(latest.operations_from, valuations[0].captured_at)
        events = [
            (datetime.fromisoformat(timestamp), json.loads(raw))
            for timestamp, raw in db.execute(
                "SELECT event_at, payload FROM portfolio_operations WHERE account_id = ? "
                "AND event_at BETWEEN ? AND ? ORDER BY event_at, operation_id",
                (
                    account_id,
                    begin.isoformat(timespec="microseconds"),
                    latest.captured_at.isoformat(timespec="microseconds"),
                ),
            )
        ]

    positions = sorted(
        ruble_stock_marks(portfolio, instruments), key=lambda row: row["market_value"], reverse=True
    )
    with_basis = [p for p in positions if p["fifo_cost"] is not None]
    cost = sum((p["fifo_cost"] for p in with_basis), Decimal(0))
    price_result = sum((p["market_value"] - p["fifo_cost"] for p in with_basis), Decimal(0))
    for position in positions:
        position["weight_pct"] = position["market_value"] / nav * 100
    cash = sum(
        (
            money_value(row.get("quantity") or {})
            for row in portfolio.get("positions", [])
            if row.get("instrumentType") == "currency" and row.get("figi") == "RUB000UTSTOM"
        ),
        Decimal(0),
    )
    net_dividends = Decimal(0)
    for stamp, event in events:
        field = CASH_FLOW_FIELDS.get(event.get("type"))
        if stamp >= latest.operations_from and field in {"dividends", "dividend_tax"}:
            net_dividends += rubles(event.get("payment")) or Decimal(0)

    reserve = nav * reserve_pct / 100
    returns = performance(valuations, events)
    drawdown = returns["drawdown_pct"]
    return {
        "snapshot_msk": latest.captured_at.astimezone(MOSCOW).isoformat(timespec="seconds"),
        "operations_from_msk": latest.operations_from.astimezone(MOSCOW).isoformat(
            timespec="seconds"
        ),
        "account_value_rub": nav,
        "ruble_stock_positions": positions,
        "stock_positions_with_fifo_cost": len(with_basis),
        "stock_fifo_cost_rub": cost,
        "unrealized_stock_result_rub": price_result if with_basis else None,
        "unrealized_stock_result_pct": price_result / cost * 100 if cost else None,
        "net_rub_dividends": net_dividends,
        "rub_cash": cash,
        "reserve_target_rub": reserve,
        "reserve_shortfall_rub": max(reserve - cash, Decimal(0)),
        "rub_cash_above_reserve": max(cash - reserve, Decimal(0)),
        "positions_at_weight_limit": [
            p["ticker"] for p in positions if p["weight_pct"] >= weight_watch_pct
        ],
        "drawdown_review": drawdown is not None and drawdown <= -drawdown_watch_pct,
        "returns": returns,
        "thresholds_pct": {
            "reserve": reserve_pct,
            "position_weight": weight_watch_pct,
            "drawdown": drawdown_watch_pct,
        },
    }


def write_report(path: Path, report: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".portfolio-risk-", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(report + "\n")
            output.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only risk report for the primary TQBR account."
    )
    parser.add_argument("--database", type=Path, default=Path("data/portfolio.sqlite3"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reserve-pct", type=Decimal, default=Decimal(5))
    parser.add_argument("--weight-watch-pct", type=Decimal, default=Decimal(10))
    parser.add_argument("--drawdown-watch-pct", type=Decimal, default=Decimal(5))
    args = parser.parse_args()
    if not all(
        pct.is_finite() and 0 < pct < 100
        for pct in (args.reserve_pct, args.weight_watch_pct, args.drawdown_watch_pct)
    ):
        parser.error("Threshold percentages must be between 0 and 100.")
    try:
        report = build_report(
            args.database,
            reserve_pct=args.reserve_pct,
            weight_watch_pct=args.weight_watch_pct,
            drawdown_watch_pct=args.drawdown_watch_pct,
        )
        output = json.dumps(report, ensure_ascii=False, default=str, indent=2)
        if args.output is not None:
            write_report(args.output, output)
            print(f"Portfolio risk report saved for {report['snapshot_msk']}.")
        else:
            print(output)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
