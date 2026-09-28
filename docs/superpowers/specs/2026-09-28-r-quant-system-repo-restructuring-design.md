# R-Quant System Repository Restructuring & Flagship Documentation Design

- **Date**: 2026-09-28
- **Project**: R-Quant System (Al-Sangmoo Institutional Quant Platform)
- **Status**: Approved for Planning

---

## 1. Executive Summary & Intent

The repository has evolved from an initial experimental research script (scraping YouTube video streams and sending daily alert emails) into an institutional-grade algorithmic swing-trading platform featuring FastAPI, WebSocket real-time broadcast hubs, Korea Investment & Securities (KIS) OpenAPI integration, SQLite WAL persistence, and a high-performance web terminal.

The repository name was changed to `r-quant-system`. However, the root directory remains cluttered with legacy transcription directories, test database files, and outdated documentation referencing only the early morning email bot.

This design establishes a clean directory hierarchy, archives historical research assets, ensures zero API key exposure, protects GitHub Actions CI/CD workflows, and delivers a professional flagship `README.md`.

---

## 2. Constraints & Principles

1. **Package Stability**: Maintain internal Python package name `al_sangmoo/` to preserve import stability across 100+ files and avoid regressions in tested core modules.
2. **CI/CD Pipeline Integrity**: Keep `al_sangmoo_daily_bot.py`, `dashboard_data.json`, and `trade_history.csv` accessible at root as required by `.github/workflows/daily_al_sangmoo_briefing.yml`.
3. **Strict Credential Protection**: Ensure `.env`, `.kis_token_*.json`, secret credentials, and account identifiers are strictly excluded from git tracking. The README must only present placeholder dummy configurations (`your_api_key_here`).
4. **Clean Tone & Non-AI Style**: Remove decorative emojis and syrupy AI phrasing. Use precise, institutional, engineering-focused language in all documentation.

---

## 3. Directory Restructuring Plan

### 3.1 New Archive Directory (`archive/`)
Consolidate historical YouTube transcript processing and corpus distillation folders into `archive/`:
- `al_sangmoo_distill/` -> `archive/al_sangmoo_distill/`
- `al_sangmoo_transcripts/` -> `archive/al_sangmoo_transcripts/`
- `transcripts_and_raw_data/` -> `archive/transcripts_and_raw_data/`

### 3.2 Root Directory Cleanup
- Remove stray root test databases (`test_challenger1_p5_3.db`, `test_challenger2_empirical.db`, `test_quant_trades_p5_2.db`, `test_quant_trades_p5_3.db`).
- Verify `.gitignore` contains `test_*.db*`, `*.db-wal`, `*.db-shm`, `.env`, `.kis_token_*.json`, `data/charts/*.json`.
- Provide a clean, encoding-safe launcher: `run_terminal.bat` alongside the existing `알상무_퀀트_터미널_실행.bat`.

### 3.3 Target Layout
```
r-quant-system/
├── al_sangmoo/                 # Core domain quant, risk guardrails, broker interfaces, infrastructure
├── frontend/                   # Real-time web trading terminal UI (HTML, CSS, Vanilla JS, Canvas)
├── docs/                       # Architecture specs, investment prospectuses (C1/M2, C2), audit handoffs
│   ├── archive/                # Legacy architecture notes and design audits
│   ├── handoffs/               # Session handoff documentation
│   ├── prospectus/             # Investment prospectuses (EN/KO)
│   └── superpowers/specs/      # Design and implementation specifications
├── research_and_backtests/     # SEC N-PORT hedge fund filing parser, PIT universe backtester
├── tests/                      # Core autopilot and portfolio test suites
├── tools_and_tests/            # E2E security, concurrency, adversarial, and quant SSOT test suites
├── data/                       # Local universe snapshots, chart data, SQLite database files
├── daily_reports/              # Markdown archives of daily quant briefings
├── archive/                    # Historical research corpus (YouTube transcripts, raw subtitles, NLP distillation)
│   ├── al_sangmoo_distill/
│   ├── al_sangmoo_transcripts/
│   └── transcripts_and_raw_data/
├── server.py                   # FastAPI backend server entrypoint
├── al_sangmoo_daily_bot.py     # GitHub Actions morning scanner and forward tracker bot
├── generate_dashboard_feed.py  # Dashboard data feed generator
├── run_terminal.bat            # Cross-platform Windows execution launcher
├── 알상무_퀀트_터미널_실행.bat    # Korean console launcher with port auto-kill and browser launch
├── PROJECT.md                  # System architecture inventory and milestone status
├── TEST_INFRA.md               # Test infrastructure guidelines
└── README.md                   # Comprehensive technical documentation
```

---

## 4. Flagship README.md Specification

The new `README.md` will replace the outdated 31-line briefing bot note with an institutional-grade document structured as follows:

1. **Header & Badges**:
   - Title: `R-Quant System: Al-Sangmoo Institutional Quant Platform`
   - Badges: Python 3.11+, FastAPI, SQLite WAL, WebSocket, KIS OpenAPI, GitHub Actions.
   - One-line definition of the platform.

2. **Core Trading Doctrine & Quant SSOT**:
   - Mathematical formulation: 3-Month Relative Strength (RS).
   - Ichimoku Cloud mechanics: Kumo breakout, Tenkan-Kijun golden cross, Chikou span validation.
   - Macro Stance Index (MSI 2.0): Regime classification based on US 10Y Yield, DXY, VIX, High Yield Spread, and SPY 200 SMA.
   - Portfolio Risk Engine: 3-slot integer allocation, Dual Stop-Loss (-7% EOD / -10% Emergency), Trailing Take-Profit (+18%), Cash Proxy sleeve (QQQ/SGOV).

3. **Project Evolution & Milestones (Phase 1 to Phase 5)**:
   - Phase 1: Knowledge distillation from 50+ live streams of 17-year institutional quant manager Alex Oh (`archive/`).
   - Phase 2: Autonomous forward tracking bot via GitHub Actions (`al_sangmoo_daily_bot.py`).
   - Phase 3: SSOT domain engine and order concurrency guardrails (`al_sangmoo/domain/`).
   - Phase 4: Full-stack real-time trading terminal with FastAPI and WebSocket (`frontend/`, `server.py`).
   - Phase 5: C-2 Institutional bundle, SEC Form N-PORT hedge fund holdings tracker, Point-in-Time (PIT) backtester, 50+ automated adversarial test suites.

4. **System Architecture**:
   - Mermaid diagram detailing interaction between Web Client, FastAPI Server, Broadcast Hub, Quant Scoring & Macro Engines, Order Guardrail Mutex, KIS Broker Gateway, and SQLite Database.

5. **Repository Layout Guide**:
   - Structured table and tree describing the role of each directory.

6. **Getting Started**:
   - Environment setup: Python version, virtualenv, dependency installation.
   - Configuration: Setting up `.env` from `.env.example` with dummy values.
   - Running the server: Command line (`python server.py`) and one-click batch launcher.
   - Accessing the dashboard: `http://localhost:8000`.

7. **Automated Verification**:
   - Running test suites via `pytest`.

---

## 5. Security & Secret Protection Guardrails

- All examples in `README.md` and documentation will use explicit placeholder syntax:
  - `KIS_APP_KEY=your_kis_app_key_here`
  - `KIS_APP_SECRET=your_kis_app_secret_here`
  - `KIS_CANO=12345678`
  - `KIS_ACNT_PRDT_CD=01`
- Confirm that real `.env` is uncommitted and listed in `.gitignore`.
- Run `git status` verification before any commit to ensure zero secret exposure.

---

## 6. Implementation Stages

1. **Directory Restructuring**:
   - Move `al_sangmoo_distill`, `al_sangmoo_transcripts`, and `transcripts_and_raw_data` into `archive/`.
   - Remove root scratch `.db` files.
   - Create `run_terminal.bat`.
2. **README Overhaul**:
   - Write comprehensive, professional `README.md` following the specification.
3. **Verification**:
   - Run core tests (`pytest tools_and_tests/test_domain_quant.py`, `pytest tools_and_tests/test_risk_constants_ssot.py`) to confirm zero regressions.
   - Verify `server.py` startup imports.
   - Verify git status for clean changes and uncompromised credentials.
