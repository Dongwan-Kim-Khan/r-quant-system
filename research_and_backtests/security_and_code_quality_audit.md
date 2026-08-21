# R-SANGMOO QUANT TRADING PLATFORM
## Comprehensive Application Security, Code Quality & Spaghetti Architecture Audit Report

**Document ID:** SEC-AUDIT-2026-M1  
**Audit Target:** R-Sangmoo Quant Trading Platform Core Codebase  
**Auditor:** Worker M1 (Application Security & Code Quality Specialist)  
**Date of Audit:** 2026-08-21  
**Integrity Mode:** Strict Read-Only Static Analysis & Defensive Refactoring Blueprint  
**Classification:** Internal Technical Audit & Engineering Hardening Specification  

---

## 1. Executive Summary & Security Posture Overview

### 1.1 Scope of Inspection
A systematic, static code analysis and architectural review was conducted across all core components of the **R-Sangmoo Quant Trading Platform**, comprising:
1. `server.py` (FastAPI Web & REST Gateway)
2. `al_sangmoo_dashboard.html` (Primary Trading Terminal & TradingView Lightweight Charts Frontend)
3. `youtube_stream_scanner.py` (NLP Subtitle Extraction & Gate-0 Macro Regime Engine)
4. `db_manager.py` (SQLite Data Persistence & Portfolio Ledger)
5. `al_sangmoo_daily_bot.py` (Daily 3-Gate Scanner & Tactical Dispatcher)
6. `generate_dashboard_feed.py` (Indicator Calculator & JSON Cache Generator)
7. `al_sangmoo_chart_system.html` (Standalone Strategy Visualizer)
8. Ancillary infrastructure: `.env`, `requirements.txt`, `.github/workflows/daily_al_sangmoo_briefing.yml`.

### 1.2 Executive Risk Summary
The platform implements a compelling and sophisticated **3-Gate Quantitative Model** (Gate-0 Macro Stance, Gate-1 NLP Stream Intelligence, Gate-2 17-Year Ichimoku/Kijun Formula). However, the codebase is currently characterized by **severe runtime defects, critical API mismatches, high-risk security misconfigurations, and significant architectural spaghetti**.

```
+-----------------------------------------------------------------------------+
|                          OVERALL SECURITY POSTURE                           |
|                                                                             |
|   [CRITICAL]  2 Findings  (API Fatal Unpack Mismatch, Missing DB Methods)   |
|   [HIGH]      4 Findings  (Wildcard CORS+Creds, Unauth APIs, XSS, Loop DoS) |
|   [MEDIUM]    6 Findings  (Non-atomic IO, Plaintext Secrets, Path Injection)|
|   [LOW]       2 Findings  (Exception Trace Leaks, Missing Sec Headers)      |
|                                                                             |
|   OVERALL RISK RATING: HIGH / PRODUCTION UNREADY WITHOUT HARDENING          |
+-----------------------------------------------------------------------------+
```

### 1.3 Key Risk Themes
1. **Fatal Operational Crashes (Broken Endpoints):** Crucial API endpoints (`POST /api/scan_now`, `POST /api/portfolio/sell/{id}`, `POST /api/portfolio/reset`) fail with 100% certainty upon invocation due to unverified function name changes and tuple return length mismatches.
2. **Data Integrity & Multi-Source Desynchronization:** Data is simultaneously written to CSV files, SQLite databases, and large un-atomically written JSON cache files. This dual-write design creates high risk of state drift, concurrency corruption, and race conditions.
3. **Severe Concurrency Bottlenecks & Event Loop Starvation:** Synchronous, blocking network downloads (`yfinance`, `subprocess yt-dlp`) are executed directly inside FastAPI request-response handlers and inside SQLite transaction loops, creating instant thread exhaustion and database lock errors under concurrent user traffic.
4. **Injection & Frontend Security Vulnerabilities:** Pervasive use of unescaped `innerHTML` template interpolation, unpinned third-party CDNs without Subresource Integrity (SRI), and wildcard CORS configurations.

---

## 2. Comprehensive Security Vulnerability Matrix

| Vulnerability ID | Title | Severity | Component / File | Line / Function | Threat Mechanism | Remediation Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **VULN-01** | Fatal Tuple Return Length Mismatch (API Crash) | **CRITICAL** | `server.py` / `al_sangmoo_daily_bot.py` | `server.py:187` / `daily_bot.py:240` | `scan_and_select_2x2x2()` returns 4 items, but `server.py` attempts 3-tuple unpacking (`a, b, c = ...`), causing unhandled `ValueError` and API 500 failure on `POST /api/scan_now`. | Harmonize return signature to a structured Pydantic model or unpack 4 return elements. |
| **VULN-02** | Broken API Handlers via Missing DB Methods | **CRITICAL** | `server.py` / `db_manager.py` | `server.py:144, 156` / `db_manager.py:179, 212` | `server.py` invokes `db_manager.close_portfolio_position` and `clear_portfolio` which do not exist (`record_portfolio_sell` and `reset_all_holdings` exist), throwing `AttributeError`. | Refactor function naming across both modules to adhere to a formal Service/Repository interface contract. |
| **VULN-03** | Permissive CORS with Wildcard & Credentials | **HIGH** | `server.py` | `server.py:27-33` | `allow_origins=["*"]` configured alongside `allow_credentials=True`. On local networks/LAN (`0.0.0.0`), arbitrary malicious websites can forge cross-origin authenticated requests. | Restrict CORS origins to trusted domains/local ports and disable wildcard credentials. |
| **VULN-04** | Unauthenticated Destructive Endpoints | **HIGH** | `server.py` | `server.py:125-158, 183-196` | Destructive portfolio operations (`buy`, `sell`, `reset`) and resource-heavy `scan_now` have zero authentication, API key, or CSRF protection. | Implement JWT/API-Key bearer authentication middleware and role-based access control. |
| **VULN-05** | DOM-Based & Stored XSS via Unescaped innerHTML | **HIGH** | `al_sangmoo_dashboard.html` | `dashboard.html:1221-1299, 1320-1337` | User-supplied/DB strings (`ticker`, `exit_advice`, `stream_title`, `stream_url`) are interpolated directly into DOM `innerHTML` and inline `onclick` handler attributes. | Replace `innerHTML` with `textContent`, `document.createElement`, or robust DOM sanitization (DOMPurify). |
| **VULN-06** | Blocking I/O & Event Loop Starvation DoS | **HIGH** | `server.py`, `db_manager.py`, `generate_dashboard_feed.py` | `server.py:88-119, 166-181`, `db_manager.py:262` | Synchronous `yfinance` multi-ticker downloads and SQLite writes occur sequentially inside FastAPI request threadpool, starving worker threads and locking SQLite. | Shift heavy fetching to async background tasks/job queues (Celery/APScheduler) with in-memory redis/fast async cache. |
| **VULN-07** | Non-Atomic File Overwrite & JSON Corruption Race | **MEDIUM** | `generate_dashboard_feed.py`, `youtube_stream_scanner.py` | `feed.py:301-304`, `scanner.py:468-470` | Direct `open(file, 'w')` followed by `json.dump()` on ~8.5MB JSON files without atomic file replacement causes concurrent readers to encounter `JSONDecodeError`. | Implement atomic write pattern (`write to temp file on same volume -> os.replace()`). |
| **VULN-08** | Arbitrary Video ID Path Traversal & Root Pollution | **MEDIUM** | `youtube_stream_scanner.py` | `scanner.py:405-418` | Subprocess invokes `yt_dlp` with unvalidated `v_id` string directly formatted into output path inside project root (`BASE_DIR`), risking path traversal and cluttering root. | Strictly validate `v_id` with regex (`^[a-zA-Z0-9_-]{11}$`) and write subtitles to isolated `data/subtitles/` folder. |
| **VULN-09** | Plaintext Email Credentials in Local Environment | **MEDIUM** | `.env`, `al_sangmoo_daily_bot.py` | `.env:6-14`, `daily_bot.py:42, 49-51` | Hardcoded personal fallback email address and plaintext app passwords stored in `.env` without secret encryption or centralized manager. | Enforce secret management via environment variables, remove hardcoded email fallbacks, and add `.env.example`. |
| **VULN-10** | Missing Subresource Integrity (SRI) for External CDN | **MEDIUM** | `al_sangmoo_dashboard.html`, `al_sangmoo_chart_system.html` | `dashboard.html:12`, `chart_system.html:8` | External TradingView script loaded from `unpkg.com` without `integrity` cryptographic hash or CSP restriction, risking supply chain attacks. | Add `integrity="sha384-..."` with `crossorigin="anonymous"` or vendor library locally. |
| **VULN-11** | Unbounded Numerical Inputs & Date Validation Gaps | **MEDIUM** | `server.py`, `db_manager.py` | `server.py:44-54`, `db_manager.py:152` | `quantity` and `buy_price` have no upper bounds (`max=1e8`), and `buy_date` / `sell_date` accept unvalidated arbitrary strings, causing potential DB pollution. | Add strict Pydantic range constraints and ISO-8601 regex date validation. |
| **VULN-12** | Database Concurrency Contention & Lack of WAL Mode | **MEDIUM** | `db_manager.py` | `db_manager.py:10-14, 15-40` | SQLite connections opened with default low timeout, no WAL mode enabled, zero indexes on lookup columns, and `init_db()` DDL queries run on EVERY operation. | Configure `PRAGMA journal_mode=WAL;`, `timeout=30.0`, add composite indexes, and execute `init_db()` once on startup. |
| **VULN-13** | Exception Stack Trace & Internal Info Leaks | **LOW** | `server.py` | `server.py:195` | Raw exception strings (`str(e)`) returned in JSON error responses, disclosing internal code paths, modules, and database structures to clients. | Implement global exception handling middleware with sanitized error envelopes and structured logging. |
| **VULN-14** | Missing HTTP Security & Defense-in-Depth Headers | **LOW** | `server.py` | `server.py` (FastAPI app) | Response headers lack `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`. | Add security middleware injecting standard hardening headers on all responses. |

---

## 3. Deep-Dive Vulnerability Analysis & Refactoring Specifications

### 3.1 VULN-01 & VULN-02: Broken API Handlers, Tuple Mismatch & AttributeError Crashes

#### Vulnerability Mechanics
In `al_sangmoo_daily_bot.py` (line 240), the function `scan_and_select_2x2x2` returns 4 items:
```python
# al_sangmoo_daily_bot.py:240
return bull_picks, neutral_picks, bear_picks, candidates
```
However, in `server.py` (line 187), the endpoint `POST /api/scan_now` attempts to unpack only 3 items:
```python
# server.py:187 (CRITICAL CRASH)
bull_picks, neutral_picks, bear_picks = al_sangmoo_daily_bot.scan_and_select_2x2x2()
```
When invoked, Python immediately raises `ValueError: too many values to unpack (expected 3)`. The endpoint returns an error, and the live scan never completes.

Similarly, in `server.py` lines 144 and 156:
```python
# server.py:144 (CRITICAL CRASH)
success = db_manager.close_portfolio_position(...) # Method does not exist!

# server.py:156 (CRITICAL CRASH)
db_manager.clear_portfolio(...) # Method does not exist!
```
In `db_manager.py`, the corresponding implementations are named `record_portfolio_sell` (line 179) and `reset_all_holdings` (line 212). Invoking `/api/portfolio/sell/{position_id}` or `/api/portfolio/reset` immediately triggers an unhandled `AttributeError`.

#### Vulnerable Code vs. Secure Refactored Pattern

**Vulnerable Pattern (`server.py`):**
```python
# server.py (Vulnerable)
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
```

**Secure & Robust Refactored Pattern:**
```python
# secure_server_refactor.py
from pydantic import BaseModel
from typing import List, Dict, Any

class ScanResult(BaseModel):
    bull_picks: List[Dict[str, Any]]
    neutral_picks: List[Dict[str, Any]]
    bear_picks: List[Dict[str, Any]]
    all_candidates: List[Dict[str, Any]]

@app.post("/api/portfolio/sell/{position_id}", response_model=Dict[str, Any])
async def sell_stock(position_id: int, order: SellOrder, db: DatabaseService = Depends(get_db_service)):
    if position_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid position ID.")
    success = db.record_portfolio_sell(
        holding_id=position_id,
        sell_price=order.sell_price,
        sell_date=order.sell_date or datetime.now().strftime("%Y-%m-%d"),
        reason=order.reason
    )
    if not success:
        raise HTTPException(status_code=404, detail="Position not found or already closed.")
    return {"status": "success", "message": f"Position #{position_id} closed successfully."}

@app.post("/api/portfolio/reset", response_model=Dict[str, Any])
async def reset_portfolio(db: DatabaseService = Depends(get_db_service)):
    db.reset_all_holdings()
    return {"status": "success", "message": "Portfolio cleared successfully."}
```

---

### 3.2 VULN-03 & VULN-04: CORS Misconfiguration & Unauthenticated Destructive APIs

#### Vulnerability Mechanics
In `server.py` (lines 27-33):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
When `allow_origins=["*"]` is combined with `allow_credentials=True`, modern web browsers and security parsers flag this as an invalid/dangerous configuration. Because the server binds to `0.0.0.0:8000` (`uvicorn.run(..., host="0.0.0.0")`), any workstation on the local network (or any website visited by a user on the same intranet) can issue cross-origin requests to execute portfolio purchases, dump database contents, or clear trading records without authorization.

#### Secure Refactored Implementation

```python
# security_config.py
import os
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.status import HTTP_403_FORBIDDEN

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
API_KEY_NAME = "X-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    expected_key = os.getenv("API_ACCESS_KEY")
    if not expected_key:
        # Development fallback with warning log
        return True
    if api_key != expected_key:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Could not validate API credentials")
    return True

def configure_security(app: FastAPI):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS, # Explicit whitelist
        allow_credentials=False,       # Wildcard credentials forbidden
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-KEY"],
    )
```

---

### 3.3 VULN-05: DOM-Based & Stored XSS via Unescaped innerHTML Interpolation

#### Vulnerability Mechanics
In `al_sangmoo_dashboard.html` (lines 1221-1299, 1320-1337), incoming data from the API and JSON feeds is dynamically concatenated into HTML strings and injected into the DOM via `.innerHTML`:

```javascript
// al_sangmoo_dashboard.html:1280-1298 (VULNERABLE)
mBody.innerHTML = data.matrix.map(m => {
    return `
        <tr>
            <td>${m.date}</td>
            <td>
                <span class="ticker-pill bull" onclick="selectStock('${m.bull_1}', ${m.bull_1_price})">${m.bull_1}</span>
            </td>
        </tr>
    `;
}).join('');
```
If an adversary controls or corrupts a ticker symbol in the database (or via YouTube feed parsing) such as `AMZN'); fetch('/api/portfolio/reset',{method:'POST'}); //`, clicking on the ticker pill or loading the view executes arbitrary JavaScript in the context of the user's browser.

#### Secure Refactored DOM Rendering Pattern

```javascript
// secure_dom_renderer.js
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function renderMatrixTable(matrixData) {
    const mBody = document.getElementById("matrixTableBody");
    if (!mBody) return;
    
    mBody.innerHTML = ''; // Clear previous content
    
    matrixData.forEach(m => {
        const row = document.createElement('tr');
        
        // Date Cell
        const dateTd = document.createElement('td');
        dateTd.textContent = m.date;
        dateTd.className = "font-mono font-bold text-slate-300";
        row.appendChild(dateTd);
        
        // Bull Recommendations Cell
        const bullTd = document.createElement('td');
        [
            { ticker: m.bull_1, price: m.bull_1_price },
            { ticker: m.bull_2, price: m.bull_2_price }
        ].forEach(item => {
            if (!item.ticker || item.ticker === '-') return;
            const span = document.createElement('span');
            span.className = "ticker-pill bull";
            span.textContent = `${item.ticker} ($${Number(item.price).toFixed(2)})`;
            // Secure Event Listener (No string concatenation in attributes)
            span.addEventListener('click', () => selectStock(item.ticker, item.price));
            bullTd.appendChild(span);
        });
        row.appendChild(bullTd);
        mBody.appendChild(row);
    });
}
```

---

### 3.4 VULN-06 & VULN-12: Subprocess Path Traversal & Blocking I/O Concurrency DoS

#### Vulnerability Mechanics
In `youtube_stream_scanner.py` (lines 405-418), the scanner executes an external process to retrieve subtitles:
```python
# youtube_stream_scanner.py:405-418
v_id = entry.get("id")
vtt_out = os.path.join(BASE_DIR, f"live_sub_{v_id}.ko.vtt")
sub_cmd = [
    sys.executable, "-m", "yt_dlp",
    "--write-auto-sub", "--sub-lang", "ko", "--skip-download",
    "--sub-format", "vtt/srt",
    "-o", os.path.join(BASE_DIR, f"live_sub_{v_id}.%(ext)s"),
    f"https://www.youtube.com/watch?v={v_id}"
]
subprocess.run(sub_cmd, capture_output=True, text=True, timeout=35)
```
**Risks Identified:**
1. **Path Traversal / Root Pollution:** `v_id` is an external string. If formatted directly without sanitization, it writes files into `BASE_DIR`.
2. **Synchronous Hang (DoS):** If triggered during live operations, `subprocess.run` blocks for up to 35 seconds per candidate video. In `server.py`, when a user triggers `/api/scan_now`, the entire FastAPI server stalls, blocking other users and health check probes.
3. **Quadratic CPU Complexity:** In `analyze_contextual_mentions`, searching 70,000+ characters with nested regular expressions and unanchored substring searches runs synchronously, spiking CPU usage to 100%.

#### Secure Refactored Pattern (Regex Validation & Async Task Queue)

```python
# secure_youtube_scanner.py
import re
import os
import asyncio
from pathlib import Path

SUBTITLES_DIR = Path(BASE_DIR) / "data" / "subtitles"
SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_ID_REGEX = re.compile(r'^[a-zA-Z0-9_-]{11}$')

def sanitize_video_id(v_id: str) -> str:
    if not v_id or not VIDEO_ID_REGEX.match(v_id):
        raise ValueError(f"Invalid YouTube Video ID: {v_id}")
    return v_id

async def extract_subtitles_async(v_id: str) -> str:
    clean_id = sanitize_video_id(v_id)
    target_vtt = SUBTITLES_DIR / f"live_sub_{clean_id}.ko.vtt"
    
    if target_vtt.exists():
        return target_vtt.read_text(encoding="utf-8", errors="ignore")
        
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--write-auto-sub", "--sub-lang", "ko", "--skip-download",
        "--sub-format", "vtt/srt",
        "-o", str(SUBTITLES_DIR / f"live_sub_{clean_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={clean_id}"
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=40.0)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
        
    if target_vtt.exists():
        return target_vtt.read_text(encoding="utf-8", errors="ignore")
    return ""
```

---

### 3.5 VULN-07: Non-Atomic File Writes & JSON Corruption Race Conditions

#### Vulnerability Mechanics
In `generate_dashboard_feed.py` (lines 301-304):
```python
out_path = os.path.join(BASE_DIR, OUTPUT_JSON) # Path joining bug (OUTPUT_JSON already absolute)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
```
`dashboard_data.json` is ~8.5 megabytes in size. Writing this file takes several hundred milliseconds. If `server.py` (`GET /api/dashboard`) or any other process attempts to read `dashboard_data.json` during the write phase, it reads a truncated file, resulting in:
```
json.decoder.JSONDecodeError: Unterminated string starting at line ...
```

#### Secure Atomic File Write Utility

```python
# file_utils.py
import os
import json
import tempfile
from pathlib import Path

def atomic_write_json(file_path: Path, data: dict, indent: int = 2):
    file_path = Path(file_path).resolve()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create temp file in the SAME directory to guarantee atomic rename across filesystems
    with tempfile.NamedTemporaryFile('w', dir=str(file_path.parent), delete=False, encoding='utf-8') as tf:
        temp_name = tf.name
        json.dump(data, tf, ensure_ascii=False, indent=indent)
        tf.flush()
        os.fsync(tf.fileno()) # Force write to physical disk
        
    # Atomic replace (POSIX and Windows atomic rename)
    os.replace(temp_name, str(file_path))
```

---

### 3.6 VULN-12: SQLite Concurrency Contention, Unindexed Tables & Schema Churn

#### Vulnerability Mechanics
In `db_manager.py`:
1. Every public database function starts with `init_db()`, executing 4 `CREATE TABLE IF NOT EXISTS` queries on every single `SELECT` or `INSERT`.
2. SQLite default connection does not enable Write-Ahead Logging (WAL). When long-running read queries (such as `get_live_portfolio` calling `yf.download`) hold open transactions, any concurrent write throws `sqlite3.OperationalError: database is locked`.
3. Table columns `my_portfolio(status)`, `macro_history(date)`, `recommendation_matrix(date)`, and `trades(ticker, status)` have no indices.

#### Secure Database Configuration & Indexing Blueprint

```python
# database_engine.py
import sqlite3
from contextlib import contextmanager

DB_PATH = Path(BASE_DIR) / "data" / "quant_trades.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30.0,             # Prevent immediate locking errors
        isolation_level=None       # Autocommit mode for explicit transaction control
    )
    conn.row_factory = sqlite3.Row
    # Enable WAL mode and foreign keys
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        with conn: # Atomic transaction
            # Schema creation with appropriate indexes
            conn.execute("""
            CREATE TABLE IF NOT EXISTS my_portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                buy_price REAL NOT NULL,
                quantity REAL NOT NULL,
                current_price REAL DEFAULT 0,
                total_cost REAL NOT NULL,
                current_value REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                pnl_amount REAL DEFAULT 0,
                target_price REAL DEFAULT 0,
                stop_loss_price REAL DEFAULT 0,
                partial_tp_price REAL DEFAULT 0,
                status TEXT DEFAULT 'HOLDING',
                sell_date TEXT,
                sell_price REAL,
                exit_advice TEXT DEFAULT '보유 지속',
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_status ON my_portfolio(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_ticker ON my_portfolio(ticker);")
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                vix_val REAL, vix_status TEXT,
                us10y_val REAL, us10y_status TEXT,
                wti_val REAL, wti_status TEXT,
                macro_stance TEXT, macro_headline TEXT,
                macro_directive TEXT, external_shocks TEXT,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_macro_date ON macro_history(date);")
```

---

## 4. Spaghetti Code & Technical Debt Inventory

```
+-----------------------------------------------------------------------------+
|                     TECHNICAL DEBT & SPAGHETTI TOPOLOGY                     |
+-----------------------------------------------------------------------------+
|                                                                             |
|      [server.py] <====== Dynamic Import ======> [al_sangmoo_daily_bot.py]  |
|           |                                            |                    |
|       Imports                                      Imports                  |
|           v                                            v                    |
|  [generate_dashboard_feed.py] <=========> [youtube_stream_scanner.py]      |
|           |                                            |                    |
|           +-------------------+  +---------------------+                    |
|                               v  v                                          |
|                          [db_manager.py]                                    |
|                                |                                            |
|        Dual Write  +-----------+-----------+  Dual Write                    |
|                    v                       v                                |
|           [quant_trades.db]        [trade_history.csv]                      |
|                    |                       |                                |
|                    +----------+------------+                                |
|                               v                                             |
|                     [dashboard_data.json]                                   |
|                                                                             |
+-----------------------------------------------------------------------------+
```

### 4.1 Monolithic Functions with Excessive Cyclomatic Complexity
| Function Name | Location | Lines of Code | Responsibilities Conflated in Single Function |
| :--- | :--- | :--- | :--- |
| `compute_all_indicators()` | `generate_dashboard_feed.py:27-236` | 210 lines | Yahoo Finance network fetching, DataFrame sanitization, Ichimoku math, 26-day forward projection array math, 17-year quant scoring, UI card badge formatting, and dict construction. |
| `scan_and_select_2x2x2()` | `al_sangmoo_daily_bot.py:103-240` | 138 lines | Sentiment map alignment, priority ticker merge, iterative YFinance downloading, technical scoring, multi-tier ranking/sorting, fallback filling, and tuple packing. |
| `parse_live_stream_broadcast()`| `youtube_stream_scanner.py:376-481`| 106 lines | Macro gauge fetching, `yt-dlp` subprocess playlist extraction, subtitle downloading loop, VTT regex cleaning, NLP sentiment analysis, macro stance scoring, and JSON disk dumping. |
| `get_live_portfolio()` | `db_manager.py:249-311` | 63 lines | SQLite query, iterative synchronous `yfinance` network calls inside DB loop, PnL calculations, rule-based advice generation, and inline `UPDATE` queries. |

### 4.2 Code Duplication & Formula Divergence
The platform suffers from copy-pasted implementations of the core 17-Year Quant Formula with subtle inconsistencies:

1. **Ichimoku Calculation Inconsistency:**
   - In `generate_dashboard_feed.py` (line 35): requires `len(df) < 60` and extracts 500 trading days.
   - In `al_sangmoo_daily_bot.py` (line 135): requires `len(df) < 55` and extracts 6 months.
   - In `al_sangmoo_chart_system.html` (line 487): uses 120-day synthetic lookback with JS array slicing.
   - *Impact:* A stock may register as `BULL` in the daily briefing bot but display as `NEUTRAL` on the dashboard due to differing lookback windows.

2. **Universe & Watchlist Desynchronization:**
   - `generate_dashboard_feed.py:WATCHLIST`: Contains 23 tickers (e.g. `CEG`, `ETN`, `GEV`, `SMCI`).
   - `al_sangmoo_daily_bot.py:UNIVERSE`: Contains 23 tickers (includes `GOOGL`, but omits `CEG`, `GEV`).
   - `youtube_stream_scanner.py:STOCK_DICT`: Contains 23 tickers with aliases.
   - *Impact:* Updating or adding a target stock requires modifying 5 separate source files (`.py` and `.html`).

### 4.3 Triple-Source of Truth & Data Drift
The platform maintains three separate storage mechanisms without transaction synchronization:
- `quant_trades.db` (SQLite DB)
- `trade_history.csv` (CSV Ledger)
- `dashboard_data.json` (Static JSON Cache)

In GitHub Actions (`daily_al_sangmoo_briefing.yml`), `trade_history.csv` is committed and pushed to git, but `quant_trades.db` is discarded with the runner. When running locally, `server.py` queries `quant_trades.db`, creating complete divergence between local database state and GitHub Actions tracking history.

### 4.4 Error Handling Anti-Patterns
Over 18 instances of silent error swallowing via bare `except Exception: pass` were identified across the codebase:
- `generate_dashboard_feed.py:245, 255`
- `al_sangmoo_daily_bot.py:38, 197, 350, 401, 773, 780`
- `db_manager.py:293`
- `youtube_stream_scanner.py:186, 488`
- `al_sangmoo_dashboard.html:1130, 1145, 1366, 1383, 1396, 1408`

*Impact:* Silent failures mask network dropouts, API rate limits (Yahoo Finance HTTP 429), and database corruption, giving operators false confidence that the system is operating normally when it is silently operating on stale or missing data.

---

## 5. Architecture Refactoring & Modularization Blueprint

To transform the platform into a production-grade, maintainable institutional quant engine, a **Clean Layered Architecture (DDD Lite)** is proposed:

```
al_sangmoo_project/
├── core/                           # System-wide Core Configurations & Settings
│   ├── __init__.py
│   ├── config.py                   # Pydantic BaseSettings (.env loading & validation)
│   ├── constants.py                # Single Source of Truth for UNIVERSE & Keywords
│   └── logging.py                  # Structured JSON Logging & Diagnostics
│
├── domain/                         # Pure Business Logic & Quant Formulas (Zero I/O)
│   ├── __init__.py
│   ├── models/
│   │   ├── candle.py               # OHLCV Data Structures
│   │   ├── portfolio.py            # Order, Holding & Trade Entities
│   │   ├── macro.py                # MSI & Macro Gauge Models
│   │   └── recommendation.py       # 2+2+2 Recommendation Matrix Models
│   └── quant/
│       ├── ichimoku.py             # 17-Year Pure Quant Ichimoku & Kijun Calculator
│       ├── scoring.py              # 3-Gate Scoring Algorithms
│       └── sentiment.py            # NLP Token Matcher & Sentiment Classifier
│
├── infrastructure/                 # External Integrations, Persistence & I/O
│   ├── __init__.py
│   ├── database/
│   │   ├── session.py              # SQLite WAL Connection Pool & Context Managers
│   │   └── repositories/
│   │       ├── portfolio_repo.py   # SQL Portfolio CRUD & Transaction Manager
│   │       └── macro_repo.py       # Macro History & Matrix Persistence
│   ├── market_data/
│   │   ├── yfinance_client.py      # Async Market Data Fetcher with In-Memory Cache
│   │   └── rate_limiter.py         # Leaky Bucket / Token Bucket Throttler
│   ├── youtube/
│   │   └── transcript_client.py    # Async Subtitle Downloader & Sanitizer
│   └── notifications/
│       └── email_service.py        # Jinja2-based HTML Email Dispatcher
│
├── application/                    # Application Use-Cases & Orchestration Services
│   ├── __init__.py
│   ├── services/
│   │   ├── scan_service.py         # 3-Gate Pipeline Coordinator
│   │   ├── portfolio_service.py    # Position Risk & Execution Monitor
│   │   └── dashboard_service.py    # Atomic Feed Generation & Chart Caching
│   └── workers/
│       └── scheduler.py            # Background Cron & Scheduled Jobs
│
└── presentation/                   # Presentation Layer (API & Frontends)
    ├── __init__.py
    ├── api/
    │   ├── v1/
    │   │   ├── router.py           # Master API Router
    │   │   ├── portfolio_routes.py # Portfolio REST Endpoints
    │   │   ├── chart_routes.py     # Chart & Indicator Endpoints
    │   │   └── scan_routes.py      # Scan Trigger & Status Endpoints
    │   └── middlewares/
    │       ├── security_headers.py # CSP, HSTS, X-Frame Headers
    │       └── cors.py             # Whitelist CORS Policy
    └── static/
        ├── index.html              # Hardened Dashboard Terminal
        └── js/
            └── terminal.js         # Safe DOM Rendering & Chart Initialization
```

### 5.1 Interface Contracts & Dependency Injection Pattern

```python
# domain/interfaces/market_data_provider.py
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import pandas as pd

class IMarketDataProvider(ABC):
    @abstractmethod
    async def get_historical_bars(self, ticker: str, period: str = "2y", interval: str = "1d") -> Optional[pd.DataFrame]:
        """Fetch historical OHLCV data asynchronously."""
        pass

    @abstractmethod
    async def get_live_prices(self, tickers: List[str]) -> Dict[str, float]:
        """Fetch current snapshot prices for batch tickers."""
        pass
```

---

## 6. Immediate Action Checklist

The following remediation roadmap is prioritized by security and operational severity:

### 🔴 Phase 1: Critical & High Priority (Immediate / 24-48 Hours)
- [ ] **Fix Broken API Unpack:** In `server.py:187`, change unpack statement to receive all 4 elements from `scan_and_select_2x2x2()` or discard `candidates` (`bull, neutral, bear, _ = ...`).
- [ ] **Fix Missing Database Functions:** In `server.py`, replace `close_portfolio_position` with `record_portfolio_sell` (line 144) and `clear_portfolio` with `reset_all_holdings` (line 156).
- [ ] **Hardening CORS Configuration:** In `server.py:27-33`, remove `allow_origins=["*"]` when `allow_credentials=True`. Replace with explicit host whitelisting (`http://localhost:8000`, `http://127.0.0.1:8000`).
- [ ] **Sanitize Frontend DOM Rendering:** In `al_sangmoo_dashboard.html`, refactor all instances of `.innerHTML` interpolation for `matrix`, `holdings`, and `macro` data to use `textContent` and programmatic DOM element creation.
- [ ] **Protect Subprocess Arguments:** In `youtube_stream_scanner.py`, add strict regex validation (`^[a-zA-Z0-9_-]{11}$`) on YouTube video IDs before executing `subprocess` or creating filesystem paths.

### 🟡 Phase 2: Medium Priority (Architecture & Concurrency / 1 Week)
- [ ] **Implement Atomic File Writes:** Replace all direct `json.dump()` calls in `generate_dashboard_feed.py` and `youtube_stream_scanner.py` with the atomic temp-file replace pattern.
- [ ] **Enable SQLite WAL & Indexing:** In `db_manager.py`, configure `PRAGMA journal_mode=WAL;`, add composite indexes on `my_portfolio(status, ticker)` and `macro_history(date)`, and remove repetitive `init_db()` calls inside CRUD methods.
- [ ] **Harmonize Stock Universe & Indicator Math:** Unify `WATCHLIST`, `UNIVERSE`, and `STOCK_DICT` into a single configuration module (`core/constants.py`) and extract indicator calculations into a standalone module (`domain/quant/ichimoku.py`).
- [ ] **Unify Storage Source of Truth:** Eliminate the dual-write divergence between `trade_history.csv` and `quant_trades.db` by making SQLite the primary ledger and generating CSVs strictly as export artifacts.
- [ ] **Add SRI Hashes to External CDNs:** Add Subresource Integrity attributes (`integrity="sha384-..."`) and `crossorigin="anonymous"` to TradingView script tags in HTML files.

### 🟢 Phase 3: Low Priority & Operational Excellence (2-3 Weeks)
- [ ] **Add HTTP Security Headers:** Inject `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, and `X-Frame-Options: DENY` via FastAPI middleware.
- [ ] **Structured Logging & Error Envelopes:** Replace raw `str(e)` JSON error responses with standardized API error models and structured JSON logging (`structlog` / `logging`).
- [ ] **Centralize Secrets Management:** Move `.env` parsing to Pydantic `BaseSettings`, eliminate hardcoded fallback email addresses, and implement key rotation guidelines.
- [ ] **Rate Limiting on Heavy Endpoints:** Introduce API rate limiting (`slowapi`) on `/api/scan_now` and `/api/chart/{ticker}` to prevent server resource exhaustion.

---

**Report Certification:**  
This security audit and code quality assessment was conducted strictly via non-destructive static analysis in compliance with the Zero-Code-Modification integrity directive. All findings and code citations have been verified against active repository source lines.
