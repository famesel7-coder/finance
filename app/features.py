import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "return_5d",
    "return_10d",
    "return_20d",
    "ma_gap_10d",
    "ma_gap_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "rsi_14d",
    "atr_pct_14d",
]


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    return rsi.mask((loss == 0) & (gain == 0), 50.0)


def add_features(
    frame: pd.DataFrame, horizon: int = 10, target_return: float = 0.02
) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError(f"OHLCV data must contain {sorted(required)}")

    parts: list[pd.DataFrame] = []
    for ticker, group in frame.groupby(level="ticker", sort=False):
        data = group.droplevel("ticker").sort_index().copy()
        close = data["close"].astype(float)
        daily = close.pct_change()
        data["return_5d"] = close.pct_change(5)
        data["return_10d"] = close.pct_change(10)
        data["return_20d"] = close.pct_change(20)
        data["ma_gap_10d"] = close / close.rolling(10).mean() - 1
        data["ma_gap_20d"] = close / close.rolling(20).mean() - 1
        data["volatility_20d"] = daily.rolling(20).std() * np.sqrt(252)
        data["volume_ratio_20d"] = data["volume"] / data["volume"].rolling(20).mean()
        data["rsi_14d"] = _rsi(close)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - close.shift()).abs(),
                (data["low"] - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        data["atr_pct_14d"] = true_range.rolling(14).mean() / close
        data["forward_return"] = close.shift(-horizon) / close - 1
        data["target"] = (data["forward_return"] >= target_return).astype(int)
        data["ticker"] = ticker
        parts.append(data.reset_index().set_index(["date", "ticker"]))

    return pd.concat(parts).sort_index()


def training_rows(feature_frame: pd.DataFrame) -> pd.DataFrame:
    return feature_frame.dropna(subset=FEATURE_COLUMNS + ["forward_return"])


def latest_rows(feature_frame: pd.DataFrame) -> pd.DataFrame:
    valid = feature_frame.dropna(subset=FEATURE_COLUMNS)
    if valid.empty:
        raise ValueError("Not enough history to calculate features")
    return valid.groupby(level="ticker", group_keys=False).tail(1)
