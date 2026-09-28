# Handoff: C-2 Production Freeze → Antigravity

**Date:** 2026-09-12 (KST)  
**From:** Cursor (quant review of `HANDOFF_QUANT_STRATEGY_REVIEW_AND_SELECTION.md`)  
**To:** Antigravity  
**Status:** **DECISION LOCKED — stop parameter search**  
**Live code:** `al_sangmoo/` is still **C1-M2**. Do not hot-swap constants until the checklist in §6 is done on paper/VPS.

Related visual review (optional): `C:\Users\kdw58\.cursor\projects\d-R\canvases\quant-strategy-selection.canvas.tsx`

---

## 0. What you must do / must not do

### Do

1. Freeze **Proposal C-2** as the research champion and the only candidate for a future production cutover.
2. Treat C-2 as a **bundle** (slots + dual-clock stops + trail + entry gates + no standalone kijun exit). Changing two constants is not C-2.
3. If implementing: paper / KIS VPS first, **new entries only**. Existing C1-M2 lots keep their ticket stops.
4. If more research: walk-forward + delist-aware prices. Not another ATR 0.1 grid.

### Do not

1. Do **not** ship **C2-1** (ATR 2.8) or **C2-2** (2-slot 50/50 + ATR 2.8).
2. Do **not** resume C → C-2 → C2-n “beat the last champion” loops on the same 2019-09-30 ~ 2026-08-28 sample.
3. Do **not** claim the 543-name OHLCV panel is 100% survivorship-free.
4. Do **not** schedule EOD exits at a hardcoded `05:45` KST cron (DST-wrong; see §7).
5. Do **not** leave Guardian’s intraday **-5% last-price** loop on if you turn on C-2’s EOD -7%. Both will fire; the -5% loop kills the whole point of C-2.

---

## 1. Verdict (one paragraph)

N-PORT PIT + switching satellite exits from **intraday low -5%** to **EOD wider stops** (Proposal C family) is real work. Everything after C-2 — ATR 2.8, 2-slot 50/50, tiered ratchets — is in-sample peak hunting on ~114 trades with **zero walk-forward**. Freeze **C-2**. Haircut live expectations to the **C / C-1 cluster (~26–31% 7y CAGR)**, not 32.76%.

| Candidate | Spec | 7y CAGR / MDD / PF / N | Call |
|---|---|---|---|
| **C-2** | 34/33/33, EOD -7% / emerg -10%, +18% / ATR **3.0** | 32.76% / -38.85% / 2.15 / 114 | **FREEZE** |
| C2-1 | same slots/stop, ATR **2.8** | 33.30% / -36.81% / 2.24 / 114 | Reject — cliff edge |
| C2-2 | **50/50**, ATR **2.8** | 34.72% / -36.64% / 2.55 / 75 | Reject — interaction needle |
| C1-M2 (live) | 50/30/20, intraday low -5%, +15% / ATR 2.5 | 15.41% / -34.47% / 1.71 / 199 | Keep until cutover; then replace satellite exits |

Window: 2019-09-30 ~ 2026-08-28 (6.9y). Nested 3y (2023-08-31 ~ 2026-08-28) is **in-sample**, not OOS. Friction: 10 bps slip + 8 bps fee. Idle cash still uses QQQ/QLD overlay (1.5x when SPY ≥ SMA200 and VIX < 20) — alpha vs QQQ B&H is **not** pure stock alpha.

---

## 2. What was meaningful vs overfitting

### Keep (structural)

1. **SEC N-PORT PIT universe** (`filing_date ≤ as-of`). Correct research infrastructure. 11 ETFs, 308 filings, 609 unique tickers.
2. **Intraday -5% is an alpha killer** on this book. C1-M2: 134 / 199 trades stopped by the daily low. EOD wider stop + RS ≥ QQQ + QQQ ≥ SMA20 is what first beat QQQ (Proposal C 26.27%).
3. **Equal 34/33/33 vs 40/30/30** is a small, coherent increment (C-1 31.62% → C-2 32.76%, same 114 trades). Risk hygiene, not a new factor.

### Discard (snooping)

Same 6.9y tape, ~50 specs (`A/B/C/D`, `C-1..C-4`, `C2-1..C2-4`, `run_c_grid`, `explore_c2_levers{,_2,_3}`, cliff tests). No walk-forward.

Smoking-gun cross:

| Spec | 7y CAGR |
|---|---|
| 3-slot ATR 3.0 (**C-2**) | **32.76%** |
| 2-slot ATR 3.0 | **32.47%** (worse than C-2) |
| 3-slot ATR 2.8 (C2-1) | 33.30% |
| 2-slot ATR 2.8 (**C2-2**) | **34.72%** |
| 2-slot ATR 2.7 | 28.11% |
| 1-slot ATR 2.8 | 44.95% |

2-slot concentration is **not** robust. C2-2 exists only as **2-slot × ATR 2.8**. Idle QLD 1.5x and the 2023–26 AI path explain the print.

### Sensitivity (do not retune into these)

**ATR on 3-slot 34/33/33, EOD -7%, trail +18%:**

| ATR | 7y CAGR | 7y MDD |
|---|---|---|
| 2.6 | 26.33% | -38.12% |
| 2.7 | **25.53%** | -36.81% |
| 2.8 (C2-1) | 33.30% | -36.81% |
| 2.9 | 32.89% | -38.85% |
| **3.0 (C-2)** | **32.76%** | -38.85% |

Plateau is **2.8–3.0**. Cliff is **2.7 → 2.8**. C-2 (3.0) is inside the plateau; C2-1 (2.8) sits on the edge.

**EOD stop on C-2 chassis (ATR 3.0):**

| Stop | 7y CAGR |
|---|---|
| -6.5% | 26.50% |
| **-7.0% (C-2)** | **32.76%** |
| -7.5% | 28.42% |
| -8.0% | 26.90% |

**-7.0% is a local peak, not a plateau.** Live fills already jitter ±tens of bp. Do not grid 6.8 / 7.2.

C-3/C-4 beat C-2 on the nested 3y window and lose on 7y. That is not confirmation.

---

## 3. Corrections to the previous handoff

The inbound `HANDOFF_QUANT_STRATEGY_REVIEW_AND_SELECTION.md` overstated cleanliness and mixed two clocks. Fix these if you quote it:

| Previous claim | Fact |
|---|---|
| 543-name book is 100% unbiased | **609** N-PORT tickers; **81 missing** from `nport_full_530_ohlcv.pkl` (ABMD, ALXN, ATVI, CELG, CERN, DISH, ETFC, FEYE, …). Universe is PIT; **price tape is still survivorship-biased**. OHLCV dict has 543 keys including QQQ/QLD/SPY/sector ETFs (~528 equities). |
| “서머타임 기준 한국 05:45–05:59” = US close | **Wrong.** EDT 16:00 ET = **05:00 KST**. Last 15 min: **04:45–05:00 KST (EDT)** / **05:45–06:00 KST (EST)**. |
| Proposal C 7y 26.87% (walkthrough §2) vs 26.27% (C-family table) | Two engines/universes. **C-2 was tuned against 26.27% / MDD -39.19%.** Use that lineage. |
| C-2 is a robust plateau on stop **and** ATR | ATR 2.8–3.0 is a plateau. Stop **-7.0% is a needle**. |
| C2-2 extra return is “drop 3rd slot, keep leaders” | 2-slot + ATR 3.0 **underperforms** C-2. Extra return is the ATR 2.8 interaction + QLD beta. |
| Cliff script C2-2 reprint 35.01% / -35.84% | `test_parameter_cliff.py` omitted `emergency_stop_pct=-0.10`. Canonical C2-2 remains **34.72% / -36.64%**. |

Early-sample look-ahead in `SECNportUniverseProvider.get_etf_candidates_at_date`: if `target_date` is before the first filing, it **falls back to the oldest filing**. Disable that fallback for PIT purity.

Same-bar trail arming: `run_variant_book` updates `peak_high` with **today’s high** then evaluates EOD exit on **today’s close**. Document this; do not “fix” it silently if you want to match frozen C-2 prints.

Emergency fill: backtest fills at **emergency price**, not the low/open. Overnight gap -18% is **not** a -10% live fill. Size as if a 34% slot × -50% gap ≈ **-17% NAV**.

---

## 4. Frozen C-2 spec (SSOT for any future cutover)

Copy this table into code comments / prospectus. Do not invent nearby values.

| Lever | C-2 value | Live C1-M2 today |
|---|---|---|
| Bull slots | **0.34 / 0.33 / 0.33** (3) | 0.50 / 0.30 / 0.20 |
| Bear slots | **0.25 / 0.25** (2) | same |
| Hard stop | **EOD close ≤ entry × 0.93** | last price ≤ entry × 0.95, polled ~10s |
| Emergency | **intraday low / last ≤ entry × 0.90** (RTH only) | none (the -5% loop *is* the emergency) |
| Trail arm | peak gain **≥ +18%** | +15% |
| Trail floor | `max(Kijun-26, peak − 3.0 × ATR14)` | `max(Kijun-26, peak − 2.5 × ATR14)` |
| Standalone kijun exit | **OFF** (kijun is trail floor only) | **ON** whenever not trailing |
| Entry trend gate | **QQQ close ≥ SMA20** (prior bar) | not in C-2 form |
| Entry alpha gate | **stock composite RS ≥ QQQ composite RS** (prior bar) | conviction rank / watchlist |
| Entry timing | prior-bar signal → **next open** | Autopilot already 09:30–10:00 ET on confirmed EOD feed — **keep** |
| Idle overlay | QQQ core; QLD to 1.5x gross when bull + VIX&lt;20 | already in live sleeve — **keep**, Guardian must still skip proxy tickers |
| Friction (research) | 10 bps + 8 bps | live KIS spread/fee differ |

Backtest implementation to clone, not the earlier CVariantSpec docstring (that file’s header still describes a **different** C-1/C-2/C-3/C-4 from a prior experiment):

- Engine: `C:\Users\kdw58\.gemini\antigravity\brain\8b3fcff7-bc56-4aa4-93f2-efc7577272b7\scratch\backtest_c_variants.py` (`CVariantSpec` + `decide_variant_exit` + `run_variant_book`)
- Frozen runner: `...\scratch\test_four_c_candidates.py` → spec `'C-2 (Equal Sizing + Agile Stop: 34/33/33, -7.0% Stop)'`
- Universe: `research_and_backtests/pit_sec_nport_universe.py` + `data/universe/sec_nport_pit_holdings.pkl`
- Prices: `data/backtest_cache/nport_full_530_ohlcv.pkl`

Repo copies (may lag scratch): `research_and_backtests/pit_sec_nport_backtester.py`, `pit_sec_nport_universe.py`, `fetch_sec_nport_holdings.py`.

---

## 5. Live files you will touch (when implementing — not now unless user asks)

Production is untouched C1-M2. Cutover is a **bundle** across:

| File | C1-M2 today | C-2 change |
|---|---|---|
| `al_sangmoo/core/constants.py` | `STOP_LOSS_PCT=-0.05`, `TAKE_PROFIT_PCT=0.15`, `ATR_MULTIPLIER=2.5`, `SLOT_WEIGHTS_BULL=[0.50,0.30,0.20]` | Split **EOD stop -0.07** vs **emergency -0.10**. `TAKE_PROFIT_PCT=0.18`, `ATR_MULTIPLIER=3.0`, `SLOT_WEIGHTS_BULL=[0.34,0.33,0.33]`. Update module docstring / prospectus pointer. |
| `al_sangmoo/domain/risk/order_guardrail.py` | Bull caps 0.55 / 0.35 / 0.25 | **0.39 / 0.38 / 0.38** (target + 5%p buffer). Docstring ranks 1–3. |
| `al_sangmoo/domain/risk/trailing_stop.py` | Hard -5%; always-on kijun close exit; trail +15% / 2.5 ATR | Dual clock; **remove standalone kijun** (or keep it and **do not claim C-2**). |
| `al_sangmoo/domain/risk/portfolio_guardian.py` | 10s last-price vs -5%; LIMIT 00 at `last*0.995`; skip if `not is_market_open_for_orders` | RTH: emergency -10% only. **Last ~10–15 min ET:** EOD -7% job. After 16:00 ET currently **skips** sells — a 16:00:05 poll misses the session. |
| `al_sangmoo/domain/risk/autopilot_trader.py` | EOD feed → next open 09:30–10:00 ET | Keep entry clock. Add QQQ SMA20 + RS≥QQQ gates or live book ≠ test book. |
| `al_sangmoo/domain/quant/conviction_engine.py`, `position_sizer.py`, `macro.py` | Read `SLOT_WEIGHTS_*` | Should follow constants; verify bear 2-slot unchanged. |
| `al_sangmoo_daily_bot.py`, `generate_dashboard_feed.py`, frontend slot UI | Displays -5% / 50-30-20 | Copy SSOT or the dashboard lies. |
| Tests | `tools_and_tests/test_c1_m2_live_allocation.py` and guardrail/trailing suites | Rewrite expected caps/stops; do not leave C1-M2 numbers green by accident. |
| Docs | `INVESTMENT_PROSPECTUS_C1_M2.md` if present | New C-2 prospectus; do not silently rename C1-M2. |

Frontend modular files must stay **&lt; 600 lines** (`tools_and_tests/test_phase5_4_kis_modular.py`).

---

## 6. Cutover procedure (ordered)

Do this only when the user explicitly asks to implement. Until then, live stays C1-M2.

1. **Paper / KIS VPS** for at least one earnings season with the **full** C-2 bundle (gates + dual-clock + ATR 3.0 + 34/33/33).
2. **Do not rewrite** `stop_loss_price` on open C1-M2 lots. New entries get C-2 tickets. Old lots exit under their original -5% / +15% / 2.5 ATR / kijun rules.
3. Turn **off** Guardian’s -5% last-price satellite stop **in the same deploy** as EOD -7%. Partial deploy = still C1-M2 damage with a new label.
4. Explicit human decision on **kijun-anytime exit**: C-2 backtest = off. If product wants it on, that is a **new** spec, re-backtest, not a silent merge.
5. Whole-share rounding on KIS (`qty: int`). 34/33/33 will not hold on small NAV; document residual cash → QQQ proxy.
6. After deploy: one-page runbook (DST cron, half-days, EGW00201 retry, unconfirmed SELL ledger).

---

## 7. KIS EOD / emergency — implementation constraints

There is **no Nasdaq MOC** on KIS overseas. `place_order` `ORD_DVSN`: `"00"` limit, `"01"` market (`al_sangmoo/infrastructure/brokers/kis_broker.py`). Backtest close fills **cannot** be reproduced. Live EOD is “last/NBBO in the final minutes,” then hope.

### Clocks (use `America/New_York`, not a KST constant)

| Session | Regular close | Last 10 minutes (job window) | Last 10 min in KST |
|---|---|---|---|
| EDT (summer) | 16:00 ET | 15:50–16:00 ET | **04:50–05:00 KST** |
| EST (winter) | 16:00 ET | 15:50–16:00 ET | **05:50–06:00 KST** |
| US half-day | **13:00 ET** | 12:50–13:00 ET | Do **not** run the 15:50 ET job |

`is_us_regular_hours` in `al_sangmoo/core/market_time.py` is inclusive through **16:00**, exclusive after. Guardian skip-if-closed means **16:00:05 = no sell that day**.

### Suggested live recipe

1. Emergency: keep ~10s poll in RTH; fire only if last (or bid) ≤ entry × 0.90. Marketable limit `last * 0.995` (current Guardian pattern) or market `01`.
2. EOD: 15:50 ET quote; if last ≤ entry × 0.93 **and** not already trailed out, send SELL; confirm via day-order TR; **one** retry on `EGW00201`.
3. Token: existing `_auth_lock` / 1-per-minute `EGW00133`. Do not stampede `tokenP` at 15:50.
4. TPS: limiter is 3.5 rps. 15:50 must not coincide with sleeve rebalance + three satellite quotes + three sells without serialization (`ORDER_MUTEX` / proxy mutex already exist — use them).
5. Gap-through: if open is already through -10%, you get the open, not -10%. Do not model otherwise.
6. Extended hours: Guardian is RTH-only by design. Do not silently enable pre/after for C-2 EOD (that is not the backtest).

---

## 8. Allowed research after freeze (optional)

Only if the user wants more science. **Not** required for the verdict.

1. **Walk-forward:** train through 2022-12-31, freeze C-2, test 2023-01-02 ~ 2026-08-28. If 7y in-sample 32.76% collapses toward QQQ, that is the honest live prior.
2. **Delist tape:** fill the **81** missing N-PORT names (CRSP/Norgate/QI). Re-run **frozen C-2 only** — no re-optimization.
3. **Disable** first-filing fallback in `get_etf_candidates_at_date`.
4. **Ablate QLD:** C-2 with overlay off vs on, to separate stock alpha from 1.5x idle beta.
5. Do **not** add C2-5 / ATR 2.85 / 36/32/32.

---

## 9. Artifact map

| What | Path |
|---|---|
| Inbound review request | `d:\코딩\R\HANDOFF_QUANT_STRATEGY_REVIEW_AND_SELECTION.md` |
| This verdict | `d:\코딩\R\HANDOFF_ANTIGRAVITY_C2_VERDICT.md` |
| N-PORT holdings | `d:\코딩\R\data\universe\sec_nport_pit_holdings.pkl` |
| OHLCV cache | `d:\코딩\R\data\backtest_cache\nport_full_530_ohlcv.pkl` |
| PIT provider (repo) | `d:\코딩\R\research_and_backtests\pit_sec_nport_universe.py` |
| C-family engine (canonical) | `...\antigravity\brain\8b3fcff7-bc56-4aa4-93f2-efc7577272b7\scratch\backtest_c_variants.py` |
| C / C-1 / **C-2** / C-3 / C-4 | `...\scratch\test_four_c_candidates.py` |
| C2-1 / C2-2 / C2-3 / C2-4 | `...\scratch\test_four_c2_candidates.py` |
| ATR/stop needles | `...\scratch\explore_c2_levers.py`, `explore_c2_levers_2.py`, `explore_c2_levers_3.py` |
| Cliff (note missing emergency on one C2-2 row) | `...\scratch\test_parameter_cliff.py` |
| Walkthrough (mixed C prints) | `...\8b3fcff7-bc56-4aa4-93f2-efc7577272b7\walkthrough.md` |
| Live SSOT | `d:\코딩\R\al_sangmoo\core\constants.py` |
| Live exits | `d:\코딩\R\al_sangmoo\domain\risk\trailing_stop.py`, `portfolio_guardian.py` |
| KIS | `d:\코딩\R\al_sangmoo\infrastructure\brokers\kis_broker.py` |

---

## 10. One-line for the user / chat title

**실전 후보는 C-2로 동결. C2-1·C2-2는 과적합. 라이브는 당분간 C1-M2 유지. 컷오버는 번들 단위 + 페이퍼 먼저.**
