from collections.abc import Iterator
from datetime import datetime
from uuid import UUID, uuid4

import httpx

from app.config import Settings
from app.tls import system_verify


class TInvestClient:
    base_url = "https://invest-public-api.tbank.ru/rest"
    contract = "tinkoff.public.invest.api.contract.v1"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, verify=system_verify())

    @property
    def configured(self) -> bool:
        return bool(self.settings.tinvest_token and self.settings.tinvest_account_id)

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "sandbox": self.settings.tinvest_sandbox,
            "live_trading_enabled": self.settings.allow_live_trading,
            "withdrawal": {
                "automatic": False,
                "manual_required": True,
                "reason": "T-Invest API does not support withdrawal from a brokerage account.",
            },
        }

    def get_withdraw_limits(self) -> dict:
        method = (
            "SandboxService/GetSandboxWithdrawLimits"
            if self.settings.tinvest_sandbox
            else "OperationsService/GetWithdrawLimits"
        )
        return self._post(
            method,
            {"accountId": self._account_id()},
        )

    def list_accounts(self) -> list[dict]:
        method = (
            "SandboxService/GetSandboxAccounts"
            if self.settings.tinvest_sandbox
            else "UsersService/GetAccounts"
        )
        return self._post(method, {"status": "ACCOUNT_STATUS_OPEN"}).get("accounts", [])

    def get_portfolio(self, account_id: str) -> dict:
        method = (
            "SandboxService/GetSandboxPortfolio"
            if self.settings.tinvest_sandbox
            else "OperationsService/GetPortfolio"
        )
        return self._post(method, {"accountId": account_id, "currency": "RUB"})

    def get_instrument(self, identifier: str, id_type: str) -> dict:
        return self._post(
            "InstrumentsService/GetInstrumentBy",
            {"id": identifier, "idType": id_type},
        )["instrument"]

    def iter_operations(
        self, account_id: str, since: datetime, until: datetime
    ) -> Iterator[dict]:
        method = (
            "SandboxService/GetSandboxOperationsByCursor"
            if self.settings.tinvest_sandbox
            else "OperationsService/GetOperationsByCursor"
        )
        payload = {
            "accountId": account_id,
            "from": since.isoformat(),
            "to": until.isoformat(),
            "state": "OPERATION_STATE_EXECUTED",
            "limit": 500,
        }
        cursors = set()
        while True:
            result = self._post(method, payload)
            yield from result.get("items", [])
            if not result.get("hasNext"):
                return
            cursor = result.get("nextCursor")
            if not cursor or cursor in cursors:
                raise ValueError("T-Invest returned an invalid operations cursor")
            cursors.add(cursor)
            payload["cursor"] = cursor

    def preview_order(
        self, ticker: str, side: str, quantity_lots: int, request_id: UUID | None = None
    ) -> dict:
        return {
            "request_id": str(request_id or uuid4()),
            "mode": "sandbox" if self.settings.tinvest_sandbox else "live",
            "instrument_id": f"{ticker}_TQBR",
            "side": side,
            "quantity_lots": quantity_lots,
            "will_execute": False,
            "required_confirmation": "EXECUTE_LIVE_ORDER",
        }

    def place_order(
        self, ticker: str, side: str, quantity_lots: int, confirmation: str | None,
        request_id: UUID | None = None,
    ) -> dict:
        if confirmation != "EXECUTE_LIVE_ORDER":
            raise ValueError("Explicit order confirmation is required")
        if not self.settings.allow_live_trading:
            raise ValueError("ALLOW_LIVE_TRADING is disabled")
        if not self.settings.tinvest_sandbox and request_id is None:
            raise ValueError("A request_id from the order preview is required")
        order_id = str(request_id or uuid4())
        service = (
            "SandboxService/PostSandboxOrder"
            if self.settings.tinvest_sandbox
            else "OrdersService/PostOrder"
        )
        payload = {
            "quantity": str(quantity_lots),
            "direction": "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL",
            "accountId": self._account_id(),
            "orderType": "ORDER_TYPE_MARKET",
            "orderId": order_id,
            "instrumentId": f"{ticker}_TQBR",
            "timeInForce": "TIME_IN_FORCE_DAY",
            "confirmMarginTrade": False,
        }
        try:
            return {**self._post(service, payload), "request_id": order_id}
        except (httpx.TimeoutException, httpx.NetworkError):
            try:
                return self.get_order_state(order_id)
            except httpx.HTTPError:
                return {"request_id": order_id, "status": "unknown"}

    def get_order_state(self, request_id: str) -> dict:
        service = (
            "SandboxService/GetSandboxOrderState"
            if self.settings.tinvest_sandbox
            else "OrdersService/GetOrderState"
        )
        result = self._post(
            service,
            {
                "accountId": self._account_id(),
                "orderId": request_id,
                "orderIdType": "ORDER_ID_TYPE_REQUEST",
            },
        )
        return {**result, "request_id": request_id}

    def _post(self, method: str, payload: dict) -> dict:
        if not self.settings.tinvest_token:
            raise ValueError("T-Invest token and account ID are not configured")
        token = self.settings.tinvest_token.get_secret_value()
        response = self.client.post(
            f"{self.base_url}/{self.contract}.{method}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()

    def _account_id(self) -> str:
        if not self.settings.tinvest_account_id:
            raise ValueError("T-Invest account ID is not configured")
        return self.settings.tinvest_account_id
