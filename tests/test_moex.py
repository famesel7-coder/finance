from datetime import date

import httpx

from app.moex import MoexIssProvider, _period_days


def test_moex_provider_normalizes_candles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["interval"] == "24"
        return httpx.Response(
            200,
            json={
                "candles": {
                    "columns": ["begin", "open", "high", "low", "close", "volume"],
                    "data": [
                        ["2026-01-10 00:00:00", 100, 105, 98, 103, 1_000_000],
                        ["2026-01-11 00:00:00", 103, 108, 102, 107, 1_200_000],
                    ],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = MoexIssProvider(client)._ticker_history("SBER", date(2025, 1, 1))

    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.names == ["date", "ticker"]
    assert frame.loc[("2026-01-11", "SBER"), "close"] == 107


def test_period_parser() -> None:
    assert _period_days("2y") == 760
    assert _period_days("6mo") == 186
    assert _period_days("30d") == 90
