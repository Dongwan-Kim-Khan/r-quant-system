# R-Quant System Repository Restructuring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the repository root, consolidate legacy research assets into `archive/`, remove stray test databases, provide an encoding-safe launcher, and deliver a comprehensive flagship `README.md` presenting the evolution and production architecture of the R-Quant System without decorative emojis or exposed credentials.

**Architecture:** Consolidate historical YouTube transcript and corpus extraction directories under `archive/`. Keep core production packages and CI/CD files untouched at root. Rewrite `README.md` from the ground up to document the 5-phase evolution, quantitative models, system architecture, directory map, and setup instructions.

**Tech Stack:** Python 3.11+, FastAPI, SQLite WAL, Git, Mermaid, PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-28-r-quant-system-repo-restructuring-design.md`

## Global Constraints

- Preserve internal Python package name `al_sangmoo/` to prevent import failures across production and test modules.
- Preserve root location of `al_sangmoo_daily_bot.py`, `trade_history.csv`, and `dashboard_data.json` for `.github/workflows/daily_al_sangmoo_briefing.yml`.
- Never include active API keys, secrets, tokens, or personal identifiers in documentation or tracked files.
- Avoid decorative emojis and conversational filler. Maintain an objective, institutional engineering tone.

## Review Focus

- Accidental moving of production files: verify only legacy research/transcript directories are moved.
- Broken imports in server: verify `server.py` and routers import cleanly.
- Secret leaks: verify no live tokens or accounts in `README.md` or git status.
- Windows character encoding issues: ensure `run_terminal.bat` executes cleanly with UTF-8 support.
- Git status hygiene: confirm untracked temporary databases are eliminated.

---

### Task 1: Archive Consolidation & Root Cleanup

**Files:**
- Create: `archive/`
- Move: `al_sangmoo_distill/` -> `archive/al_sangmoo_distill/`
- Move: `al_sangmoo_transcripts/` -> `archive/al_sangmoo_transcripts/`
- Move: `transcripts_and_raw_data/` -> `archive/transcripts_and_raw_data/`
- Delete: `test_challenger1_p5_3.db`, `test_challenger2_empirical.db`, `test_quant_trades_p5_2.db`, `test_quant_trades_p5_3.db`
- Create: `run_terminal.bat`

**Interfaces:**
- Consumes: Existing root directories and batch file logic.
- Produces: Clean root structure, `archive/` hierarchy, and `run_terminal.bat`.

- [ ] **Step 1: Create archive directory and relocate historical corpus folders**

Execute Git commands to track relocations:
```powershell
git mv al_sangmoo_distill archive/al_sangmoo_distill
git mv al_sangmoo_transcripts archive/al_sangmoo_transcripts
git mv transcripts_and_raw_data archive/transcripts_and_raw_data
```

- [ ] **Step 2: Clean up stray test database files from workspace root**

Remove untracked test databases generated during earlier test runs:
```powershell
Remove-Item -Force -ErrorAction SilentlyContinue test_challenger1_p5_3.db, test_challenger2_empirical.db, test_quant_trades_p5_2.db, test_quant_trades_p5_3.db
```

- [ ] **Step 3: Create encoding-safe Windows launcher `run_terminal.bat`**

Write `run_terminal.bat` mirroring the process cleanup, port check, and python detection logic of the Korean batch script, but with English messages and standard naming:
```bat
@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title [R-Quant System - Production Engine Terminal]

cd /d "%~dp0"

echo ======================================================================
echo   R-Quant System Terminal - C-2 Production Engine
echo   [34/33/33 Sizing + Cash Proxy / Dual Stop -7.0%% EOD, -10.0%% Emerg]
echo ======================================================================
echo  [1] Checking and terminating stale processes on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

set BOOT_ID=%RANDOM%%RANDOM%

echo  [2] Waiting for server on port 8000 and launching browser...
start /b "" cmd /c "for /L %%i in (1,1,45) do (netstat -ano | findstr :8000 | findstr LISTENING >nul && (start "" http://localhost:8000/?boot=%BOOT_ID% & exit /b) & ping 127.0.0.1 -n 2 >nul)"

echo  [3] Starting FastAPI backend server...
set "PYEXE="
if exist "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" (
    set "PYEXE=C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe"
) else if exist "C:\Users\kdw58\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYEXE=C:\Users\kdw58\AppData\Local\Programs\Python\Python312\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
)

if defined PYEXE (
    "%PYEXE%" server.py
) else (
    python server.py
)
```

- [ ] **Step 4: Verify root directory structure**

Run: `Get-ChildItem -Directory`
Verify `archive/` is present and root contains only core modules, documentation, and entrypoints.

---

### Task 2: Git Configuration & Credential Shielding Check

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Current `.gitignore` rules.
- Produces: Hardened `.gitignore` guaranteeing no test artifacts or secrets get staged.

- [ ] **Step 1: Audit `.gitignore` for complete coverage**

Ensure `.gitignore` covers:
- `test_*.db*`
- `*.db-wal`, `*.db-shm`, `*.db-journal`
- `.env`, `*.env`, `.env.*`, `!.env.example`
- `.kis_token_*.json`, `data/.kis_token_*.json`
- `data/charts/*.json`
- `__pycache__/`, `*.pyc`

- [ ] **Step 2: Verify git status for secret exposure**

Run: `git status`
Confirm `.env` and token caches remain untracked and unstaged.

---

### Task 3: Comprehensive Flagship README.md Authoring

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Quantitative architecture, history documentation, and system specifications.
- Produces: Professional, institutional markdown documentation.

- [ ] **Step 1: Write the updated `README.md`**

Structure:
1. Header & Technical Specification Badges (Python, FastAPI, SQLite WAL, KIS OpenAPI, WebSocket, GitHub Actions).
2. Executive Overview (Project mission, quantitative philosophy, architecture scope).
3. Core Quantitative Framework (MSI 2.0 Macro Stance, 3M RS Momentum, Ichimoku Cloud, 3-slot integer allocation, Dual Stop, Trailing TP, Cash Proxy).
4. Project Evolution & History (Detailed 5-phase timeline from YouTube research corpus to institutional trading platform).
5. Architecture Blueprint (Mermaid sequence/component diagram).
6. Repository Directory Map (Table and tree explaining all folders including `archive/`).
7. Quick Start & Operating Guide (Environment setup with dummy `.env.example`, execution instructions).
8. Automated Testing & Verification Guide (`pytest tools_and_tests/`).

- [ ] **Step 2: Verify formatting, links, and tone**

Check that:
- No decorative emojis are used.
- All secrets are masked with placeholders (`your_kis_app_key_here`).
- Code blocks and markdown tables render cleanly without syntax errors.

---

### Task 4: System Verification & Integrity Audit

**Files:**
- Test: `server.py`, `tools_and_tests/test_domain_quant.py`, `tools_and_tests/test_risk_constants_ssot.py`

**Interfaces:**
- Consumes: Relocated structure, updated README.
- Produces: Verification evidence confirming zero regressions.

- [ ] **Step 1: Test server import**

Run: `python -c "import server; print('Server imports successfully')"`
Expected: Output `Server imports successfully`.

- [ ] **Step 2: Run core quantitative unit tests**

Run: `pytest tools_and_tests/test_domain_quant.py tools_and_tests/test_risk_constants_ssot.py -v`
Expected: 100% test pass.

- [ ] **Step 3: Check git status and staging readiness**

Run: `git status`
Verify tracked files, moved files, and modified README.
