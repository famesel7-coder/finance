import pandas as pd
import pytest

from app.backtest import maximum_drawdown, replay_ranked


def test_replay_marks_interim_drawdown_before_a_profitable_exit():
    dates = pd.date_range("2026-01-12", periods=4, freq="D")
    market = pd.DataFrame(
        {"close": [100, 90, 96, 101]},
        index=pd.MultiIndex.from_product([dates, ["TEST"]], names=["date", "ticker"]),
    )
    signals = pd.DataFrame(
        [
            {
                "date": dates[0],
                "ticker": "TEST",
                "probability": 0.7,
                "entry_date": dates[1],
                "entry_price": 100,
                "exit_date": dates[3],
            }
        ]
    )

    nav, trades, count = replay_ranked(market, signals, 1, 60)
    factor = 1 / 1.003
    assert list(nav) == pytest.approx([1.0, 0.90 * factor, 0.96 * factor, 1.01 * factor * 0.997])
    assert trades == pytest.approx([1.01 * factor * 0.997 - 1])
    assert count == 1
    assert maximum_drawdown(nav, 1.0) == pytest.approx(0.90 * factor - 1)
    assert maximum_drawdown([0.90, 0.96, 1.01], 1.0) == pytest.approx(-0.10)
