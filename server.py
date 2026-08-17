import os
import sys
import json
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="Al-Sangmoo Quant Backend", version="1.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_HTML = os.path.join(os.path.dirname(__file__), "al_sangmoo_dashboard.html")
DASHBOARD_JSON = os.path.join(os.path.dirname(__file__), "dashboard_data.json")

@app.on_event("startup")
def startup_event():
    db_manager.init_db()
    db_manager.sync_from_csv()
    print("Al-Sangmoo Quant FastAPI Server Started on http://localhost:8000")

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_HTML):
        with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard HTML Not Found</h1>"

@app.get("/api/dashboard")
def get_dashboard_data():
    if os.path.exists(DASHBOARD_JSON):
        with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return build_dashboard_data()

@app.get("/api/chart/{ticker}")
def get_ticker_chart(ticker: str):
    ticker_upper = ticker.upper()
    data = compute_all_indicators(ticker_upper)
    if not data:
        raise HTTPException(status_code=404, detail=f"Failed to fetch data for ticker: {ticker_upper}")
    return data

@app.get("/api/trades")
def get_trades():
    conn = db_manager.get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY date DESC, id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/scan_now")
def trigger_scan_now():
    data = build_dashboard_data()
    return {"status": "success", "message": "Scan completed and dashboard updated!", "data": data}

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
