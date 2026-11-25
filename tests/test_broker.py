from pathlib import Path

import pytest

from app.broker import PaperBroker


def test_paper_broker_buy_and_sell(tmp_path: Path) -> None:
    broker = PaperBroker(tmp_path / "state.json", initial_cash=1_000, commission_bps=0, spread_bps=0)
    broker.submit("TEST", "buy", 5, 100)
    assert broker.snapshot() == {"cash": 500, "positions": {"TEST": 5}}
    broker.submit("TEST", "sell", 2, 110)
    assert broker.snapshot() == {"cash": 720, "positions": {"TEST": 3}}


def test_partial_fills_keep_cash_after_commission_and_reuse_order_id(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"cash": 1000, "positions": {"OLD": 3}}', encoding="utf-8")
    broker = PaperBroker(path)
    args = {"lot_size": 2, "available_lots": 1, "order_id": "paper-1", "bid": 99, "ask": 101}

    result = broker.submit("TEST", "buy", 4, 100, **args)
    assert result["status"] == "partially_filled"
    assert result["quantity"] == 2
    assert result["commission"] == pytest.approx(0.606)
    assert result["account"]["positions"] == {"OLD": 3, "TEST": 2}
    assert result["account"]["cash"] == pytest.approx(797.394)
    restarted = PaperBroker(path)
    assert restarted.submit("TEST", "buy", 4, 100, **args) == result
    assert restarted.snapshot() == result["account"]
    with pytest.raises(ValueError, match="different parameters"):
        restarted.submit("TEST", "buy", 6, 100, **args)
    with pytest.raises(ValueError, match="lot size"):
        restarted.submit("TEST", "buy", 3, 100, lot_size=2)


def test_paper_order_rejects_purchase_when_commission_exceeds_available_cash(tmp_path: Path) -> None:
    broker = PaperBroker(tmp_path / "state.json", initial_cash=1_000, spread_bps=0)
    with pytest.raises(ValueError, match="including commission"):
        broker.submit("TEST", "buy", 10, 100)
    assert broker.snapshot() == {"cash": 1000, "positions": {}}
