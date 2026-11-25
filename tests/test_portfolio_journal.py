import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.portfolio_journal import summarize_journal, sync_journal
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
                    return httpx.Response(200, json={"items": [
                        operation("t", "DIVIDEND_TAX", "-13", -20_000_000),
                        operation("f", "BROKER_FEE", "0", -50_000_000),
                        operation("u", "DIVIDEND", "1", 20_000_000, currency="usd"),
                    ]})
                return httpx.Response(200, json={
                    "items": [operation("d", "DIVIDEND", "100", 100_000_000)],
                    "hasNext": True,
                    "nextCursor": "second-page",
                })
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
