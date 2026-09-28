# Handoff: Cash Proxy Budget Guard — Cursor Independent Audit

**Date:** 2026-09-19 (KST)  
**From:** Cursor  
**To:** Operator / Antigravity  
**Status:** **PASS WITH NOTES — incident replay is blocked; two residual holes documented**  
**Source:** `HANDOFF_CURSOR_PROXY_BUDGET_GUARD_VERIFICATION.md`

---

## 0. Verdict

The 2026-09-18 23:08 over-buy (**$806 notional vs $782.77 USD cash**, KRW integrated margin filling the $23 gap) **cannot recur on the happy path**. Plan clamp + execution `remaining_cash` both refuse BUY whose `qty * round(px * 1.005, 2)` exceeds available **USD** cash. Zero cash suppresses all BUY.

QQQ-first is the correct C-2 policy. `$300` leftover does **not** idle: QQQ is skipped, QLD is bought in whole shares.

I did **not** rubber-stamp the handoff:

1. Plan vs executor buffer was **not** identical (`px * 1.005` float vs `round(px * 1.005, 2)`). Cursor aligned the plan to the rounded limit. Delta was cents, executor already clamped, but Task 2 asked for a match.
2. `remaining_cash` is decremented **before** pending-TTL / intent / broker ACK. That cannot overspend; it can **starve** a later QLD buy if the QQQ ticket never actually goes out.
3. Intent lock is **per ticker** (`PRIMARY KEY ticker`), not per cash. In-process `PROXY_ORDER_MUTEX` serializes Guardian vs Autopilot. Two processes could still claim QQQ and QLD independently against a stale cash snapshot. This deployment is one process — acceptable, not a cash mutex.

Handoff claimed **61** sleeve tests; this tree runs **60** (+2 new Cursor tests below). All commanded suites **OK**.

---

## 1. Task 1 — QQQ-first vs $300 QLD-only

**Replay (incident prices):** cash `$782.77`, QQQ `$716.55`, QLD `$89.45`, existing QLD 7, no QQQ.

| Step | Result |
|---|---|
| Unclamped target | QQQ +1 and QLD +1 ≈ `$806` cash, `$810` with 0.5% limit |
| After clamp | **QQQ BUY 1 only**. QLD dropped. Commitment `$720.13` ≤ `$782.77` |

**$300 residual, QQQ `$716.61`, QLD `$89.82`:** QQQ `affordable_qty = 0`, loop continues, **QLD BUY 3** (`3 * round(89.82*1.005,2) = $270.81` ≤ `$300`). Not “do nothing”.

**Is QQQ-first universally better?** For C-2 yes. 1.5x is `0.5 QQQ + 0.5 QLD` of the **idle sleeve**, with QQQ as 1.0x core and QLD as 2.0x boost. When cash can fund one QQQ **or** ~8 QLD, funding QQQ keeps the core proxy. When cash cannot fund QQQ, the same loop funds QLD. Preferring QLD-first on `$780` would park the sleeve in 2x leverage and skip the 1x anchor — worse constitution.

Do not invert the sort key.

---

## 2. Task 2 — `1.005` buffer sync

| Layer | Formula before Cursor audit | After |
|---|---|---|
| `cash_proxy._clamp_proxy_buy_actions_to_cash` | `unit_cost = px * 1.005` (float) | **`round(px * 1.005, 2)`** |
| Guardian / Autopilot `limit_est` | `round(px * 1.005, 2)` | unchanged |
| `place_order` limit | `round(px * 1.005, 2)` BUY / `0.995` SELL | unchanged |

Pre-fix example: QLD `$89.45` × 8 → plan `$719.178` vs broker `$719.20`. If cash sat in the `$0.02` gap, the plan would emit 8 shares and the executor would clamp to 7. Safe, but not synced. Plan now uses the same rounded tick as the order.

Invariant to keep:

```
sum(qty * round(px * 1.005, 2) for BUY) <= cash_usd
```

---

## 3. Task 3 — Concurrency / double-spend

| Control | Scope | What it stops |
|---|---|---|
| `cash_proxy.PROXY_ORDER_MUTEX` via `@serialized_proxy_orders` | **In-process** | Guardian `_align_cash_proxy_sleeve` and Autopilot `_ensure_cash_proxy_parked` cannot interleave sleeve orders |
| `claim_proxy_order_intent` | SQLite `proxy_order_intents.ticker PRIMARY KEY` | Second daemon cannot claim the **same ticker** (QQQ vs QQQ) |
| `ORDER_MUTEX` + KIS 3.5 TPS | Broker send | Duplicate HTTP stamps, not cash accounting |

Handoff name `rebalance_cash_proxy_sleeve` does not exist. The Autopilot entry is `_ensure_cash_proxy_parked` (`autopilot_trader.py` ~1213).

**Gap:** cash is not a lock. Process A can claim QQQ, process B can claim QLD, both seeing `$782.77`. Not this app’s process model. Do not treat intent PK as a USD wallet.

**Gap:** `remaining_cash -= qty * limit_est` runs **before** pending skip / failed claim / market-closed `continue`. If QQQ is reserved in the loop then skipped, QLD sees a smaller wallet than reality. Conservative vs KRW borrow; can skip a valid QLD fill. Next align cycle (60s) retries. Optional fix: debit only after broker ACK / simulated ledger write.

SELL proceeds are **not** credited into `remaining_cash` in the same loop. Mix rebalance that sells QQQ to buy QLD waits for the next cash snapshot. Correct vs spending unsettled sale; do not “fix” by spending KRW margin.

---

## 4. Tests Cursor ran

```bash
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian.TestCashProxyBudgetGuard -v
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian -q
python -m unittest tools_and_tests.test_track3_staging_readiness -q
python -m unittest tools_and_tests.test_dashboard_portfolio_sync tools_and_tests.test_dashboard_launch_ssot -q
```

| Suite | Result |
|---|---|
| `TestCashProxyBudgetGuard` (Antigravity 2 + Cursor 2) | **OK** |
| `test_cash_proxy_sleeve_guardian` | **60 OK** (handoff said 61) |
| `test_track3_staging_readiness` | **29 OK** |
| dashboard SSOT | **23 OK** |

Cursor added:

- `test_small_cash_funds_qld_when_qqq_unaffordable`
- `test_plan_buffer_matches_executor_rounded_limit`

---

## 5. Code Cursor changed during audit

| File | Change |
|---|---|
| `al_sangmoo/domain/risk/cash_proxy.py` | `_clamp_proxy_buy_actions_to_cash` unit cost = `round(px * buffer_factor, 2)` |
| `tools_and_tests/test_cash_proxy_sleeve_guardian.py` | Task 1 `$300` QLD path + Task 2 rounded-limit assertion |

No Guardian/Autopilot loop rewrite. C-2 slot/stop constants untouched.

---

## 6. Residual (do not confuse with the incident)

1. **Live `ord_psbl_cash` fallback** in `kis_broker.get_overseas_balance`: if `cash_avail == 0` and **not paper**, cash is replaced with purchasable amount. That field is how KRW integrated margin shows up as “cash”. Paper VPS (this book) skips it. **Do not enable that fallback on VIRTUAL_PAPER. On REAL_PRODUCTION, budget guards must keep using `free_cash_usd` from lot-reconciled snapshot, never `ord_psbl_cash`.**
2. Dashboard **CASH $0** with QQQ 1 + QLD 8 is the **post-incident fully invested state**, not a display bug by itself. New BUYs are suppressed until USD cash > 0 (or a SELL credits cash on a later snapshot).
3. `deployable = max(cash, idle)` still sizes the **target mix** off idle NAV; clamp then cuts BUYs to cash. That is intended. Do not pass `ord_psbl_cash` in as `cash_usd`.

---

## 7. Operator takeaway

- Incident class (USD short, KRW margin fill) is **closed** on the current paper daemon.
- QQQ-first + QLD spillover on small cash is **correct**.
- Buffer is now one formula.
- Double-spend across Guardian/Autopilot in **one process** is closed; cash is still not a DB lock.
- Next production cutover: strip `ord_psbl_cash` from USD wallet SSOT or the same $23-on-$92k story returns with a bigger number.
