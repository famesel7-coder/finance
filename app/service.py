from app.config import Settings
from app.data import MarketDataProvider
from app.features import add_features, latest_rows, training_rows
from app.model import GrowthModel
from app.universe import COMPANY_BY_TICKER


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
                    "company": COMPANY_BY_TICKER.get(ticker).name
                    if ticker in COMPANY_BY_TICKER
                    else ticker,
                    "sector": COMPANY_BY_TICKER.get(ticker).sector
                    if ticker in COMPANY_BY_TICKER
                    else "Unknown",
                    "probability_up": round(float(row["probability_up"]), 4),
                    "score": round(float(row["score"]), 4),
                    "last_price": round(float(row["close"]), 4),
                    "volatility_20d": round(float(row["volatility_20d"]), 4),
                    "factors": factors,
                }
            )
        return results

    def recommend_portfolio(
        self,
        tickers: list[str],
        period: str,
        top_n: int,
        capital: float,
        risk_profile: str,
    ) -> dict:
        signals = self.scan(tickers, period, top_n)
        return self.portfolio_from_signals(signals, capital, risk_profile)

    def portfolio_from_signals(
        self, signals: list[dict], capital: float, risk_profile: str
    ) -> dict:
        invested_share = {"conservative": 0.50, "balanced": 0.75, "aggressive": 0.90}[risk_profile]
        max_weight = {"conservative": 0.12, "balanced": 0.16, "aggressive": 0.20}[risk_profile]
        quality = [
            max(0.01, (item["probability_up"] - 0.45) / max(item["volatility_20d"], 0.10))
            for item in signals
        ]
        total_quality = sum(quality)
        raw_weights = [value / total_quality * invested_share for value in quality]
        weights = _cap_weights(raw_weights, invested_share, max_weight)
        positions = []
        for signal, weight in zip(signals, weights, strict=True):
            amount = capital * weight
            positions.append(
                {
                    **signal,
                    "weight": round(weight, 4),
                    "target_amount": round(amount, 2),
                    "estimated_units": int(amount / signal["last_price"]),
                }
            )
        invested = sum(item["target_amount"] for item in positions)
        return {
            "capital": capital,
            "risk_profile": risk_profile,
            "invested_amount": round(invested, 2),
            "cash_reserve": round(capital - invested, 2),
            "positions": positions,
            "execution_note": "Units are estimates, not exchange lots. Validate lot size with the broker before ordering.",
        }

    def backtest(self, tickers: list[str], period: str, top_n: int) -> dict:
        market = self.provider.history(tickers, period)
        prepared = add_features(
            market, self.settings.forecast_horizon_days, self.settings.target_return
        )
        rows = self.model.out_of_sample_predictions(
            training_rows(prepared), embargo_dates=self.settings.forecast_horizon_days
        )
        dates = rows["date"].drop_duplicates().sort_values().tolist()
        rebalance_dates = set(dates[:: self.settings.forecast_horizon_days])
        rows = rows[rows["date"].isin(rebalance_dates)].copy()
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
        periods_per_year = 252 / self.settings.forecast_horizon_days
        annualized_return = equity.iloc[-1] ** (periods_per_year / len(periodic)) - 1
        periodic_std = periodic.std(ddof=0)
        annualized_volatility = periodic_std * periods_per_year**0.5
        sharpe = periodic.mean() / periodic_std * periods_per_year**0.5 if periodic_std > 0 else 0
        return {
            "observations": len(periodic),
            "total_return": round(float(equity.iloc[-1] - 1), 4),
            "annualized_return": round(float(annualized_return), 4),
            "annualized_volatility": round(float(annualized_volatility), 4),
            "sharpe_ratio": round(float(sharpe), 4),
            "average_trade_return": round(float(selected["net_return"].mean()), 4),
            "win_rate": round(float((selected["net_return"] > 0).mean()), 4),
            "max_drawdown": round(float(drawdown.min()), 4),
            "note": "Chronological out-of-sample estimate with non-overlapping rebalance windows.",
        }


def _cap_weights(weights: list[float], target_total: float, cap: float) -> list[float]:
    result = [min(weight, cap) for weight in weights]
    for _ in range(20):
        shortfall = target_total - sum(result)
        if shortfall <= 1e-9:
            break
        adjustable = [index for index, value in enumerate(result) if value < cap - 1e-9]
        if not adjustable:
            break
        addition = shortfall / len(adjustable)
        for index in adjustable:
            result[index] = min(cap, result[index] + addition)
    return result
