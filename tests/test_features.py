import numpy as np
import pandas as pd

from app.features import FEATURE_COLUMNS, add_features, latest_rows, training_rows


def sample_market(rows: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = pd.Series(100 * np.cumprod(np.repeat(1.002, rows)), index=dates)
    frame = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.linspace(1_000_000, 2_000_000, rows),
            "ticker": "TEST",
        }
    )
    frame.index.name = "date"
    return frame.reset_index().set_index(["date", "ticker"])


def test_features_have_expected_columns() -> None:
    result = add_features(sample_market(), horizon=10, target_return=0.01)
    assert set(FEATURE_COLUMNS).issubset(result.columns)
    assert len(training_rows(result)) > 40
    assert len(latest_rows(result)) == 1
