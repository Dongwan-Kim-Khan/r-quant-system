## 2026-08-23T01:51:54+09:00

Survey and investigate all quant scoring formulas and 3-tier classification rules across the codebase:
1. Read `d:\코딩\Playground\al_sangmoo_project\.agents\ORIGINAL_REQUEST.md` (specifically the Phase 5.3 section at 2026-08-22T16:50:42Z).
2. Inspect existing scoring logic in `al_sangmoo/domain/quant/` (e.g. `scoring.py`, `ichimoku.py`), `al_sangmoo_daily_bot.py`, `generate_dashboard_feed.py`, `server.py`, and any other scanning or ranking functions.
3. Identify all conflicting scoring formulas, weights, and cutoffs:
   - Bull score calculation differences (e.g. 65pt vs 70pt vs 80pt)
   - Tier 1: Sniper Radar / Macro Tailwind + Smart Money (Gate-0 Macro Tailwind, OBV Stealth Accumulation, 14-Day Volume Flow Ratio >= 120%, Bull Score >= 75)
   - Tier 2: Structural Pullback (26-Day Kijun-sen support within +/-3%, Tenkan >= Kijun alignment, 20-Day Volume Dry-Up < 60% of 20-day MA)
   - Tier 3: Cloud Sniper (Forward +26D Cloud Trampoline bounce above Span A/B, Stage 2 breakout momentum, -4% hard stop rule)
4. Document all differences between batch scanners (`al_sangmoo_daily_bot.py`), feed generators (`generate_dashboard_feed.py`), and API endpoints.
5. Propose the canonical `al_sangmoo/domain/quant/scoring.py` architecture, data structures, and function signatures to guarantee deterministic, identical results across all callers.
