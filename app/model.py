from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from app.features import FEATURE_COLUMNS


@dataclass
class TrainingReport:
    rows: int
    positive_rate: float
    validation_accuracy: float
    validation_roc_auc: float | None
    model_path: str


class GrowthModel:
    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.model: RandomForestClassifier | None = None

    def train(self, rows: pd.DataFrame) -> dict:
        ordered = rows.reset_index().sort_values(["date", "ticker"])
        x = ordered[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).dropna()
        y = ordered.loc[x.index, "target"].astype(int)
        if len(x) < 150:
            raise ValueError("At least 150 prepared rows are required for training")
        if y.nunique() < 2:
            raise ValueError("Training target contains only one class")

        splitter = TimeSeriesSplit(n_splits=4)
        train_idx, validation_idx = list(splitter.split(x))[-1]
        validation_model = self._new_model()
        validation_model.fit(x.iloc[train_idx], y.iloc[train_idx])
        prediction = validation_model.predict(x.iloc[validation_idx])
        probability = validation_model.predict_proba(x.iloc[validation_idx])[:, 1]
        accuracy = accuracy_score(y.iloc[validation_idx], prediction)
        auc = None
        if y.iloc[validation_idx].nunique() == 2:
            auc = float(roc_auc_score(y.iloc[validation_idx], probability))

        self.model = self._new_model()
        self.model.fit(x, y)
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "features": FEATURE_COLUMNS}, self.model_path)
        report = TrainingReport(len(x), float(y.mean()), float(accuracy), auc, str(self.model_path))
        return asdict(report)

    def load_if_available(self) -> bool:
        if not self.model_path.exists():
            return False
        payload = joblib.load(self.model_path)
        if payload.get("features") != FEATURE_COLUMNS:
            raise ValueError("Stored model feature schema is incompatible")
        self.model = payload["model"]
        return True

    def predict_probability(self, rows: pd.DataFrame) -> np.ndarray:
        if self.model is None and not self.load_if_available():
            return heuristic_probability(rows)
        return self.model.predict_proba(rows[FEATURE_COLUMNS])[:, 1]

    def out_of_sample_predictions(
        self,
        rows: pd.DataFrame,
        train_fraction: float = 0.70,
        embargo_dates: int = 10,
    ) -> pd.DataFrame:
        ordered = rows.reset_index().sort_values(["date", "ticker"]).copy()
        unique_dates = ordered["date"].drop_duplicates().sort_values().tolist()
        split_index = max(1, min(len(unique_dates) - 1, int(len(unique_dates) * train_fraction)))
        split_date = unique_dates[split_index]
        train_end_index = max(1, split_index - embargo_dates)
        train_end_date = unique_dates[train_end_index]
        train = ordered[ordered["date"] < train_end_date]
        test = ordered[ordered["date"] >= split_date].copy()
        if len(train) < 150 or len(test) < 30:
            raise ValueError("Not enough history for an out-of-sample backtest")
        if train["target"].nunique() < 2:
            raise ValueError("Backtest training target contains only one class")
        model = self._new_model()
        model.fit(train[FEATURE_COLUMNS], train["target"].astype(int))
        test["probability"] = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
        return test

    @staticmethod
    def _new_model() -> RandomForestClassifier:
        return RandomForestClassifier(
            n_estimators=300,
            max_depth=7,
            min_samples_leaf=8,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )


def heuristic_probability(rows: pd.DataFrame) -> np.ndarray:
    momentum = (
        rows["return_5d"].clip(-0.15, 0.15) * 2.0
        + rows["return_10d"].clip(-0.25, 0.25) * 1.5
        + rows["return_20d"].clip(-0.40, 0.40)
        + rows["ma_gap_20d"].clip(-0.20, 0.20)
        + rows["trend_consistency_20d"].clip(-1, 1) * 0.15
        + rows["breakout_60d"].clip(-0.30, 0) * 0.30
    )
    volume = (rows["volume_ratio_20d"].clip(0.5, 3.0) - 1.0) * 0.10
    overbought_penalty = ((rows["rsi_14d"] - 70).clip(lower=0) / 100) * 0.8
    risk_penalty = (
        rows["volatility_20d"].clip(0, 1.5) * 0.12
        + rows["downside_volatility_20d"].clip(0, 1.5) * 0.18
    )
    raw = momentum + volume - overbought_penalty - risk_penalty
    return 1 / (1 + np.exp(-4 * raw.to_numpy()))
