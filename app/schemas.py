from pydantic import BaseModel, Field, field_validator


class UniverseRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list, max_length=100)
    period: str = "2y"
    market: str = "moex"

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(t.strip().upper() for t in value if t.strip()))
        return cleaned


class ScanRequest(UniverseRequest):
    top_n: int = Field(default=5, ge=1, le=25)


class TrainRequest(UniverseRequest):
    period: str = "5y"


class BacktestRequest(ScanRequest):
    period: str = "5y"


class PortfolioRequest(ScanRequest):
    capital: float = Field(gt=0, le=1_000_000_000)
    risk_profile: str = "balanced"

    @field_validator("risk_profile")
    @classmethod
    def validate_risk_profile(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"conservative", "balanced", "aggressive"}:
            raise ValueError("risk_profile must be conservative, balanced or aggressive")
        return normalized


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


class BrokerOrderRequest(BaseModel):
    ticker: str
    side: str
    quantity_lots: int = Field(gt=0, le=100_000)
    confirm: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_broker_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side")
    @classmethod
    def validate_broker_side(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        return normalized
