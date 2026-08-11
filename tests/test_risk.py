from app.risk import size_position


def test_position_is_capped_by_notional() -> None:
    plan = size_position(100_000, 100, atr_pct=0.02, max_position_pct=0.10, max_risk_pct=0.02)
    assert plan.quantity == 100
    assert plan.notional == 10_000
    assert plan.stop_price < 100


def test_position_requires_positive_values() -> None:
    try:
        size_position(0, 100, atr_pct=0.02)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")
