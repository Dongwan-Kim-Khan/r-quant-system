import os
import sys
import re
import json
import sqlite3
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import pandas as pd
import yfinance as yf
from datetime import datetime

import db_manager
from al_sangmoo.api.hub import hub
from al_sangmoo.backtest.engine import run_backtest_simulation
from al_sangmoo.domain.quant.multi_timeframe import calculate_mtf_consensus
from al_sangmoo.domain.risk.position_sizer import calculate_dynamic_position_size, calculate_atr
from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail
from al_sangmoo.domain.risk.macro_guardrail import evaluate_macro_circuit_breaker
from al_sangmoo.domain.quant.ticker_resolver import resolve_ticker, search_ticker_suggestions
from al_sangmoo.infrastructure.brokers.paper_broker import default_broker
from al_sangmoo.infrastructure.backup import create_sqlite_backup, list_backups
from generate_dashboard_feed import compute_all_indicators, build_dashboard_data, atomic_save_json

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

app = FastAPI(title="Al-Sangmoo Quant Portfolio Backend", version="2.6")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_HTML = os.path.join(BASE_DIR, "al_sangmoo_dashboard.html")
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
CHARTS_DIR = os.path.join(BASE_DIR, "data", "charts")

# In-Memory Fast Caches for Sub-Millisecond Instant Responses
CHART_CACHE = {}
FEED_CACHE = {}
LAST_FEED_MTIME = 0

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')

def load_feed_cache():
    global FEED_CACHE, LAST_FEED_MTIME
    if os.path.exists(DASHBOARD_JSON):
        try:
            mtime = os.path.getmtime(DASHBOARD_JSON)
            if mtime != LAST_FEED_MTIME or not FEED_CACHE:
                with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                    FEED_CACHE = json.load(f)
                LAST_FEED_MTIME = mtime
        except Exception:
            pass

class BuyOrder(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    buy_price: float = Field(..., gt=0)
    quantity: float = Field(..., gt=0)
    buy_date: str = None

class SellOrder(BaseModel):
    sell_price: float = Field(..., gt=0)
    sell_date: str = None
    reason: str = "MANUAL_SELL"

@app.on_event("startup")
def startup_event():
    db_manager.init_db()
    
    # Load initial matrix if empty
    matrix = db_manager.get_recommendations_matrix()
    if not matrix:
        db_manager.save_recommendation_matrix_record(
            "2026-08-18",
            [{"ticker": "NVDA", "close": 225.16}, {"ticker": "AMZN", "close": 262.65}],
            [{"ticker": "AVGO", "close": 392.99}, {"ticker": "COST", "close": 948.10}],
            [{"ticker": "META", "close": 568.97}, {"ticker": "TSLA", "close": 342.27}]
        )
        
    # Pre-warm lightweight feed cache (<150KB)
    load_feed_cache()
    print("Al-Sangmoo Quant Portfolio Server Ready on http://0.0.0.0:8000 (Local & LAN Access Available)")

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_HTML):
        with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
            content = f.read()
            return HTMLResponse(content=content, status_code=200, media_type="text/html; charset=utf-8")
    return HTMLResponse(content="<h1>Dashboard HTML Not Found</h1>", status_code=404)

@app.get("/api/dashboard")
def get_dashboard_summary():
    portfolio = db_manager.get_live_portfolio()
    matrix = db_manager.get_recommendations_matrix()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Ensure fresh feed cache
    load_feed_cache()
    
    macro_info = FEED_CACHE.get("macro", {})
    kpis = dict(FEED_CACHE.get("kpis", {}))
    primary_accumulation = FEED_CACHE.get("primary_accumulation", [])
    sniper_radar = FEED_CACHE.get("sniper_radar", [])
    signal_tracker = FEED_CACHE.get("signal_tracker", [])
    chart_intelligence = FEED_CACHE.get("chart_intelligence", {})
            
    # Always stamp live current server time & active holdings
    kpis["last_updated"] = now_str
    kpis["active_positions"] = len(portfolio.get("holdings", []))
            
    # Ensure holdings is always a list
    if not isinstance(portfolio.get("holdings"), list):
        portfolio["holdings"] = [portfolio["holdings"]] if portfolio.get("holdings") else []
        
    # Ensure matrix is always a list
    if not isinstance(matrix, list):
        matrix = [matrix] if matrix else []
        
    return {
        "macro": macro_info,
        "kpis": kpis,
        "portfolio": portfolio,
        "matrix": matrix,
        "primary_accumulation": primary_accumulation,
        "sniper_radar": sniper_radar,
        "signal_tracker": signal_tracker,
        "chart_intelligence": chart_intelligence,
        "last_updated": now_str
    }

@app.websocket("/ws/live_feed")
async def websocket_live_feed(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        p_data = db_manager.get_live_portfolio()
        await websocket.send_json({"event": "connected", "data": {"portfolio": p_data}})
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)

@app.get("/api/portfolio")
def get_portfolio():
    return db_manager.get_live_portfolio()

@app.post("/api/portfolio/buy")
async def buy_stock(order: BuyOrder):
    ticker_clean = order.ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_clean):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
        
    inserted_id = db_manager.add_portfolio_buy(
        ticker=ticker_clean,
        buy_price=order.buy_price,
        quantity=order.quantity,
        buy_date=order.buy_date
    )
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "id": inserted_id, "message": f"{ticker_clean} {order.quantity}주 매수 등록 완료!"}

@app.post("/api/portfolio/sell/{position_id}")
async def sell_stock(position_id: int, order: SellOrder):
    if position_id <= 0:
        raise HTTPException(status_code=400, detail="유효하지 않은 포지션 ID입니다.")
        
    success = db_manager.record_portfolio_sell(
        holding_id=position_id,
        sell_price=order.sell_price,
        sell_date=order.sell_date,
        reason=order.reason
    )
    if not success:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "message": f"포지션 #{position_id} 매도 완료 처리되었습니다."}

@app.post("/api/portfolio/reset")
async def reset_portfolio():
    db_manager.reset_all_holdings()
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return {"status": "success", "message": "포트폴리오 계좌가 성공적으로 초기화(비우기)되었습니다."}

@app.get("/api/recommendations/matrix")
def get_recommendation_matrix():
    matrix = db_manager.get_recommendations_matrix()
    if not isinstance(matrix, list):
        matrix = [matrix] if matrix else []
    return matrix

@app.get("/api/search")
def search_stock_suggestions(q: str = ""):
    suggestions = search_ticker_suggestions(q, limit=8)
    return {"query": q, "suggestions": suggestions}

@app.get("/api/chart/{ticker}")
def get_ticker_chart(ticker: str):
    ticker_resolved = resolve_ticker(ticker)
    if not TICKER_REGEX.match(ticker_resolved):
        raise HTTPException(status_code=400, detail=f"유효하지 않은 티커 심볼 형식입니다: '{ticker}'")
        
    # 1. Return from in-memory fast cache (instant sub-millisecond)
    if ticker_resolved in CHART_CACHE:
        return CHART_CACHE[ticker_resolved]
        
    # 2. Check modular individual chart file cache (data/charts/{ticker_resolved}.json)
    chart_file = os.path.join(CHARTS_DIR, f"{ticker_resolved}.json")
    if os.path.exists(chart_file):
        try:
            with open(chart_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                CHART_CACHE[ticker_resolved] = data
                return data
        except Exception:
            pass
        
    # 3. Live On-Demand Computation via Real-Time Market API
    data = compute_all_indicators(ticker_resolved)
    if not data:
        # Fallback to uppercase raw ticker
        raw_upper = ticker.strip().upper()
        if raw_upper != ticker_resolved:
            data = compute_all_indicators(raw_upper)
            if data:
                ticker_resolved = raw_upper
                
    if not data:
        raise HTTPException(status_code=404, detail=f"'{ticker}' ({ticker_resolved}) 종목의 시세 데이터를 불러올 수 없습니다.")
        
    # Cache individual chart file for subsequent instant loads
    try:
        os.makedirs(CHARTS_DIR, exist_ok=True)
        atomic_save_json(os.path.join(CHARTS_DIR, f"{ticker_resolved}.json"), data)
    except Exception:
        pass
        
    # Manage LRU cache size (max 150 items)
    if len(CHART_CACHE) > 150:
        first_key = next(iter(CHART_CACHE))
        del CHART_CACHE[first_key]
        
    CHART_CACHE[ticker_resolved] = data
    return data

@app.post("/api/scan_now")
async def trigger_scan_now():
    try:
        import al_sangmoo_daily_bot
        bull_picks, neutral_picks, bear_picks, macro_climate = al_sangmoo_daily_bot.scan_and_select_2x2x2()
        today_str = datetime.now().strftime("%Y-%m-%d")
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        data = build_dashboard_data()
        global CHART_CACHE
        CHART_CACHE = data.get("charts", {})
        await hub.broadcast("live_feed_update", data)
        return {
            "status": "success",
            "message": f"{today_str} 실시간 3-Gate 스캔 & 대시보드 갱신 완료!",
            "macro_stance": macro_climate.get("macro_stance") if macro_climate else "NORMAL"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/backtest/{ticker}")
def get_backtest_report(ticker: str, period: str = "2y"):
    ticker_upper = ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_upper):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
    try:
        report = run_backtest_simulation(ticker_upper, period=period)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"백테스트 실행 실패: {str(e)}")

@app.get("/api/quant/mtf/{ticker}")
def get_mtf_consensus(ticker: str):
    ticker_upper = ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_upper):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
    try:
        mtf_data = calculate_mtf_consensus(ticker_upper)
        return mtf_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MTF 분석 실패: {str(e)}")

@app.get("/api/risk/size/{ticker}")
def get_recommended_position_size(ticker: str, equity: float = 100000.0, msi: float = 50.0):
    ticker_upper = ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_upper):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
    try:
        df = yf.download(ticker_upper, period="3mo", interval="1d", progress=False)
        if df.empty:
            raise HTTPException(status_code=404, detail="종목 데이터를 찾을 수 없습니다.")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cur_price = float(df['Close'].iloc[-1])
        atr_14 = calculate_atr(df, window=14)
        sizing = calculate_dynamic_position_size(portfolio_equity=equity, current_price=cur_price, atr_14=atr_14, msi_score=msi)
        sizing["ticker"] = ticker_upper
        return sizing
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"포지션 사이징 계산 실패: {str(e)}")

@app.get("/api/risk/circuit_breaker")
def get_circuit_breaker_status(msi: float = None):
    portfolio = db_manager.get_live_portfolio()
    holdings = portfolio.get("holdings", [])
    
    # Read MSI from stream cache if not provided
    if msi is None:
        msi_val = 50.0
        if os.path.exists(DASHBOARD_JSON):
            try:
                with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    msi_val = float(d.get("macro", {}).get("macro_climate", {}).get("msi_score", 50.0))
            except Exception:
                pass
        msi = msi_val
        
    evaluation = evaluate_macro_circuit_breaker(msi_score=msi, holdings=holdings)
    return evaluation

@app.post("/api/broker/order")
async def execute_broker_order(order: BuyOrder):
    portfolio = db_manager.get_live_portfolio()
    balance = default_broker.get_account_balance()
    holdings = portfolio.get("holdings", [])
    
    # Evaluate pre-trade guardrails
    validation = validate_pre_trade_guardrail(
        ticker=order.ticker,
        price=order.buy_price,
        quantity=order.quantity,
        total_equity=balance["total_equity"],
        active_holdings=holdings
    )
    if not validation["allowed"]:
        raise HTTPException(status_code=400, detail=validation["reason"])
        
    execution = default_broker.submit_buy_order(
        ticker=order.ticker,
        price=order.buy_price,
        quantity=order.quantity
    )
    
    p_data = db_manager.get_live_portfolio()
    await hub.broadcast("portfolio_update", p_data)
    return execution

@app.post("/api/backup/snapshot")
def trigger_database_backup():
    try:
        res = create_sqlite_backup()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/backup/list")
def get_backup_list():
    return {"backups": list_backups()}

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
