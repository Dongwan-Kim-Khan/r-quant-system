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
from al_sangmoo.infrastructure.brokers.paper_broker import default_broker
from al_sangmoo.infrastructure.backup import create_sqlite_backup, list_backups
from generate_dashboard_feed import compute_all_indicators, build_dashboard_data

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

# In-Memory Fast Cache for Charts (instant responses)
CHART_CACHE = {}

TICKER_REGEX = re.compile(r'^[A-Za-z0-9.\^=-]{1,15}$')

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
        
    # Pre-warm chart cache from dashboard_data.json
    global CHART_CACHE
    if os.path.exists(DASHBOARD_JSON):
        try:
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                feed = json.load(f)
                CHART_CACHE = feed.get("charts", {})
        except Exception:
            pass
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
    
    # Read latest macro & KPI feed from dashboard_data.json
    macro_info = {}
    kpis = {}
    if os.path.exists(DASHBOARD_JSON):
        try:
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                feed = json.load(f)
                macro_info = feed.get("macro", {})
                kpis = feed.get("kpis", {})
        except Exception:
            pass
            
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
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

@app.get("/api/chart/{ticker}")
def get_ticker_chart(ticker: str):
    ticker_upper = ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_upper):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
        
    # 1. Return from memory cache if available for instant load
    if ticker_upper in CHART_CACHE:
        return CHART_CACHE[ticker_upper]
        
    # 2. Otherwise compute live
    data = compute_all_indicators(ticker_upper)
    if not data:
        raise HTTPException(status_code=404, detail="시세 데이터를 불러올 수 없습니다.")
    CHART_CACHE[ticker_upper] = data
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
