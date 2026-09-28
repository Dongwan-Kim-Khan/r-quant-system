# Handoff: Cash Proxy Budget Guard Verification → Cursor

**Date:** 2026-09-19 (KST)  
**From:** Antigravity  
**To:** Cursor  
**Status:** **PATCHED & VERIFIED — Ready for Cursor Independent Audit**  
**Context:** Live trading event on 2026-09-18 23:08 KST where $782.77 residual USD cash resulted in $806.00 proxy ETF buys via broker-side KRW integrated margin.  
**Related Documents:**
- `HANDOFF_ANTIGRAVITY_PORTFOLIO_NAV_SSOT.md`
- `HANDOFF_CURSOR_C2_VERIFICATION.md`
- `al_sangmoo/domain/risk/cash_proxy.py`
- `al_sangmoo/domain/risk/portfolio_guardian.py`
- `al_sangmoo/domain/risk/autopilot_trader.py`
- `tools_and_tests/test_cash_proxy_sleeve_guardian.py`

---

## 0. Executive Summary (Incident & Resolution)

### 1) The Incident (2026-09-18 23:08:49 KST)
- **Background**: On 2026-09-17, 2 shares of QQQ were sold yielding **+$1,414.83**. Autopilot purchased 7 shares of QLD (-$625.80), leaving **$782.77** in unsettled/free USD cash.
- **Trigger**: At 23:08:49 KST (regular session open +38m), Portfolio Guardian executed `_align_cash_proxy_sleeve()` to balance the Bull 1.5x leverage sleeve (target 50% QQQ + 50% QLD).
- **Flaw**:
  - `build_cash_proxy_plan()` calculated sleeve target notional ($1,408 = existing QLD 7 shares $626 + cash $782.77).
  - Target mix: QQQ 50% ($704 -> 1 share @ $716.55) and QLD 50% ($704 -> 8 shares, delta +1 share @ $89.45).
  - Total buy cost required: **$716.55 + $89.45 = $806.00**.
  - But actual available USD cash was only **$782.77** ($23.23 deficit).
  - Because KIS OpenAPI has **KRW Integrated Margin (원화 통합증거금 / purchasable cash ~$92k)** enabled on the account, the broker accepted both orders without rejection.
  - As a result, USD cash was exhausted to **$0.00**, ~$23 was borrowed against KRW margin, and the dashboard displayed persistent `CASH: $0.00`.

### 2) The Fix Applied by Antigravity
1. **Plan Clamping (`cash_proxy.py`)**:
   - Implemented `_clamp_proxy_buy_actions_to_cash()`: Clamps/drops BUY actions so that `sum(qty * price * 1.005) <= cash_usd`.
   - If `cash_usd <= 0`, all BUY actions are suppressed.
   - Prioritizes core anchor `QQQ` first, then secondary `QLD` boost with remaining cash.
2. **Execution Hard Budget Guard (`portfolio_guardian.py` & `autopilot_trader.py`)**:
   - Tracks `remaining_cash = max(0.0, float(cash))` inside the execution loop.
   - For every `side == "BUY"`, enforces `needed_cost <= remaining_cash`. If exceeded, clamps `qty` to `int(remaining_cash // limit_est)`. Drops order if affordable qty is 0.
   - Subtracts executed commitment from `remaining_cash` before next action.
3. **Logger Safe Guards**:
   - Added `logger = logging.getLogger(__name__)` to `portfolio_guardian.py` and `autopilot_trader.py`.

---

## 1. File Diff Inventory

| File | Change Description |
|---|---|
| [`al_sangmoo/domain/risk/cash_proxy.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/cash_proxy.py#L255-L330) | Added `_clamp_proxy_buy_actions_to_cash()`. Wired into `build_cash_proxy_plan()`. Ensures plan actions never exceed `cash_usd`. |
| [`al_sangmoo/domain/risk/portfolio_guardian.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/portfolio_guardian.py#L640-L675) | Added `remaining_cash` tracking and pre-order budget clamping guard in `_align_cash_proxy_sleeve()`. Added module `logger`. |
| [`al_sangmoo/domain/risk/autopilot_trader.py`](file:///d:/코딩/R/al_sangmoo/domain/risk/autopilot_trader.py#L1235-L1270) | Added `remaining_cash` tracking and pre-order budget clamping guard in `rebalance_cash_proxy_sleeve()`. Added module `logger`. |
| [`tools_and_tests/test_cash_proxy_sleeve_guardian.py`](file:///d:/코딩/R/tools_and_tests/test_cash_proxy_sleeve_guardian.py#L1548-L1595) | Added test class `TestCashProxyBudgetGuard` verifying the $782.77 vs $806.00 deficit scenario and zero cash suppression. |

---

## 2. Invariant Contracts Enforced

1. **Strict USD Invariant**:
   $$\sum_{a \in \text{BUY actions}} \left( \text{qty}_a \times \text{px}_a \times 1.005 \right) \le \text{available\_cash\_usd}$$
   Under no circumstances should the trading engine dispatch buy orders whose sum exceeds available USD cash, regardless of broker purchasing power (`ord_psbl_cash`).
2. **Zero-Cash Invariant**:
   If `cash_usd <= 0.0`, `actions` must not contain any `BUY` orders.
3. **Anti-Whipsaw / Cooldown Preserved**:
   Existing 300s proxy sell cooldown, $300 / 3-share deadband, and `serialized_proxy_orders` mutex remain untouched.
4. **Broker SSOT Preserved**:
   `reconcile_overseas_nav()` continues to reject KRW-dwarfed NAVs (>3x) while respecting real lot values.

---

## 3. Cursor Verification Tasks (What to Audit)

Please review the implementation and verify the following architectural and mathematical points:

### Task 1: Priority Order in `_clamp_proxy_buy_actions_to_cash`
- **Location**: `al_sangmoo/domain/risk/cash_proxy.py:302`
- **Current Logic**:
  ```python
  ordered_buys = sorted(
      buys,
      key=lambda x: 0 if str(x.get("ticker") or "").upper() == CASH_PROXY_TICKER else 1,
  )
  ```
- **Audit Question**: 
  - In our Bull 1.5x regime, QQQ is the 1.0x core equity proxy ($716/share) and QLD is the 2.0x boost ($89/share).
  - When cash is ~$780 and both want +1 share, QQQ is funded first ($716.55), leaving ~$66 which is insufficient for 1 share of QLD ($89.45). QLD is dropped.
  - **Question for Cursor**: Is prioritizing QQQ over QLD universally superior, or are there edge cases where funding multiple shares of QLD with smaller cash increments (e.g. $300 cash when QQQ costs $716) would be preferred over doing nothing? *(Note: our code currently naturally funds QLD if QQQ is not affordable, because QQQ yields `affordable_qty = 0` and the loop continues to QLD with the remaining cash).*

### Task 2: Margin & Slippage Buffer Alignment
- **Location**:
  - `cash_proxy.py`: `buffer_factor = 1.005`
  - `portfolio_guardian.py`: `limit_est = round(px * 1.005, 2)`
  - `autopilot_trader.py`: `limit_est = round(px * 1.005, 2)`
- **Audit Question**:
  - Ensure the 0.5% limit buffer in the broker order call matches the budget check so that an order is never clamped by the plan and then rejected by Guardian, or vice versa.

### Task 3: Concurrency & Lock Ownership
- **Check**:
  - Does `serialized_proxy_orders` mutex and `claim_proxy_order_intent` prevent race conditions between `PortfolioGuardian._align_cash_proxy_sleeve` and `AutoPilotTrader.rebalance_cash_proxy_sleeve`?
  - Verify that both cannot execute concurrent proxy buy orders and double-spend the same cash balance.

---

## 4. How to Run Verifications

Run the following test commands in terminal:

```bash
# 1. Run the new Budget Guard regression tests
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian.TestCashProxyBudgetGuard -v

# 2. Run full cash proxy & guardian test suite (61 tests)
python -m unittest tools_and_tests.test_cash_proxy_sleeve_guardian -q

# 3. Run Track 3 staging readiness & proxy sleeve rounding test
python -m unittest tools_and_tests.test_track3_staging_readiness -q

# 4. Run dashboard portfolio sync & SSOT tests
python -m unittest tools_and_tests.test_dashboard_portfolio_sync tools_and_tests.test_dashboard_launch_ssot -q
```

All 4 test suites must pass (`OK`).

---

## 5. Live State Baseline (2026-09-19 00:00 KST)

- **Portfolio NAV**: **~$7,867.00** (~10,850,000 KRW, +8.7% overall account return)
- **Holdings SSOT**:
  - `AMD`: 4 shares ($544.62 / share, ~$2,178, ~27.7%)
  - `DELL`: 4 shares ($583.17 / share, ~$2,332, ~29.6%)
  - `CVX`: 9 shares ($211.48 / share, ~$1,903, ~24.2%)
  - `QQQ`: 1 share ($716.31 / share, ~$716, ~9.1%)
  - `QLD`: 8 shares ($89.77 / share, ~$718, ~9.1%)
- **Cash**: **$0.00** (fully invested; 3 satellite leaders ~81.5%, proxy sleeve ~18.2%)
- **Server**: Running on `127.0.0.1:8000` (PID reloaded with new guards).
