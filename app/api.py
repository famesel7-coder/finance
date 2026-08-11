from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.broker import PaperBroker
from app.config import get_settings
from app.data import YahooFinanceProvider
from app.moex import MoexIssProvider
from app.schemas import (
    BacktestRequest,
    BrokerOrderRequest,
    PaperOrderRequest,
    PortfolioRequest,
    ScanRequest,
    TrainRequest,
)
from app.service import SignalService
from app.tinvest import TInvestClient
from app.universe import DEFAULT_MOEX_TICKERS, public_universe

settings = get_settings()
paper_broker = PaperBroker()
t_invest = TInvestClient(settings)
moex_provider = MoexIssProvider()
yahoo_provider = YahooFinanceProvider()
static_dir = Path(__file__).parent / "static"

app = FastAPI(
    title="Finance Signal Service",
    version="0.2.0",
    description="MOEX stock ranking, portfolio modelling, backtesting and broker-safe API.",
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _service(market: str) -> SignalService:
    provider = moex_provider if market.lower() == "moex" else yahoo_provider
    return SignalService(provider, settings)


def _tickers(tickers: list[str], market: str) -> list[str]:
    if tickers:
        return tickers
    if market.lower() == "moex":
        return DEFAULT_MOEX_TICKERS
    raise ValueError("Tickers are required for non-MOEX markets")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "market": "moex",
        "live_trading_enabled": settings.allow_live_trading,
    }


@app.get("/universe")
def universe() -> dict:
    return {"market": "moex", "companies": public_universe()}


@app.post("/scan")
def scan(request: ScanRequest) -> dict:
    try:
        tickers = _tickers(request.tickers, request.market)
        return {
            "horizon_days": settings.forecast_horizon_days,
            "signals": _service(request.market).scan(tickers, request.period, request.top_n),
        }
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/train")
def train(request: TrainRequest) -> dict:
    try:
        tickers = _tickers(request.tickers, request.market)
        return _service(request.market).train(tickers, request.period)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/backtest")
def backtest(request: BacktestRequest) -> dict:
    try:
        tickers = _tickers(request.tickers, request.market)
        return _service(request.market).backtest(tickers, request.period, request.top_n)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/portfolio/recommend")
def recommend_portfolio(request: PortfolioRequest) -> dict:
    try:
        tickers = _tickers(request.tickers, request.market)
        top_n = min(request.top_n, settings.max_portfolio_positions)
        return _service(request.market).recommend_portfolio(
            tickers,
            request.period,
            top_n,
            request.capital,
            request.risk_profile,
        )
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/analysis")
def analysis(request: PortfolioRequest) -> dict:
    try:
        tickers = _tickers(request.tickers, request.market)
        top_n = min(request.top_n, settings.max_portfolio_positions)
        service = _service(request.market)
        signals = service.scan(tickers, request.period, top_n)
        portfolio = service.portfolio_from_signals(signals, request.capital, request.risk_profile)
        return {
            "horizon_days": settings.forecast_horizon_days,
            "signals": signals,
            "portfolio": portfolio,
        }
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
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


@app.get("/broker/status")
def broker_status() -> dict:
    return t_invest.status()


@app.get("/broker/withdraw-limits")
def broker_withdraw_limits() -> dict:
    try:
        return {
            "limits": t_invest.get_withdraw_limits(),
            "automatic_withdrawal": False,
            "manual_required": True,
        }
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/broker/orders/preview")
def broker_order_preview(request: BrokerOrderRequest) -> dict:
    return t_invest.preview_order(request.ticker, request.side, request.quantity_lots)


@app.post("/broker/orders/execute")
def broker_order_execute(request: BrokerOrderRequest) -> dict:
    try:
        return t_invest.place_order(
            request.ticker, request.side, request.quantity_lots, request.confirm
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
