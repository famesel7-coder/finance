from fastapi import FastAPI, HTTPException

from app.broker import PaperBroker
from app.config import get_settings
from app.data import YahooFinanceProvider
from app.schemas import BacktestRequest, PaperOrderRequest, ScanRequest, TrainRequest
from app.service import SignalService

settings = get_settings()
service = SignalService(YahooFinanceProvider(), settings)
paper_broker = PaperBroker()

app = FastAPI(
    title="Finance Signal Service",
    version="0.1.0",
    description="Short-horizon stock ranking, backtesting and paper trading API.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "live_trading_enabled": settings.allow_live_trading}


@app.post("/scan")
def scan(request: ScanRequest) -> dict:
    try:
        return {
            "horizon_days": settings.forecast_horizon_days,
            "signals": service.scan(request.tickers, request.period, request.top_n),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/train")
def train(request: TrainRequest) -> dict:
    try:
        return service.train(request.tickers, request.period)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/backtest")
def backtest(request: BacktestRequest) -> dict:
    try:
        return service.backtest(request.tickers, request.period, request.top_n)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/paper/account")
def paper_account() -> dict:
    return paper_broker.snapshot()


@app.post("/paper/orders")
def paper_order(request: PaperOrderRequest) -> dict:
    try:
        return paper_broker.submit(request.ticker, request.side, request.quantity, request.price)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
