import pandas as pd
import pytest

from app.portfolio_replay import Scenario, compare_scenarios


def test_dividend_cash_is_spent_only_after_payment_and_rebalance_keeps_lots_and_budget():
    dates = pd.date_range("2026-01-12", periods=4, freq="D")
    quotes = pd.DataFrame(
        [
            (100, 100),
            (100, 100),
            (100, 100),
            (100, 100),
            (90, 90),
            (100, 100),
            (90, 90),
            (100, 120),
        ],
        columns=["open", "close"],
        index=pd.MultiIndex.from_product([dates, ["A", "B"]], names=["date", "ticker"]),
    )
    plan = Scenario.model_validate(
        {
            "as_of": "2026-01-12",
            "until": "2026-01-15",
            "positions": {"A": {"shares": 10, "lot": 2}, "B": {"shares": 10, "lot": 1}},
            "dividends": [
                {
                    "ticker": "A",
                    "eligible_on": "2026-01-13",
                    "paid_on": "2026-01-14",
                    "net_per_share": "10",
                }
            ],
            "reinvest_in": ["B"],
            "rebalance": {
                "decided_on": "2026-01-12",
                "sell": ["A"],
                "buy": ["B"],
                "budget_rub": "500",
            },
            "commission_bps": 0,
            "spread_bps": 0,
        }
    )
    benchmark = pd.Series([100, 102, 104, 105], index=dates)
    report = compare_scenarios(plan, quotes, benchmark)
    assert report["mcftr_gross_return_pct"] == 5
    modes = report["scenarios"]
    assert modes["hold"]["net_dividends_rub"] == 100
    assert modes["hold"]["max_drawdown_pct"] == 0
    assert modes["reinvest"]["net_dividends_rub"] == 100
    assert modes["reinvest"]["total_return_pct"] == 11
    assert modes["rebalance"]["rebalance_on"] == "2026-01-13"
    assert modes["rebalance"]["rebalance_sold_rub"] == 400
    assert modes["rebalance"]["net_dividends_rub"] == 60


def test_scenarios_reject_a_missing_starting_quote():
    dates = pd.to_datetime(["2026-01-13", "2026-01-14"])
    quotes = pd.DataFrame(
        {"open": [100, 100, 100, 100], "close": [100, 100, 100, 100]},
        index=pd.MultiIndex.from_product([dates, ["A", "B"]], names=["date", "ticker"]),
    )
    plan = Scenario.model_validate(
        {
            "as_of": "2026-01-12",
            "until": "2026-01-14",
            "positions": {"A": {"shares": 10}, "B": {"shares": 10}},
            "rebalance": {"decided_on": "2026-01-12", "sell": ["A"], "buy": ["B"]},
        }
    )
    with pytest.raises(ValueError, match="No starting close"):
        compare_scenarios(plan, quotes)
