import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class PaperAccount:
    cash: float
    positions: dict[str, int]


class PaperBroker:
    def __init__(
        self, state_path: Path = Path("paper_state.json"), initial_cash: float = 100_000.0
    ):
        self.state_path = state_path
        self.account = self._load(initial_cash)

    def submit(self, ticker: str, side: str, quantity: int, price: float) -> dict:
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        ticker = ticker.upper()
        cost = quantity * price
        current = self.account.positions.get(ticker, 0)
        if side == "buy":
            if cost > self.account.cash:
                raise ValueError("Insufficient paper cash")
            self.account.cash -= cost
            self.account.positions[ticker] = current + quantity
        elif side == "sell":
            if quantity > current:
                raise ValueError("Insufficient paper position")
            self.account.cash += cost
            remaining = current - quantity
            if remaining:
                self.account.positions[ticker] = remaining
            else:
                self.account.positions.pop(ticker, None)
        else:
            raise ValueError("side must be buy or sell")
        self._save()
        return {
            "status": "filled",
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "account": asdict(self.account),
        }

    def snapshot(self) -> dict:
        return asdict(self.account)

    def _load(self, initial_cash: float) -> PaperAccount:
        if not self.state_path.exists():
            return PaperAccount(initial_cash, {})
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return PaperAccount(
            float(payload["cash"]), {k: int(v) for k, v in payload["positions"].items()}
        )

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(asdict(self.account), indent=2), encoding="utf-8")
