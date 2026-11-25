from collections.abc import Iterable

import pandas as pd


def maximum_drawdown(values: Iterable[float], starting_value: float) -> float:
    peak = starting_value
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return drawdown


def replay_ranked(
    market: pd.DataFrame,
    signals: pd.DataFrame,
    top_n: int,
    round_trip_bps: float,
) -> tuple[pd.Series, list[float], int]:
    """Track price-based positions daily, with cash carried between completed windows."""
    closes = market["close"].unstack("ticker").sort_index().where(lambda frame: frame.gt(0))
    calendar = closes.index
    leg_cost = round_trip_bps / 20_000
    settled = 1.0
    exit_on = None
    paths = []
    trade_returns = []
    windows = 0

    for signal_on, candidates in signals.groupby("date", sort=True):
        if exit_on is not None and signal_on < exit_on:
            continue
        entry_index = calendar.searchsorted(signal_on, side="right")
        if entry_index == len(calendar):
            break
        entry_on = calendar[entry_index]
        selected = candidates.nlargest(top_n, "probability")
        active = selected[selected["entry_date"].eq(entry_on)]
        if active.empty:
            continue

        exit_on = active["exit_date"].max()
        dates = calendar[(calendar >= entry_on) & (calendar <= exit_on)]
        # An unavailable opening quote leaves its allocation in cash.
        factors = pd.Series(float(len(selected) - len(active)), index=dates)
        for order in active.itertuples(index=False):
            quotes = closes[order.ticker].loc[entry_on : order.exit_date].reindex(dates).ffill()
            # Mark the first bar at the actual entry level if it has no closing quote yet.
            quotes = quotes.fillna(order.entry_price)
            value = (quotes / order.entry_price) / (1 + leg_cost)
            value.loc[order.exit_date :] *= 1 - leg_cost
            factors += value
            trade_returns.append(float(value.iloc[-1]) - 1)
        path = settled * factors / len(selected)
        if not paths:
            paths.append(pd.Series([1.0], index=[signal_on]))
        paths.append(path)
        settled = float(path.iloc[-1])
        windows += 1

    if not paths:
        raise ValueError("Backtest produced no trades")
    nav = pd.concat(paths)
    calendar_slice = calendar[(calendar >= nav.index[0]) & (calendar <= nav.index[-1])]
    return nav.reindex(calendar_slice).ffill(), trade_returns, windows
