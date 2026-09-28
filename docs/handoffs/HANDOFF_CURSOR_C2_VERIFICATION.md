# HANDOFF: C-2 PRODUCTION ENGINE VERIFICATION & INTEGRITY AUDIT

**Date:** 2026-09-16 (KST)  
**From:** Antigravity Agent  
**To:** Cursor Agent / Operator  
**Purpose:** Independent cross-verification of the frozen **C-2 Production Engine**, investment prospectus synchronization, dashboard DIV alignments, and operational readiness before production paper trading.  
**Git Working Tree:** Branch `main` (clean working state with uncommitted C-2 production updates)  
**Related Documents:**
- `HANDOFF_ANTIGRAVITY_C2_VERDICT.md` (Decision Lock & Backtest Verdict)
- `DEPLOYMENT_RUNBOOK_C2.md` (Operational Runbook & Staging Protocol)
- `INVESTMENT_PROSPECTUS_C2_KO.md` / `INVESTMENT_PROSPECTUS_C2.md` (C-2 PPM/KIID Prospectus)

---

## 1. Executive Summary & Verification Objective

The quant research engine has been frozen to **Proposal C-2** (34/33/33 equal sizing, dual-clock stops, +18% uncapped trailing ATR 3.0, QQQ cash proxy). All legacy `C1-M2` configurations (50/30/20, intraday -5% tick stop, ATR 2.5, standalone Kijun exit) have been superseded.

The purpose of this handoff is for Cursor to execute an **unbiased, independent audit** of the codebase to verify:
1. That the **C-2 bundle architecture** is 100% intact across Python backend, risk daemons, SQLite persistence, and frontend modules.
2. That **no legacy C1-M2 constants or broken behaviors** (especially the intraday -5% tick stop or standalone Kijun exits) inadvertently remain active.
3. That the **investment prospectus** and **dashboard DIVs** reflect the C-2 model accurately and modernly.
4. That all automated regression suites pass without a single failure or warning.

---

## 2. Frozen C-2 Single Source of Truth (SSOT) Matrix

Compare every component against this specification. **Zero deviation is permitted.**

| Parameter | C-2 Production Specification (SSOT) | Implementation Target | Legacy C1-M2 (Obsolete) |
|:---|:---|:---|:---|
| **Bull Satellite Slots** | **3 Slots: 34% / 33% / 33%** | `constants.py`, `ui.js`, `position_sizer.py` | 50% / 30% / 20% |
| **Bear Satellite Slots** | **2 Slots: 25% / 25%** | `constants.py`, `ui.js` | 25% / 25% |
| **EOD Hard Stop** | **−7.0% (`close/last <= entry * 0.93`)**<br>*Evaluated strictly during 15:50–16:00 ET closing window* | `portfolio_guardian.py`, `constants.py` | None (intraday tick stop used) |
| **Emergency Stop** | **−10.0% (`last <= entry * 0.90`)**<br>*Monitored 10s intervals during regular trading hours* | `portfolio_guardian.py`, `constants.py` | −5.0% last-price poll |
| **Intraday -5% Stop** | **PERMANENTLY DELETED (DO NOT RE-ADD)** | `portfolio_guardian.py` | Fired on intraday low (alpha killer) |
| **Trailing Latch** | **Armed at +18.0% peak gain** | `trailing_stop.py`, `constants.py` | +15.0% peak gain |
| **Trailing Floor** | `max(Kijun-26, peak − 3.0 × ATR14)` | `trailing_stop.py`, `constants.py` | `max(Kijun-26, peak − 2.5 × ATR14)` |
| **Standalone Kijun Exit** | **PERMANENTLY DISABLED** (`STANDALONE_KIJUN_EXIT_ENABLED = False`) | `trailing_stop.py` | Active anytime below Kijun |
| **Trend Entry Gate** | `QQQ Close >= SMA20` (prior confirmed daily bar) | `autopilot_trader.py` | Not enforced |
| **Alpha Entry Gate** | `Stock Composite RS >= QQQ Composite RS` | `autopilot_trader.py`, `conviction_engine.py` | Watchlist conviction only |
| **Cash Proxy Sleeve** | **1.0x QQQ Core Overlay** (unallocated cash &rarr; QQQ)<br>Conditional 1.5x QLD boost when `SPY >= SMA200` & `VIX < 20` | `cash_proxy.py`, `autopilot_trader.py` | QQQ/QLD with thrash bugs |
| **Pre-Trade Guardrail** | Bull max single asset: **39.0%** (34% + 5%p buffer)<br>Bear max single asset: **30.0%** (25% + 5%p buffer) | `order_guardrail.py` | 55% / 35% / 25% |
| **Whole-Share Sizing** | Floor division `int(allocation // price)`, residual cash remains in liquid USD balance | `position_sizer.py`, `kis_broker.py` | Fractional simulated |
| **Frontend Token Bound** | Every modular file under `frontend/` **strictly < 600 lines** | `frontend/js/ui.js` (currently 495 lines) | Failed at 613 lines |

---

## 3. Scope of Recent Antigravity Updates

The following items were recently modified and should be targeted for verification:

1. **Investment Prospectus Set (PPM/KIID)**:
   - Created: `INVESTMENT_PROSPECTUS_C2_KO.md`, `INVESTMENT_PROSPECTUS_C2_KO.html` (Modern Korean prospectus).
   - Created: `INVESTMENT_PROSPECTUS_C2.md`, `INVESTMENT_PROSPECTUS_C2.html` (Modern English prospectus).
   - Mirrored: `frontend/INVESTMENT_PROSPECTUS_C2_KO.html` and `frontend/INVESTMENT_PROSPECTUS_C2.html`.
   - Updated: `server.py` routes `/docs/prospectus`, `/docs/prospectus-ko`, `/docs/prospectus-en` to serve C-2 by default with backward-compatibility fallbacks.
2. **Dashboard Markup & DIV Inspection (`frontend/index.html`)**:
   - Header: `btnProspectus` link updated to `/static/INVESTMENT_PROSPECTUS_C2_KO.html`, label updated to `투자설명서 (C-2)`.
   - Visualizer Overlay Bar: Legacy ID `c1m2OverlayBar` renamed to `c2OverlayBar`.
   - Verified DIV consistency: 1-line macro strip, KPI 4-cards, C-2 slot allocator (34/33/33), Top Picks (#1 & #2), Active Portfolio table (`STOP (-7%/-10%)`, `TRAILING`), Strategy Protocol card (C-2 4대 헌법), Chart/Decoder, Execution logs.
3. **Frontend Modularity & Code Compaction (`frontend/js/ui.js`)**:
   - Refactored redundant string templates and DOM helpers using clean ES6.
   - Compressed from **613 lines down to 495 lines**, resolving the line count violation in `test_phase5_4_kis_modular.py`.

---

## 4. Verification Procedures & Test Commands for Cursor

Cursor should execute the following verification tiers in order:

### Tier 1: Automated Pytest Regression Suite (64/64 Must Pass)

Run all three primary test suites synchronously:

```bash
# 1. Frontend modularity, APIRouters & KIS broker gateway (11 tests)
python -m pytest tools_and_tests/test_phase5_4_kis_modular.py -v

# 2. C-2 bundle architecture, dual-clock stops & entry gates (24 tests)
python -m pytest tools_and_tests/test_c2_bundle_architecture.py -v

# 3. Track 3 staging readiness, concurrency, rate limits & state migration (29 tests)
python -m pytest tools_and_tests/test_track3_staging_readiness.py -v
```

**Passing Criteria:**
- Total 64 tests collected.
- **64 passed, 0 failed, 0 errors.**
- Execution time: ~30 seconds total.

---

### Tier 2: Frontend Token Bounds & Modularity Verification

Check that no modular frontend file violates the `< 600 lines` token efficiency limit:

```bash
python -c "
import os
frontend_dir = 'frontend'
files = [
    'index.html',
    os.path.join('css', 'terminal.css'),
    os.path.join('js', 'api.js'),
    os.path.join('js', 'chart.js'),
    os.path.join('js', 'constants.js'),
    os.path.join('js', 'decoder.js'),
    os.path.join('js', 'ui.js'),
    os.path.join('js', 'websocket.js'),
]
for rel in files:
    p = os.path.join(frontend_dir, rel)
    with open(p, 'r', encoding='utf-8') as f:
        lines = len(f.readlines())
    print(f'{rel:25s}: {lines:4d} lines  [PASS]' if lines < 600 else f'{rel:25s}: {lines:4d} lines  [FAIL - EXCEEDS 600]')
    assert lines < 600, f'Line limit violated: {p} has {lines} lines'
print('\nALL FRONTEND MODULES SATISFY < 600 LINES CONSTRAINT.')
"
```

---

### Tier 3: SSOT Cross-Check (Python vs JS Constants)

Verify that Python SSOT constants and JS constants are in exact lockstep:

```bash
python -c "
import re
from al_sangmoo.core.constants import (
    STOP_LOSS_PCT, EOD_STOP_LOSS_PCT, EMERGENCY_STOP_LOSS_PCT,
    TAKE_PROFIT_PCT, ATR_MULTIPLIER, STANDALONE_KIJUN_EXIT_ENABLED,
    SLOT_WEIGHTS_BULL, SLOT_WEIGHTS_BEAR
)

assert EOD_STOP_LOSS_PCT == -0.07, f'EOD stop wrong: {EOD_STOP_LOSS_PCT}'
assert EMERGENCY_STOP_LOSS_PCT == -0.10, f'Emergency stop wrong: {EMERGENCY_STOP_LOSS_PCT}'
assert TAKE_PROFIT_PCT == 0.18, f'Take profit wrong: {TAKE_PROFIT_PCT}'
assert ATR_MULTIPLIER == 3.0, f'ATR multiplier wrong: {ATR_MULTIPLIER}'
assert STANDALONE_KIJUN_EXIT_ENABLED is False, 'Standalone kijun exit must be False'
assert SLOT_WEIGHTS_BULL == [0.34, 0.33, 0.33], f'Bull slots wrong: {SLOT_WEIGHTS_BULL}'
assert SLOT_WEIGHTS_BEAR == [0.25, 0.25], f'Bear slots wrong: {SLOT_WEIGHTS_BEAR}'

with open('frontend/js/constants.js', 'r', encoding='utf-8') as f:
    js_text = f.read()

assert 'STOP_LOSS_PCT = -0.07' in js_text
assert 'EMERGENCY_STOP_LOSS_PCT = -0.10' in js_text
assert 'TAKE_PROFIT_PCT = 0.18' in js_text
assert 'ATR_MULTIPLIER = 3.0' in js_text
assert '[0.34, 0.33, 0.33]' in js_text
assert '[0.25, 0.25]' in js_text
print('SSOT LOCKSTEP VERIFIED: Python core and frontend JS constants are identical.')
"
```

---

### Tier 4: FastAPI Server & Prospectus Route Live Verification

Verify that FastAPI cleanly boots, serves the dashboard index, and serves the new C-2 prospectus documents:

```bash
python -c "
import asyncio
from server import app
from tools_and_tests.test_phase5_2_concurrency import asgi_request

async def test_server_routes():
    # 1. Root dashboard
    s, h, b = await asgi_request(app, 'GET', '/')
    assert s == 200, f'Root dashboard status: {s}'
    assert 'R QUANT TERMINAL' in b.decode('utf-8')
    assert 'c2OverlayBar' in b.decode('utf-8')
    assert 'INVESTMENT_PROSPECTUS_C2_KO.html' in b.decode('utf-8')
    print('1. Root Dashboard (/) OK - C-2 elements confirmed.')

    # 2. Korean Prospectus
    s, h, b = await asgi_request(app, 'GET', '/docs/prospectus-ko')
    assert s == 200, f'Prospectus KO status: {s}'
    ko_text = b.decode('utf-8')
    assert 'C-2' in ko_text
    assert '34/33/33' in ko_text
    assert '32.76%' in ko_text
    print('2. /docs/prospectus-ko OK - C-2 Korean PPM confirmed.')

    # 3. English Prospectus
    s, h, b = await asgi_request(app, 'GET', '/docs/prospectus-en')
    assert s == 200, f'Prospectus EN status: {s}'
    en_text = b.decode('utf-8')
    assert 'C-2' in en_text
    assert '34/33/33' in en_text
    print('3. /docs/prospectus-en OK - C-2 English PPM confirmed.')

asyncio.run(test_server_routes())
"
```

---

### Tier 5: Critical Risk & Edge-Case Checks

Verify that known failure modes from earlier phases do not regress:

1. **Cash Proxy Exemption from Satellite Exits**:
   - Verify `portfolio_guardian.py`: holdings in `CASH_PROXY_TICKERS` (`QQQ`, `QLD`) must be skipped during stop-loss loops (`continue`).
2. **Backward Compatibility on Legacy DB Lots**:
   - Verify `test_legacy_open_lot_preserves_minus_5_ticket_stop` in `test_track3_staging_readiness.py`: existing open lots with ticket stop at 95.0 are NEVER forcibly overwritten with 93.0 or 90.0 during `_persist_mark`.
3. **No KST Hardcoding for EOD Window**:
   - Verify `market_time.py` uses `America/New_York` timestamps for the 15:50–16:00 ET closing window, correctly handling US Daylight Saving Time (EDT vs EST).
4. **Token Stampede Avoidance**:
   - Verify `_auth_lock` and `EGW00133` reuse existing valid cached token without spamming KIS OAuth.

---

## 5. Hard Constraints & Red Lines (What NOT to Do)

- **DO NOT** revert constants to C1-M2 (`-0.05`, `50/30/20`, `2.5 ATR`).
- **DO NOT** re-enable standalone Kijun exit (`STANDALONE_KIJUN_EXIT_ENABLED` must remain `False`).
- **DO NOT** re-introduce intraday `-5%` stop evaluations in `portfolio_guardian.py`.
- **DO NOT** exceed 600 lines in any file under `frontend/`.
- **DO NOT** hardcode KST cron times for market events (always use `America/New_York`).

---

## 6. Audit Verdict Template for Cursor

Upon executing the above verification tiers, Cursor should conclude its response using the following verdict format:

```markdown
## Cursor C-2 Verification Audit Summary

- [ ] Tier 1: Pytest Regression Suite (64/64 Passed)
- [ ] Tier 2: Frontend Token Bounds (< 600 lines check)
- [ ] Tier 3: SSOT Lockstep (Python vs JS Constants)
- [ ] Tier 4: FastAPI Server & Prospectus Routes (200 OK)
- [ ] Tier 5: Critical Risk & Edge-Case Safeguards

**Audit Verdict:** [PASSED / FAILED]
**Key Observations:** [Brief 2-3 sentence technical summary of findings]
**Recommendation for Deployment:** [PROCEED TO STAGING / ACTION REQUIRED]
```
