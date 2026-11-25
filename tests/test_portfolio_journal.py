import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.portfolio_journal import SCHEMA, daily_valuations, summarize_journal, sync_journal
from app.tinvest import TInvestClient


def test_sync_preserves_money_and_is_idempotent_across_pages(tmp_path):
    end = datetime(2026, 11, 25, tzinfo=UTC)
    start = end - timedelta(days=365)
    calls = []

    def operation(identifier, kind, units, nano, currency="rub"):
        return {
            "id": identifier,
            "type": f"OPERATION_TYPE_{kind}",
            "date": "2026-10-01T09:00:00Z",
            "payment": {"currency": currency, "units": units, "nano": nano},
        }

    def handle(request):
        method = request.url.path.rsplit(".", 1)[-1]
        payload = json.loads(request.content)
        calls.append(method)
        assert request.headers["Authorization"] == "Bearer sample"
        match method:
            case "UsersService/GetAccounts":
                return httpx.Response(200, json={"accounts": [{"id": "test-account"}]})
            case "OperationsService/GetPortfolio":
                return httpx.Response(
                    200, json={"positions": [{"instrumentType": "share", "instrumentUid": "uid"}]}
                )
            case "InstrumentsService/GetInstrumentBy":
                return httpx.Response(200, json={"instrument": {"ticker": "TEST"}})
            case "OperationsService/GetOperationsByCursor":
                assert payload["state"] == "OPERATION_STATE_EXECUTED"
                if payload.get("cursor") == "second-page":
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                operation("t", "DIVIDEND_TAX", "-13", -20_000_000),
                                operation("f", "BROKER_FEE", "0", -50_000_000),
                                operation("u", "DIVIDEND", "1", 20_000_000, currency="usd"),
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "items": [operation("d", "DIVIDEND", "100", 100_000_000)],
                        "hasNext": True,
                        "nextCursor": "second-page",
                    },
                )
            case _:
                pytest.fail(f"Unexpected broker request: {method}")

    settings = Settings(_env_file=None, tinvest_sandbox=False, tinvest_token=SecretStr("sample"))
    database = tmp_path / "local.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        client = TInvestClient(settings, transport)
        assert sync_journal(client, database, start, end)["operations_received"] == 4
        sync_journal(client, database, start, end)

    totals = summarize_journal(database, start)["cash_flow_by_currency"]
    assert totals["RUB"] == {
        "dividends": Decimal("100.10"),
        "dividend_tax": Decimal("-13.02"),
        "dividends_after_recorded_tax": Decimal("87.08"),
        "broker_fees": Decimal("-0.05"),
    }
    assert totals["USD"]["dividends"] == Decimal("1.02")
    assert calls.count("OperationsService/GetOperationsByCursor") == 4
    assert database.stat().st_mode & 0o777 == 0o600


def test_iter_operations_rejects_repeated_cursor():
    settings = Settings(_env_file=None, tinvest_sandbox=False, tinvest_token=SecretStr("sample"))
    response = {"items": [], "hasNext": True, "nextCursor": "repeated"}
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
    end = datetime(2026, 11, 25, tzinfo=UTC)
    with httpx.Client(transport=transport) as http_client:
        client = TInvestClient(settings, http_client)
        with pytest.raises(ValueError, match="invalid operations cursor"):
            list(client.iter_operations("test-account", end - timedelta(days=7), end))


def test_daily_valuations_keep_last_snapshot_by_moscow_date(tmp_path):
    database = tmp_path / "journal.sqlite3"
    captures = (
        ("2026-11-24T20:30:00.000000+00:00", "950"),
        ("2026-11-24T21:30:00.000000+00:00", "1000"),
        ("2026-11-25T17:00:00.000000+00:00", "1010"),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        for stamp, amount in captures:
            snapshot = {
                "accounts": [
                    {
                        "portfolio": {
                            "totalAmountPortfolio": {
                                "currency": "rub",
                                "units": amount,
                                "nano": 500_000_000,
                            }
                        }
                    }
                ],
            }
            connection.execute(
                "INSERT INTO portfolio_snapshots VALUES (?, ?, ?)",
                (stamp, captures[0][0], json.dumps(snapshot)),
            )
    values = daily_valuations(database, datetime(2026, 11, 24, tzinfo=UTC))["daily_valuations"]
    assert values["2026-11-24"]["total_rub"] == Decimal("950.5")
    assert values["2026-11-25"]["total_rub"] == Decimal("1010.5")
