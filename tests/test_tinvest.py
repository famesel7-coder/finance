import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.tinvest import TInvestClient


def test_tinvest_status_never_exposes_token() -> None:
    settings = Settings(
        tinvest_token=SecretStr("super-secret"),
        tinvest_account_id="account-1",
        tinvest_sandbox=True,
    )
    status = TInvestClient(settings).status()

    assert status["configured"] is True
    assert "super-secret" not in str(status)
    assert status["withdrawal"]["automatic"] is False


def test_order_execution_is_blocked_by_default() -> None:
    settings = Settings(
        tinvest_token=SecretStr("super-secret"),
        tinvest_account_id="account-1",
        allow_live_trading=False,
    )
    client = TInvestClient(settings)

    with pytest.raises(ValueError, match="ALLOW_LIVE_TRADING"):
        client.place_order("SBER", "buy", 1, "EXECUTE_LIVE_ORDER")


def test_sandbox_withdraw_limits_use_sandbox_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("SandboxService/GetSandboxWithdrawLimits")
        assert request.headers["Authorization"] == "Bearer super-secret"
        return httpx.Response(200, json={"money": []})

    settings = Settings(
        tinvest_token=SecretStr("super-secret"),
        tinvest_account_id="account-1",
        tinvest_sandbox=True,
    )
    transport = httpx.MockTransport(handler)

    assert TInvestClient(settings, httpx.Client(transport=transport)).get_withdraw_limits() == {
        "money": []
    }
