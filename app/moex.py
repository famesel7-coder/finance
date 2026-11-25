from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.data import REQUIRED_COLUMNS, MarketDataProvider
from app.tls import system_verify

MOSCOW = ZoneInfo("Europe/Moscow")


class MoexIssProvider(MarketDataProvider):
    base_url = "https://iss.moex.com/iss"

    def __init__(
        self, client: httpx.Client | None = None, cache_ttl_seconds: int = 300,
        *, ca_file: Path | None = None,
    ):
        self.client = client or httpx.Client(
            timeout=30,
            verify=system_verify(ca_file),
            headers={"User-Agent": "finance-signal-service/0.2"},
        )
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple[tuple[str, ...], str, date], tuple[float, pd.DataFrame]] = {}
        self._cache_lock = Lock()

    def history(self, tickers: list[str], period: str = "2y") -> pd.DataFrame:
        end_date = datetime.now(MOSCOW).date() - timedelta(days=1)
        key = (tuple(tickers), period, end_date)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and monotonic() - cached[0] < self.cache_ttl_seconds:
                return cached[1].copy()
        start_date = end_date - timedelta(days=_period_days(period))
        workers = min(8, max(1, len(tickers)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            frames = list(
                executor.map(lambda ticker: self._ticker_history(ticker, start_date, end_date), tickers)
            )
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            raise ValueError("MOEX ISS returned no usable candles")
        result = pd.concat(frames).sort_index()
        normalized = result[REQUIRED_COLUMNS]
        with self._cache_lock:
            self._cache[key] = (monotonic(), normalized.copy())
        return normalized

    def index_close(self, begin: date, end: date, ticker: str = "MCFTR") -> pd.Series:
        end = min(end, datetime.now(MOSCOW).date() - timedelta(days=1))
        if begin > end:
            raise ValueError("Index history requires completed sessions")
        offset = 0
        rows = []
        columns = []
        while True:
            response = self.client.get(
                f"{self.base_url}/history/engines/stock/markets/index/securities/{ticker}.json",
                params={
                    "from": begin.isoformat(), "till": end.isoformat(), "start": offset,
                    "iss.meta": "off", "history.columns": "TRADEDATE,CLOSE",
                },
            )
            response.raise_for_status()
            reply = response.json()
            block = reply.get("history", {})
            batch = block.get("data", [])
            if not batch:
                break
            rows.extend(batch)
            columns = block["columns"]
            offset += len(batch)
            cursor = reply.get("history.cursor", {}).get("data", [])
            if cursor and offset >= cursor[0][1]:
                break
        if not rows:
            raise ValueError(f"MOEX ISS returned no index closes for {ticker}")
        history = pd.DataFrame(rows, columns=columns)
        history["TRADEDATE"] = pd.to_datetime(history["TRADEDATE"])
        prices = history.drop_duplicates("TRADEDATE", keep="last").set_index("TRADEDATE")["CLOSE"]
        return pd.to_numeric(prices, errors="coerce").dropna().sort_index()

    def _ticker_history(
        self, ticker: str, start_date: date, end_date: date | None = None
    ) -> pd.DataFrame:
        rows: list[list] = []
        columns: list[str] = []
        offset = 0
        while True:
            response = self.client.get(
                f"{self.base_url}/engines/stock/markets/shares/securities/{ticker}/candles.json",
                params={
                    "from": start_date.isoformat(),
                    **({"till": end_date.isoformat()} if end_date else {}),
                    "interval": 24,
                    "start": offset,
                    "iss.meta": "off",
                    "candles.columns": "begin,open,high,low,close,volume",
                },
            )
            response.raise_for_status()
            block = response.json().get("candles", {})
            batch = block.get("data", [])
            columns = block.get("columns", columns)
            if not batch:
                break
            rows.extend(batch)
            offset += len(batch)
            if len(batch) < 500:
                break
        if not rows:
            return pd.DataFrame(columns=REQUIRED_COLUMNS)
        frame = pd.DataFrame(rows, columns=columns)
        frame["date"] = pd.to_datetime(frame["begin"]).dt.tz_localize(None)
        if end_date:
            frame = frame[frame["date"].dt.date <= end_date].copy()
        frame["ticker"] = ticker
        for column in REQUIRED_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        normalized = (frame.dropna(subset=REQUIRED_COLUMNS)
                      .drop_duplicates(subset=["date", "ticker"], keep="last")
                      .set_index(["date", "ticker"]))
        return normalized[REQUIRED_COLUMNS]


def _period_days(period: str) -> int:
    normalized = period.strip().lower()
    if normalized.endswith("y"):
        return max(365, int(normalized[:-1]) * 365 + 30)
    if normalized.endswith("mo"):
        return max(90, int(normalized[:-2]) * 31)
    if normalized.endswith("d"):
        return max(90, int(normalized[:-1]))
    raise ValueError("period must look like 6mo, 2y or 365d")
