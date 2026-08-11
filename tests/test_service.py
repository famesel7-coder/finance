import pytest

from app.service import _cap_weights


def test_cap_weights_respects_position_limit() -> None:
    weights = _cap_weights([0.5, 0.2, 0.1, 0.1, 0.1], target_total=0.75, cap=0.16)

    assert sum(weights) == pytest.approx(0.75)
    assert max(weights) <= 0.16


def test_cap_weights_keeps_unallocatable_cash() -> None:
    weights = _cap_weights([0.7, 0.3], target_total=0.75, cap=0.20)

    assert sum(weights) == pytest.approx(0.40)
