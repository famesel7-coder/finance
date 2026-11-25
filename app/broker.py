import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


@dataclass
class PaperAccount:
    cash: Decimal
    positions: dict[str, int]

    def snapshot(self) -> dict:
        return {"cash": float(self.cash), "positions": dict(self.positions)}


class PaperBroker:
    def __init__(
        self,
        state_path: Path = Path("paper_state.json"),
        initial_cash: float = 100_000.0,
        commission_bps: float = 30.0,
        spread_bps: float = 10.0,
    ):
        self.state_path = state_path
        self.database_path = (
            state_path.with_suffix(".sqlite3") if state_path.suffix == ".json" else state_path
        )
        self.initial_cash = Decimal(str(initial_cash))
        self.commission = Decimal(str(commission_bps)) / 10_000
        self.half_spread = Decimal(str(spread_bps)) / 20_000

    def submit(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: float,
        *,
        lot_size: int = 1,
        available_lots: int | None = None,
        order_id: str | None = None,
        bid: float | None = None,
        ask: float | None = None,
    ) -> dict:
        midpoint = _positive_price(price)
        buy_quote = _positive_price(ask) if ask is not None else midpoint * (1 + self.half_spread)
        sell_quote = _positive_price(bid) if bid is not None else midpoint * (1 - self.half_spread)
        ticker = ticker.strip().upper()
        if not ticker or side not in {"buy", "sell"}:
            raise ValueError("A ticker and a buy or sell side are required")
        if lot_size <= 0 or quantity <= 0 or quantity % lot_size:
            raise ValueError("Order quantity must be a positive multiple of the lot size")
        if available_lots is not None and available_lots < 0:
            raise ValueError("Available lots cannot be negative")
        if bid is not None and ask is not None and sell_quote > buy_quote:
            raise ValueError("Bid cannot exceed ask")

        requested_lots = quantity // lot_size
        filled_lots = (
            requested_lots if available_lots is None else min(requested_lots, available_lots)
        )
        price_per_unit = buy_quote if side == "buy" else sell_quote
        fingerprint = json.dumps(
            [
                ticker,
                side,
                quantity,
                str(midpoint.normalize()),
                lot_size,
                available_lots,
                str(sell_quote.normalize()) if bid is not None else None,
                str(buy_quote.normalize()) if ask is not None else None,
            ]
        )
        order_id = order_id or str(uuid4())

        with closing(sqlite3.connect(self.database_path, timeout=15)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, payload TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS orders "
                "(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)"
            )
            saved = connection.execute(
                "SELECT fingerprint, result FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if saved:
                if saved[0] != fingerprint:
                    raise ValueError("Order ID already used with different parameters")
                return json.loads(saved[1])
            account = self._load(connection)
            filled = filled_lots * lot_size
            fee = apply_fill(account, ticker, side, filled, price_per_unit, self.commission)

            status = (
                "filled" if filled == quantity else ("partially_filled" if filled else "cancelled")
            )
            result = {
                "order_id": order_id,
                "status": status,
                "ticker": ticker,
                "side": side,
                "quantity": filled,
                "requested_quantity": quantity,
                "unfilled_quantity": quantity - filled,
                "filled_lots": filled_lots,
                "price": float(price_per_unit),
                "commission": float(fee),
                "account": account.snapshot(),
            }
            connection.execute(
                "INSERT INTO account VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET payload = excluded.payload",
                (json.dumps({"cash": str(account.cash), "positions": account.positions}),),
            )
            connection.execute(
                "INSERT INTO orders VALUES (?, ?, ?)", (order_id, fingerprint, json.dumps(result))
            )
        return result

    def snapshot(self) -> dict:
        if not self.database_path.is_file():
            return self._load().snapshot()
        with closing(sqlite3.connect(self.database_path, timeout=15)) as connection:
            return self._load(connection).snapshot()

    def _load(self, connection: sqlite3.Connection | None = None) -> PaperAccount:
        table = (
            connection is not None
            and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'account'"
            ).fetchone()
        )
        row = (
            connection.execute("SELECT payload FROM account WHERE id = 1").fetchone()
            if table
            else None
        )
        if row:
            payload = json.loads(row[0])
        elif self.state_path != self.database_path and self.state_path.is_file():
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            return PaperAccount(self.initial_cash, {})
        return PaperAccount(
            Decimal(str(payload["cash"])), {k: int(v) for k, v in payload["positions"].items()}
        )


def apply_fill(
    account: PaperAccount, ticker: str, side: str, quantity: int, price: Decimal, rate: Decimal
) -> Decimal:
    if not quantity:
        return Decimal(0)
    gross = price * quantity
    fee = gross * rate
    current = account.positions.get(ticker, 0)
    if side == "buy":
        if gross + fee > account.cash:
            raise ValueError("Insufficient paper cash including commission")
        account.cash -= gross + fee
        account.positions[ticker] = current + quantity
    else:
        if quantity > current:
            raise ValueError("Insufficient paper position")
        account.cash += gross - fee
        if current == quantity:
            account.positions.pop(ticker)
        else:
            account.positions[ticker] = current - quantity
    return fee


def _positive_price(value: float) -> Decimal:
    price = Decimal(str(value))
    if not price.is_finite() or price <= 0:
        raise ValueError("A quote must be positive and finite")
    return price
