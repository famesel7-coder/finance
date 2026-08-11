from dataclasses import dataclass


@dataclass(frozen=True)
class PositionPlan:
    quantity: int
    notional: float
    stop_price: float
    risk_amount: float


def size_position(
    portfolio_value: float,
    price: float,
    atr_pct: float,
    max_position_pct: float = 0.10,
    max_risk_pct: float = 0.02,
) -> PositionPlan:
    if portfolio_value <= 0 or price <= 0:
        raise ValueError("portfolio_value and price must be positive")
    stop_distance_pct = min(max(max(atr_pct, 0.01) * 2, 0.03), 0.15)
    risk_budget = portfolio_value * max_risk_pct
    by_risk = int(risk_budget / (price * stop_distance_pct))
    by_notional = int((portfolio_value * max_position_pct) / price)
    quantity = max(0, min(by_risk, by_notional))
    notional = quantity * price
    risk_amount = quantity * price * stop_distance_pct
    return PositionPlan(
        quantity,
        round(notional, 2),
        round(price * (1 - stop_distance_pct), 2),
        round(risk_amount, 2),
    )
