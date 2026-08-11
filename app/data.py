from abc import ABC, abstractmethod

import pandas as pd
import yfinance as yf

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


class MarketDataProvider(ABC):
    @abstractmethod
    def history(self, tickers: list[str], period: str) -> pd.DataFrame:
        """Return a date/ticker indexed OHLCV frame."""


class YahooFinanceProvider(MarketDataProvider):
    def history(self, tickers: list[str], period: str = "2y") -> pd.DataFrame:
        raw = yf.download(
            tickers=tickers,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if raw.empty:
            raise ValueError("Market data provider returned no rows")

        frames: list[pd.DataFrame] = []
        if isinstance(raw.columns, pd.MultiIndex):
            available = set(raw.columns.get_level_values(0))
            for ticker in tickers:
                if ticker not in available:
                    continue
                item = raw[ticker].copy()
                item.columns = [str(c).lower() for c in item.columns]
                item["ticker"] = ticker
                frames.append(item)
        else:
            item = raw.copy()
            item.columns = [str(c).lower() for c in item.columns]
            item["ticker"] = tickers[0]
            frames.append(item)

        if not frames:
            raise ValueError("None of the requested tickers returned usable data")

        result = pd.concat(frames).reset_index()
        date_column = next(c for c in result.columns if str(c).lower() in {"date", "datetime"})
        result = result.rename(columns={date_column: "date"})
        missing = set(REQUIRED_COLUMNS) - set(result.columns)
        if missing:
            raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")
        return result.set_index(["date", "ticker"]).sort_index()[REQUIRED_COLUMNS]
