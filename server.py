"""
AL-SANGMOO QUANT TERMINAL: MODULAR FASTAPI BACKEND SERVER
High-performance, CQRS-compliant trading server mounting modular APIRouters and static frontend.
"""

import os
import sys
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import db_manager
from al_sangmoo.api.hub import hub
from al_sangmoo.interfaces.api.routers import (
    dashboard,
    charts,
    portfolio,
    scanner,
    broker
)
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

# Windows UTF-8 encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

app = FastAPI(
    title="Al-Sangmoo Institutional Quant Platform",
    description="Algorithmic trading workstation, Ichimoku 3-Tier quant matrix, and KIS REST broker gateway.",
    version="3.5"
)

ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "null",
    "file://"
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

@app.on_event("startup")
def startup_event():
    db_manager.init_database()

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the modular Bloomberg dark terminal frontend index.html."""
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX, media_type="text/html")
    elif os.path.exists(LEGACY_DASHBOARD):
        return FileResponse(LEGACY_DASHBOARD, media_type="text/html")
    return HTMLResponse("<h1>R Quant Terminal: Frontend Not Found</h1>", status_code=404)

@app.get("/legacy", response_class=HTMLResponse)
async def serve_legacy_dashboard():
    """Serves the monolithic single-file dashboard for backward compatibility."""
    if os.path.exists(LEGACY_DASHBOARD):
        return FileResponse(LEGACY_DASHBOARD, media_type="text/html")
    return HTMLResponse("<h1>Legacy Dashboard Not Found</h1>", status_code=404)

@app.websocket("/ws")
@app.websocket("/ws/live_feed")
async def websocket_live_hub(websocket: WebSocket):
    """Real-time WebSocket connection to broadcast hub with CSWSH protection."""
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS and not origin.startswith("http://localhost:") and not origin.startswith("http://127.0.0.1:"):
        await websocket.close(code=1008, reason="Forbidden Origin")
        return
        
    connected = await hub.connect(websocket)
    if not connected:
        return
    try:
        p_data = db_manager.get_live_portfolio()
        await websocket.send_json({"type": "connected", "data": {"portfolio": p_data}})
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
