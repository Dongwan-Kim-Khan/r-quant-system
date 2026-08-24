## 2026-08-23T01:55:07Z

Implement Milestones M1, M2, M3 in `al_sangmoo/domain/quant/`:
1. **M1: `al_sangmoo/domain/quant/ichimoku.py`**:
   - Centralize all indicator calculations in `calculate_ichimoku_indicators(df)` (Tenkan 9, Kijun 26, RawSpanA, RawSpanB, SpanA shift 26, SpanB 52 shift 26, Chikou shift -26, SMA20, SMA50, SMA60, SMA200, Vol_SMA20, Vol_Ratio).
   - Implement `project_future_cloud(df_clean, periods=26, is_weekly=False)`.
   - Implement `detect_cloud_trampoline_bounce(df_clean, max_lookback=14)`.
   - Implement `compute_institutional_flow_indicators(df)`.
   - Implement `build_ichimoku_series_payload(df_clean, is_weekly=False, max_bars=500)`.
2. **M2: `al_sangmoo/domain/quant/scoring.py`**:
   - Create `QuantIndicators`, `WeeklyTrendContext`, `InstitutionalFlowContext`, `TrampolineBounceContext`, `QuantScoreBreakdown`, `TierClassification` dataclasses.
   - Implement canonical graduated scoring: `calculate_canonical_bull_score(ind)`, `calculate_canonical_sniper_score(ind, trampoline)`, `evaluate_quant_score(...)`.
   - Implement canonical 3-Tier classification: `classify_quant_tier(...)` and `classify_3tier_candidates(chart_data, tailwind_sectors, stream_mentioned_tickers=None)`.
   - Standardize -4.0% hard stop, +15.0% target, +8.0% partial take profit.
3. **M3: `al_sangmoo/domain/quant/macro.py`**:
   - Implement unified `evaluate_macro_stance(gauges, defense_count, buy_count, matched_shocks, transcript, title)` with unified hard-gauge weights (US 10Y: 25/18/10/4/0, VIX: 15/10/5/0, WTI: 10/6/3/0, DXY: 10/6/2/0).
   - Keep backward-compatible `calculate_msi_regime()` adapter.
4. **`al_sangmoo/domain/quant/__init__.py`**:
   - Export all public domain quant functions and data classes.
