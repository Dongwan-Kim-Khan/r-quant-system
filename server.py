"""
AL-SANGMOO QUANT TERMINAL: MODULAR FASTAPI BACKEND SERVER
High-performance, CQRS-compliant trading server mounting modular APIRouters and static frontend.
"""

import os
import sys
import json
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from contextlib import asynccontextmanager
import pandas as pd
import db_manager
from al_sangmoo.api.hub import hub
from al_sangmoo.interfaces.api.routers import (
    dashboard,
    charts,
    portfolio,
    scanner,
    broker,
    guardian,
    autopilot
)
from al_sangmoo.domain.risk.portfolio_guardian import default_guardian
from al_sangmoo.domain.risk.autopilot_trader import default_autopilot

from al_sangmoo.interfaces.api.routers.portfolio import (
    BuyOrder,
    SellOrder,
    buy_stock,
    sell_stock,
    reset_portfolio,
    get_portfolio
)
from al_sangmoo.interfaces.api.routers.scanner import (
    trigger_scan_now,
    search_stock_suggestions,
    get_recommendation_matrix,
    _run_background_scan_pipeline,
    _is_scanning,
    _scan_lock
)
from generate_dashboard_feed import build_dashboard_data
from al_sangmoo.interfaces.api.routers.dashboard import (
    get_dashboard_data,
    load_feed_cache,
    FEED_CACHE,
    LAST_FEED_MTIME
)
from al_sangmoo.interfaces.api.routers.charts import (
    get_chart_data,
    CHART_CACHE
)

# Legacy aliases for backwards compatibility
def get_ticker_chart(ticker: str):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(get_chart_data(ticker)))
            return future.result()
    else:
        return asyncio.run(get_chart_data(ticker))

try:
    from al_sangmoo.backtest.engine import run_backtest_simulation
    get_backtest_report = run_backtest_simulation
except ImportError:
    pass

try:
    from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators
    get_ichimoku_indicators = calculate_ichimoku_indicators
except ImportError:
    pass

try:
    from al_sangmoo.domain.quant.macro import calculate_msi_regime
    get_macro_climate = calculate_msi_regime
except ImportError:
    pass

try:
    from al_sangmoo.domain.quant.scoring import evaluate_quant_score
    get_quant_score = evaluate_quant_score
except ImportError:
    pass

try:
    from al_sangmoo.domain.quant.multi_timeframe import calculate_mtf_consensus
    get_mtf_consensus = calculate_mtf_consensus
except ImportError:
    pass

def get_recommended_position_size(ticker: str = "SPY", equity: float = 100000.0, msi: float = 50.0, **kwargs):
    from al_sangmoo.domain.risk.position_sizer import calculate_dynamic_position_size, calculate_atr
    import yfinance as yf
    try:
        df = yf.download(ticker, period="1mo", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df.columns = df.columns.get_level_values(0)
            elif 'Close' in df.columns.get_level_values(1):
                df.columns = df.columns.get_level_values(1)
        cur_price = float(df['Close'].iloc[-1]) if not df.empty else 100.0
        atr = calculate_atr(df) if not df.empty else (cur_price * 0.02)
    except Exception:
        cur_price = 100.0
        atr = 2.0
    res = calculate_dynamic_position_size(
        portfolio_equity=equity,
        current_price=cur_price,
        atr_14=atr,
        msi_score=msi
    )
    res["ticker"] = ticker
    return res

# Windows UTF-8 encoding fix & Proactor Socket Reset suppression
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _safe_call_connection_lost(self, exc=None):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

        _ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Set custom loop exception handler to silence harmless client disconnections
    try:
        loop = asyncio.get_running_loop()
        orig_handler = loop.get_exception_handler()

        def custom_loop_exception_handler(l, context):
            exc = context.get("exception")
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                return
            msg = str(context.get("message", ""))
            if "WinError 10054" in msg or (exc and "WinError 10054" in str(exc)):
                return
            if orig_handler:
                orig_handler(l, context)
            else:
                l.default_exception_handler(context)

        loop.set_exception_handler(custom_loop_exception_handler)
    except Exception:
        pass

    db_manager.init_database()
    default_guardian.start()
    default_autopilot.start()
    yield
    default_guardian.stop()
    default_autopilot.stop()

app = FastAPI(
    title="Al-Sangmoo Institutional Quant Platform",
    description="Algorithmic trading workstation, Ichimoku 3-Tier quant matrix, and KIS REST broker gateway.",
    version="3.5",
    lifespan=lifespan
)

ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None)
        )
    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()}
        )
    print(f"[ERROR 500] Unhandled exception on {request.method} {request.url.path}: {exc}", file=sys.stderr)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "An internal server error occurred"}
    )

@app.middleware("http")
async def add_security_headers_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        response = await global_exception_handler(request, exc)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")
LEGACY_DASHBOARD = os.path.join(BASE_DIR, "al_sangmoo_dashboard.html")

# Mount Modular Static Assets (/static/css/terminal.css, /static/js/*.js)
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Mount Modular APIRouters
app.include_router(dashboard.router)
app.include_router(charts.router)
app.include_router(portfolio.router)
app.include_router(scanner.router)
app.include_router(broker.router)
app.include_router(guardian.router)
app.include_router(autopilot.router)



@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the modular Bloomberg dark terminal frontend index.html."""
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX, media_type="text/html")
    elif os.path.exists(LEGACY_DASHBOARD):
        return FileResponse(LEGACY_DASHBOARD, media_type="text/html")
    return HTMLResponse("<h1>R Quant Terminal: Frontend Not Found</h1>", status_code=404)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Handles browser favicon requests without 404 log clutter."""
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/legacy")
async def serve_legacy_dashboard():
    """Redirects legacy dashboard permanently to modern modular terminal."""
    return RedirectResponse(url="/", status_code=307)

@app.websocket("/ws")
@app.websocket("/ws/live_feed")
async def websocket_live_hub(websocket: WebSocket):
    """Real-time WebSocket connection to broadcast hub with CSWSH protection."""
    origin = websocket.headers.get("origin")
    if not origin or origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008, reason="Forbidden Origin")
        return
        
    connected = await hub.connect(websocket)
    if not connected:
        return
    try:
        p_data = db_manager.get_live_portfolio()
        await websocket.send_json({"type": "connected", "event": "connected", "data": {"portfolio": p_data}})
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
            else:
                try:
                    data = json.loads(msg)
                    if data.get("type") == "ping" or data.get("event") == "ping":
                        await websocket.send_text("pong")
                except Exception:
                    pass
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False, log_level="info", ws_ping_interval=20.0, ws_ping_timeout=30.0)
