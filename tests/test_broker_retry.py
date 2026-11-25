import json
from uuid import UUID

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.tinvest import TInvestClient


def test_timeout_checks_existing_order_instead_of_sending_again() -> None:
    methods = []
    request_id = UUID("866fb7e8-88c4-4a2c-9de3-29f4ab9ea440")

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit(".", 1)[-1]
        methods.append(method)
        payload = json.loads(request.content)
        assert payload["orderId"] == str(request_id)
        if method == "OrdersService/PostOrder":
            raise httpx.ReadTimeout("status unknown", request=request)
        assert payload["orderIdType"] == "ORDER_ID_TYPE_REQUEST"
        return httpx.Response(200, json={"executionReportStatus": "EXECUTION_REPORT_STATUS_NEW"})

    settings = Settings(
        tinvest_account_id="fixture-account",
        tinvest_token=SecretStr("fixture"),
        tinvest_sandbox=False,
        allow_live_trading=True,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = TInvestClient(settings, http)
        preview = client.preview_order("TEST", "buy", 1, request_id)
        result = client.place_order(
            "TEST", "buy", 1, "EXECUTE_LIVE_ORDER", UUID(preview["request_id"])
        )
    assert result["request_id"] == str(request_id)
    assert methods == ["OrdersService/PostOrder", "OrdersService/GetOrderState"]
