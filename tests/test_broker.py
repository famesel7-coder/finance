from pathlib import Path

from app.broker import PaperBroker


def test_paper_broker_buy_and_sell(tmp_path: Path) -> None:
    broker = PaperBroker(tmp_path / "state.json", initial_cash=1_000)
    broker.submit("TEST", "buy", 5, 100)
    assert broker.snapshot() == {"cash": 500, "positions": {"TEST": 5}}
    broker.submit("TEST", "sell", 2, 110)
    assert broker.snapshot() == {"cash": 720, "positions": {"TEST": 3}}
