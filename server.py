import os
import sys
import re
import json
import sqlite3
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import pandas as pd
import yfinance as yf
from datetime import datetime

import db_manager
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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

@app.get("/api/portfolio")
def get_portfolio():
    return db_manager.get_live_portfolio()

@app.post("/api/portfolio/buy")
def buy_stock(order: BuyOrder):
    ticker_clean = order.ticker.strip().upper()
    if not TICKER_REGEX.match(ticker_clean):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커 심볼 형식입니다.")
        
    inserted_id = db_manager.add_portfolio_buy(
        ticker=ticker_clean,
        buy_price=order.buy_price,
        quantity=order.quantity,
        buy_date=order.buy_date
    )
    return {"status": "success", "id": inserted_id, "message": f"{ticker_clean} {order.quantity}주 매수 등록 완료!"}

@app.post("/api/portfolio/sell/{position_id}")
def sell_stock(position_id: int, order: SellOrder):
    if position_id <= 0:
        raise HTTPException(status_code=400, detail="유효하지 않은 포지션 ID입니다.")
        
    success = db_manager.close_portfolio_position(
        position_id=position_id,
        sell_price=order.sell_price,
        sell_date=order.sell_date,
        reason=order.reason
    )
    if not success:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
    return {"status": "success", "message": f"포지션 #{position_id} 매도 완료 처리되었습니다."}

@app.post("/api/portfolio/reset")
def reset_portfolio():
    db_manager.clear_portfolio()
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
def trigger_scan_now():
    try:
        import al_sangmoo_daily_bot
        bull_picks, neutral_picks, bear_picks = al_sangmoo_daily_bot.scan_and_select_2x2x2()
        today_str = datetime.now().strftime("%Y-%m-%d")
        db_manager.save_recommendation_matrix_record(today_str, bull_picks, neutral_picks, bear_picks)
        data = build_dashboard_data()
        global CHART_CACHE
        CHART_CACHE = data.get("charts", {})
        return {"status": "success", "message": f"{today_str} 실시간 스캔 & 차트 갱신 완료!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
