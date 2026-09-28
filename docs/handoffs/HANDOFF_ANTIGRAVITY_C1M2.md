# Handoff: C1-M2 Dashboard / Cash Proxy → Antigravity

**Date:** 2026-09-10 (KST)  
**From:** Cursor Agent (Phase 3 UX + Cash Proxy sleeve patch)  
**Branch:** `cursor/cloud-agent-1788871273215-8njl9`  
**Remote:** `origin/cursor/cloud-agent-1788871273215-8njl9`  
**Latest commits:**
- `5f6b9d9` — Phase 3 SSOT UX (slot count / Quick Buy QTY / MSI / Composite RS)
- `6886cb0` — Cash Proxy sleeve vs Guardian satellite-exit separation
- `687b58c` — Broker portfolio reconciliation + APP chart resolver + pending-fill ledger safety

---

## 1. What this system is

Production engine **C1-M2** for Al-Sangmoo quant terminal:

| Rule | Spec |
|------|------|
| Bull slots | 50 / 30 / 20 (3 satellites) |
| Bear slots | 25 / 25 (2 satellites) |
| Idle cash | QQQ Cash Proxy overlay |
| Leverage | 1.5x via QQQ+QLD when `SPY ≥ SMA200` **AND** `VIX < 20` |
| Hard stop | −5.0% |
| Trailing | After +15% peak: `max(Kijun-26, peak − 2.5×ATR)` |

**Strict frontend constraint:** every modular file under `frontend/` (`ui.js`, `websocket.js`, `api.js`, `chart.js`, `decoder.js`, `terminal.css`, `index.html`) must stay **&lt; 600 lines** (`tools_and_tests/test_phase5_4_kis_modular.py`).

---

## 2. Completed work (this thread)

### Phase 3 — UX / SSOT visualization (`5f6b9d9`)

1. **Slot counter bug** — QQQ/QLD no longer inflate satellite slot count.  
   - Summary uses `sats.length` only (e.g. 2 sats + QLD → `2 / 2` or `2 / 3`).  
   - Grid still renders proxy cards with `[CASH PROXY]`.  
   - Files: `frontend/js/ui.js` (`renderSlotVisualizer`, `renderKPIs`).

2. **Quick Buy QTY** — Auto-fills `qbQty` from Top Pick / Runner-up `sizing.shares`, else `floor(next_slot_cap / price)`.  
   - Helpers in `frontend/js/constants.js` (`resolveQuickBuyQty`).

3. **MSI KPI** — Card shows `MSI: {score} PT · {stance}` from `macro.msi_score` / `macro_climate.msi_score`.

4. **Decoder card 4** — Title `4. COMPOSITE RS`; prefers `composite_rs`, falls back to `rs_3m`. Feed also writes `composite_rs` in `generate_dashboard_feed.py` / scoring.

Cache bust query: frontend assets at `?v=4.3.3`.

### Critical risk patch — Cash Proxy thrash (`6886cb0`)

**Symptom:** EXECUTION LOG showed QQQ/QLD (and sometimes CRWD) flipping buy/sell every few seconds overnight (esp. 2026-09-10 ~03:48–03:57 KST).

**Root cause (two bugs):**

1. **Guardian applied satellite exit rules (−5% / kijun / trailing) to QQQ & QLD.**  
   Brief kijun dips on ETFs → sell → Autopilot repark buy → loop.

2. **Autopilot `_ensure_cash_proxy_parked` only executed BUY actions.**  
   Plan correctly emitted `DELEVERAGE_TO_1X_QQQ_CORE` (SELL QLD) when leverage off, but SELLs were skipped → **QLD leftover while UI showed LEV 1.0x**.

**Fix (do not “abandon” automation — separate engines):**

| Engine | Owns |
|--------|------|
| **Guardian** | Satellite exits only (−5% / kijun / trailing). **Skips** QQQ/QLD for those rules. |
| **Cash Proxy sleeve** | Still fully automatic: park idle NAV, free cash for satellite entries, **delever QLD→QQQ on 1.0x**, 1.5x mix when allowed. |

Implementation notes:
- `portfolio_guardian.py`: early `continue` for `is_proxy_ticker`; new `_align_cash_proxy_sleeve()` (delever immediate; other sleeve actions throttled ~60s).
- `autopilot_trader.py`: `_ensure_cash_proxy_parked` executes **SELL then BUY**; always fetches QLD price; scheduler aligns sleeve even when satellite slots are full.
- Tests: `tools_and_tests/test_cash_proxy_sleeve_guardian.py` (32 related tests green with autopilot/guardrail suite).

### Post-patch integration correction (`687b58c`)

The first sleeve patch exposed two independent live integration defects:

1. Guardian called nonexistent `default_kis_broker.check_sync()`, so broker→SQLite calibration was silently skipped. It now calls the serialized domain `check_sync(auto_calibrate=True)` implementation.
2. KIS `submitted` means order accepted, not filled. Guardian/Autopilot no longer close or add local lots on acceptance; they suppress duplicate orders while reconciliation waits for the broker balance to confirm the fill.
3. Dashboard startup and refresh fetch `/api/portfolio?reconcile=true`, so the Active Portfolio is replaced with broker-reconciled SSOT.
4. `APP` was resolved to `AAPL` because `"APP"` matched `"Apple Inc"` before exact ticker lookup reached AppLovin. Exact ticker resolution now runs as a separate first pass.

---

## 3. Verification already run

| Check | Result |
|-------|--------|
| `pytest tools_and_tests/test_phase5_4_kis_modular.py` | 11 passed (&lt;600 lines) |
| `python tools_and_tests/test_modular_2tier_architecture.py` | Feed **44.0 KB** |
| `node tools_and_tests/verify_dashboard_browser.js` | Pass (slot sync, MSI, COMPOSITE RS, 0 console errors) |
| `node tools_and_tests/verify_slot_allocator_edge_cases.js` | Pass (3/3 with QLD excluded from count; QTY autofill) |
| Regression pytest batch (tests/ + phase1/sync/ssot/msi/phase4) | 50 passed |
| Cash proxy sleeve tests | Pass |

**Ops note:** After pull, **restart `server.py`** so Guardian/Autopilot daemons load `6886cb0`. Hot-reload of Python daemons is not guaranteed.

---

## 4. Current live / ledger context (as of handoff)

- Broker and SQLite were reconciled to **QLD 8 + DELL 4 + CVX 9** with zero discrepancies.
- Guardian is ON; Autopilot is OFF after final verification restart.
- UI LEV can show **1.0x** while QLD is held before the market opens. The sleeve submits QLD delever only in the valid order window, then keeps the local QLD lot until broker balance confirms the fill.
- Live market probe during session sometimes showed **1.5x allowed** (SPY &gt; SMA200, VIX &lt; 20). Dashboard LEV vs live Yahoo can diverge if feed gauges are stale — treat feed `macro.leverage` as UI SSOT, verify with `evaluate_dynamic_leverage` if debugging.

Do **not** commit without need:
- `data/charts/*.json`, `quant_trades.db`, prospectus HTML/PDF copies, `dashboard_verification.png` (runtime artifacts).

---

## 5. Suggested next work for Antigravity

Priority order:

1. **Smoke live sleeve during market hours**  
   - With Guardian ON: confirm QLD sell is submitted when `leverage_mode=False`, broker reconciliation removes QLD after fill, then QQQ parks.
   - Confirm EXECUTION LOG no longer thrash-loops QQQ/QLD on kijun alone.

2. **Optional hardening (if thrash remains on satellites)**
   - Re-entry cooldown after Guardian exit of same ticker (CRWD was also flipping in the same window).  
   - Inspect broker day-book cancellation/partial-fill states before retrying after the current pending-order cooldown.

3. **LEV badge vs holdings consistency**  
   - UI: if QLD held but `leverage_mode=False`, show warning chip e.g. `LEV: 1.0x · QLD PENDING DELEVER` until sleeve catches up.

4. **Feed composite_rs freshness**  
   - Ranked list may still show `composite_rs: null` until next full `generate_dashboard_feed` rebuild; decoder falls back to 3M RS correctly.

5. **Do not regress**  
   - Modular &lt;600 lines.  
   - Satellite slot count must ignore QQQ/QLD.  
   - Never put kijun/−5% exits back on QQQ/QLD.

---

## 6. Key files

```
frontend/js/ui.js                 # slots, KPIs, Quick Buy console
frontend/js/constants.js          # MSI helpers, resolveQuickBuyQty
frontend/js/decoder.js            # COMPOSITE RS card
frontend/index.html               # MSI badge markup, card title
al_sangmoo/domain/risk/cash_proxy.py
al_sangmoo/domain/risk/portfolio_guardian.py
al_sangmoo/domain/risk/autopilot_trader.py
generate_dashboard_feed.py
tools_and_tests/verify_dashboard_browser.js
tools_and_tests/verify_slot_allocator_edge_cases.js
tools_and_tests/test_cash_proxy_sleeve_guardian.py
```

---

## 7. Quick commands

```powershell
$env:PYTHONUTF8=1
pytest tools_and_tests/test_cash_proxy_sleeve_guardian.py tools_and_tests/test_phase5_4_kis_modular.py -q
python tools_and_tests/test_modular_2tier_architecture.py
node tools_and_tests/verify_dashboard_browser.js
node tools_and_tests/verify_slot_allocator_edge_cases.js
```

Server: `python server.py` → `http://127.0.0.1:8000/`

---

## 8. One-line summary for Antigravity

**C1-M2 Phase 3 UX, cash-proxy separation, broker reconciliation, pending-fill safety, and APP chart resolution are integrated; live state is QLD/DELL/CVX with zero broker/local discrepancies, Guardian ON, Autopilot OFF.**
