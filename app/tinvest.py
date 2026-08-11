from uuid import uuid4

import httpx

from app.config import Settings


class TInvestClient:
    base_url = "https://invest-public-api.tbank.ru/rest"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30)

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
            "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxWithdrawLimits"
            if self.settings.tinvest_sandbox
            else "tinkoff.public.invest.api.contract.v1.OperationsService/GetWithdrawLimits"
        )
        return self._post(
            method,
            {"accountId": self._account_id()},
        )

    def preview_order(self, ticker: str, side: str, quantity_lots: int) -> dict:
        return {
            "mode": "sandbox" if self.settings.tinvest_sandbox else "live",
            "instrument_id": f"{ticker}_TQBR",
            "side": side,
            "quantity_lots": quantity_lots,
            "will_execute": False,
            "required_confirmation": "EXECUTE_LIVE_ORDER",
        }

    def place_order(
        self, ticker: str, side: str, quantity_lots: int, confirmation: str | None
    ) -> dict:
        if confirmation != "EXECUTE_LIVE_ORDER":
            raise ValueError("Explicit order confirmation is required")
        if not self.settings.allow_live_trading:
            raise ValueError("ALLOW_LIVE_TRADING is disabled")
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
            "orderId": str(uuid4()),
            "instrumentId": f"{ticker}_TQBR",
            "timeInForce": "TIME_IN_FORCE_DAY",
            "confirmMarginTrade": False,
        }
        method = f"tinkoff.public.invest.api.contract.v1.{service}"
        return self._post(method, payload)

    def _post(self, method: str, payload: dict) -> dict:
        if not self.configured:
            raise ValueError("T-Invest token and account ID are not configured")
        token = self.settings.tinvest_token.get_secret_value()
        response = self.client.post(
            f"{self.base_url}/{method}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()

    def _account_id(self) -> str:
        if not self.settings.tinvest_account_id:
            raise ValueError("T-Invest account ID is not configured")
        return self.settings.tinvest_account_id
