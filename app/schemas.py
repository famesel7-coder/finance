from pydantic import BaseModel, Field, field_validator


class UniverseRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=100)
    period: str = "2y"

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(t.strip().upper() for t in value if t.strip()))
        if not cleaned:
            raise ValueError("At least one non-empty ticker is required")
        return cleaned


class ScanRequest(UniverseRequest):
    top_n: int = Field(default=5, ge=1, le=25)


class TrainRequest(UniverseRequest):
    period: str = "5y"


class BacktestRequest(ScanRequest):
    period: str = "5y"


class Signal(BaseModel):
    ticker: str
    probability_up: float
    score: float
    last_price: float
    volatility_20d: float
    factors: list[str]


class PaperOrderRequest(BaseModel):
    ticker: str
    side: str
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        return normalized
