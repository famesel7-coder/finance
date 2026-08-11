from app.config import Settings
from app.data import MarketDataProvider
from app.features import add_features, latest_rows, training_rows
from app.model import GrowthModel


class SignalService:
    def __init__(self, provider: MarketDataProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.model = GrowthModel(settings.model_path)

    def train(self, tickers: list[str], period: str) -> dict:
        market = self.provider.history(tickers, period)
        prepared = add_features(
            market, self.settings.forecast_horizon_days, self.settings.target_return
        )
        return self.model.train(training_rows(prepared))

    def scan(self, tickers: list[str], period: str, top_n: int) -> list[dict]:
        market = self.provider.history(tickers, period)
        prepared = add_features(
            market, self.settings.forecast_horizon_days, self.settings.target_return
        )
        latest = latest_rows(prepared).copy()
        latest["probability_up"] = self.model.predict_probability(latest)
        latest["score"] = latest["probability_up"] - latest["volatility_20d"].clip(0, 2) * 0.08
        latest = latest.sort_values("score", ascending=False).head(top_n)
        results = []
        for (_, ticker), row in latest.iterrows():
            factors = []
            if row["return_10d"] > 0:
                factors.append("positive_10d_momentum")
            if row["ma_gap_20d"] > 0:
                factors.append("above_20d_average")
            if row["volume_ratio_20d"] > 1.2:
                factors.append("volume_expansion")
            if row["rsi_14d"] > 70:
                factors.append("overbought_risk")
            results.append(
                {
                    "ticker": ticker,
                    "probability_up": round(float(row["probability_up"]), 4),
                    "score": round(float(row["score"]), 4),
                    "last_price": round(float(row["close"]), 4),
                    "volatility_20d": round(float(row["volatility_20d"]), 4),
                    "factors": factors,
                }
            )
        return results

    def backtest(self, tickers: list[str], period: str, top_n: int) -> dict:
        market = self.provider.history(tickers, period)
        prepared = add_features(
            market, self.settings.forecast_horizon_days, self.settings.target_return
        )
        rows = training_rows(prepared).reset_index().sort_values(["date", "ticker"])
        if len(rows) < 100:
            raise ValueError("Not enough rows for backtest")
        rows["probability"] = self.model.predict_probability(rows)
        rows["rank"] = rows.groupby("date")["probability"].rank(method="first", ascending=False)
        selected = rows[rows["rank"] <= top_n].copy()
        selected["net_return"] = (
            selected["forward_return"] - self.settings.transaction_cost_bps / 10_000
        )
        periodic = selected.groupby("date")["net_return"].mean().dropna()
        if periodic.empty:
            raise ValueError("Backtest produced no trades")
        equity = (1 + periodic).cumprod()
        running_max = equity.cummax()
        drawdown = equity / running_max - 1
        return {
            "observations": len(periodic),
            "total_return": round(float(equity.iloc[-1] - 1), 4),
            "average_trade_return": round(float(selected["net_return"].mean()), 4),
            "win_rate": round(float((selected["net_return"] > 0).mean()), 4),
            "max_drawdown": round(float(drawdown.min()), 4),
            "note": "Overlapping forward-return estimate; use a walk-forward execution simulator before live trading.",
        }
