from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forecast_horizon_days: int = Field(default=10, ge=1, le=60)
    target_return: float = Field(default=0.02, ge=-0.5, le=1.0)
    model_path: Path = Path("artifacts/model.joblib")
    max_position_pct: float = Field(default=0.10, gt=0, le=1)
    max_portfolio_risk_pct: float = Field(default=0.02, gt=0, le=0.25)
    transaction_cost_bps: float = Field(default=10.0, ge=0, le=1000)
    allow_live_trading: bool = False
    max_portfolio_positions: int = Field(default=7, ge=1, le=25)
    tinvest_token: SecretStr | None = None
    tinvest_account_id: str | None = None
    tinvest_sandbox: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
