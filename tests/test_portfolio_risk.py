import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.portfolio_journal import SCHEMA
from app.portfolio_risk import Valuation, build_report, performance


def cash(amount):
    return {"currency": "rub", "units": str(amount)}


def test_withdrawal_does_not_create_drawdown():
    start = datetime(2026, 11, 25, 20, 55, tzinfo=UTC)
    next_mark = start + timedelta(days=1)
    valuations = [
        Valuation(start, start, Decimal(1000)),
        Valuation(next_mark, start, Decimal(900)),
        Valuation(next_mark + timedelta(days=1), start, Decimal(810)),
    ]
    event = {"type": "OPERATION_TYPE_OUTPUT", "payment": cash(-100)}
    result = performance(valuations, [(start + timedelta(hours=12), event)])
    assert result["return_pct"] == Decimal(-10)
    assert result["drawdown_pct"] == result["max_drawdown_pct"] == Decimal(-10)


def test_unvalued_security_transfer_breaks_the_series():
    start = datetime(2026, 11, 25, 20, 55, tzinfo=UTC)
    result = performance(
        [
            Valuation(start, start, Decimal(1000)),
            Valuation(start + timedelta(days=1), start, Decimal(500)),
        ],
        [(start + timedelta(hours=1), {"type": "OPERATION_TYPE_OUTPUT_SECURITIES"})],
    )
    assert result["return_pct"] is None
    assert result["drawdown_pct"] is None
    assert result["discontinuities"] == 1


def test_report_uses_primary_account_and_last_moscow_mark(tmp_path):
    database = tmp_path / "journal.sqlite3"
    start = datetime(2026, 11, 25, 8, tzinfo=UTC)
    latest = start + timedelta(hours=10)
    coverage = start - timedelta(days=365)
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        for stamp, nav in ((start, 1000), (latest, 950)):
            payload = {
                "accounts": [
                    {
                        "account": {"id": "demo"},
                        "portfolio": {
                            "totalAmountPortfolio": cash(nav),
                            "positions": [
                                {
                                    "instrumentType": "share",
                                    "instrumentUid": "s",
                                    "quantity": cash(10),
                                    "currentPrice": cash(90),
                                    "averagePositionPriceFifo": cash(100),
                                },
                                {
                                    "instrumentType": "currency",
                                    "figi": "RUB000UTSTOM",
                                    "quantity": cash(50),
                                },
                            ],
                        },
                    },
                    {
                        "account": {"id": "separate"},
                        "portfolio": {
                            "totalAmountPortfolio": cash(2000),
                            "positions": [],
                        },
                    },
                ],
                "instruments": {"s": {"ticker": "TEST", "classCode": "TQBR"}},
            }
            db.execute(
                "INSERT INTO portfolio_snapshots VALUES (?, ?, ?)",
                (
                    stamp.isoformat(timespec="microseconds"),
                    coverage.isoformat(timespec="microseconds"),
                    json.dumps(payload),
                ),
            )
        for key, kind, value in (("div", "DIVIDEND", 100), ("tax", "DIVIDEND_TAX", -13)):
            db.execute(
                "INSERT INTO portfolio_operations VALUES (?, ?, ?, ?)",
                (
                    "demo",
                    key,
                    start.isoformat(timespec="microseconds"),
                    json.dumps({"type": f"OPERATION_TYPE_{kind}", "payment": cash(value)}),
                ),
            )
    report = build_report(database)
    assert report["account_value_rub"] == Decimal(950)
    assert report["net_rub_dividends"] == Decimal(87)
    assert report["unrealized_stock_result_rub"] == Decimal(-100)
    assert report["rub_cash_above_reserve"] == Decimal("2.5")
    assert report["positions_at_weight_limit"] == ["TEST"]
    assert report["returns"]["drawdown_pct"] is None
    assert report["returns"]["observations"] == 1
