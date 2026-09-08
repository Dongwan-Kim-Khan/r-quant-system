# PRIVATE PLACEMENT MEMORANDUM / KEY INVESTOR INFORMATION

## Al-Sangmoo Dynamic Alpha Hedge Strategy
### *(C1–M2 Quantitative Engine)*

| Field | Specification |
|---|---|
| **Document Type** | Prospectus / PPM (Private Placement Memorandum) & KIID-style Key Facts |
| **Strategy Code** | `C1-M2` |
| **Classification** | Quantitative Systematic — Trend-Following & Dynamic Regime Overlay |
| **Base Currency** | United States Dollar (USD) |
| **Valuation Frequency** | Daily (mark-to-market, US equity session close) |
| **Document Date** | 8 September 2026 |
| **Backtest Audit Cut-off** | 28 August 2026 |
| **Status** | Research / Simulated Track Record — Not an offer to the public |

> **IMPORTANT:** This document describes a *systematic trading strategy* and its audited historical simulation. It is **not** a registered collective investment scheme prospectus under the U.S. Securities Act, the EU UCITS/AIFMD frameworks, or the Korean FSC public-offering rules, unless and until separately registered. Distribution is limited to **qualified / accredited / professional investors** where permitted by local law.

---

## I. Executive Summary

### 1.1 Fund / Strategy Identity

| Item | Detail |
|---|---|
| **Legal / Marketing Name** | Al-Sangmoo Dynamic Alpha Hedge Strategy (C1–M2 Engine) |
| **Strategy Type** | Quantitative Systematic Trend-Following & Dynamic Regime Overlay |
| **Reference Benchmarks** | NASDAQ-100 (`QQQ`); S&P 500 (`SPY`); secondary ceiling check vs ProShares Ultra QQQ (`QLD`, 2×) |
| **Target Investor** | Long-horizon capital appreciators seeking **asymmetric payoff** and measurable excess return (*alpha*) versus NASDAQ-100, comfortable with equity-like drawdowns |
| **Recommended Capital Plan** | Seed **USD 8,000** + **USD 400** on each month’s first U.S. trading session (*DCA / MWRR*) |
| **Minimum Notionals (operational)** | Live KIS brokerage account with sufficient buying power for 1.5× Nasdaq exposure in low-vol bull regimes |

### 1.2 One-Sentence Thesis

> Convert Al-Sangmoo’s 17-year proprietary swing constitution (Ichimoku *Kijun* life-line, volume-dry-up, dual-momentum leadership) into a **fully mechanical**, point-in-time, friction-aware book that **never parks idle cash**, scales conviction 50/30/20 into Composite-RS leaders, and applies a **−5.0% hard stop** with uncapped trailing winners — the C1–M2 engine.

### 1.3 Headline Simulated Results (Audited Friction)

Friction stack applied uniformly: **next-session open fills**, **10 bps slippage**, **8 bps commission**, **Point-in-Time (PIT) dynamic 60-name universe**.

| Mandate | Window | Net ROI / CAGR | Max Drawdown | Notes |
|---|---|---:|---:|---|
| M2 Lump-sum | 2018-08-31 → 2026-08-28 | **CAGR 22.00%** | **−51.13%** | Beats QQQ B&H CAGR 18.39% |
| C1 Lump-sum | same | CAGR 18.47% | −41.71% | Baseline champion pre-stop widen |
| M2 DCA ($8k + $400/mo) | same | **ROI +276.5% / XIRR 27.48%** | **−48.15%** | Final **$174,683** on $46,400 invested |
| QQQ DCA (identical flows) | same | ROI +155.8% / XIRR 19.49% | −28.90% | Final $118,705 — M2 excess **≈ +$55,979** |

---

## II. Investment Philosophy & Strategy

### 2.1 Core Philosophy

1. **Codified Discretion → Systematic Rules**  
   The discretionary framework of Al-Sangmoo (Oh Dong-seok): 17 years of prop/hedge-fund practice distilled into a **3-Gate** entry model — Engine A (20-day breakout above cloud), Engine B (*Kijun* pullback reclaim), Gate VDU (volume dry-up compression).

2. **Asymmetric Payoff (*Let Winners Run*)**  
   Accept a high rate of small, mechanical losses (hard stop) in exchange for **uncapped right-tail** capture via Ichimoku/ATR trailing after a +15% peak trigger. Win rate is intentionally modest; **Profit Factor** and **Calmar** are the primary quality metrics.

3. **No Cash Drag**  
   Empty conviction slots do **not** accrue idle cash. Residual capital is continuously allocated to a **QQQ cash-proxy overlay**, with conditional QLD mix when gross leverage targets 1.5×.

### 2.2 Dual-Alpha Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                    C1–M2 PORTFOLIO NAV                       │
├──────────────────────────┬──────────────────────────────────┤
│   SATELLITE ALPHA        │   BASE BETA (Cash Proxy)         │
│   1–3 leadership slots   │   Idle residual → QQQ (+ QLD)    │
│   Composite RS ranked    │   Regime-adaptive gross exposure │
│   PIT Dynamic-60 pool    │   Never 50% cash under SMA200*   │
└──────────────────────────┴──────────────────────────────────┘
* Prior experiments showed SMA200 cash sleeves underperform QQQ B&H.
```

| Sleeve | Role | Selection |
|---|---|---|
| **Base Beta** | Structural Nasdaq exposure; eliminates cash drag | Always-on QQQ; QLD overlay only when leverage target > 1.0× |
| **Satellite Alpha** | Idiosyncratic leadership premium | Top Composite RS names that **beat QQQ’s own Composite RS** (dual-momentum gate) inside the PIT 11-sector Dynamic-60 |

**Composite Relative Strength (ranking score):**

\[
\text{CompositeRS}_t = 0.40 \cdot RS_{21} + 0.35 \cdot RS_{63} + 0.25 \cdot RS_{126}
\]

### 2.3 Dynamic Leverage Module

| Regime Condition | Gross Nasdaq Target | Implementation |
|---|---:|---|
| `SPY ≥ SMA(200)` **and** `VIX < 20` | **1.5×** | Mix residual cash into QQQ (1×) + QLD (2×) without exceeding spendable idle capital |
| Otherwise (incl. `SPY < SMA(200)` or `VIX ≥ 20`) | **1.0×** | QLD off; QQQ core retained |

Leverage is **not** synthetic margin beyond cash; when fully invested in equities, gross exposure cannot rise further.

---

## III. Portfolio Construction & Allocation

### 3.1 Three-Slot Conviction Matrix (Bull Regime)

| Slot | Rank by Composite RS | Target Weight |
|---|---|---:|
| Slot 1 | #1 leadership name | **50%** |
| Slot 2 | #2 leadership name | **30%** |
| Slot 3 | #3 leadership name | **20%** |

**Bear regime (`SPY < SMA200`):** new entries capped at **2 slots × 25%** (Baseline C1). Residual weight remains in the QQQ proxy. *(Tuning variant M2 historically also tested a soft-bear 2×40% sleeve; production PPM risk section uses the −5% stop as the defining M2 control.)*

### 3.2 Rebalancing & Execution Protocol

| Step | Timing (US Eastern) | Action |
|---|---|---|
| 1 | **15:30 ET** (pre-close scanner window) | Signal adjudication: 3-Gate + Composite RS on **as-of prior close / same-day bars available at T−30m** (research backtests use **prior close only** — no look-ahead) |
| 2 | Next regular-session **Open** | Mechanical fills with modeled **+10 bps** buy / **−10 bps** sell slippage |
| 3 | Same bar / EOD | Proxy rebalance toward leverage target; deposit days force proxy refresh |
| 4 | Continuous | HTML-spec exits: hard stop intraday wick; trailing on close |

**DCA Protocol:** On each **first trading day of the calendar month**, USD contribution credits cash *before* exits/entries/proxy rebalance, so new capital immediately funds open slots or the QQQ core.

### 3.3 Universe Construction (Anti-Survivorship)

- **PIT Dynamic-60:** Eleven sector ETFs ranked by Composite RS; quota curve fills ~60 names using only tickers already listed as of the prior close.
- IPOs enter only when listed; delisting incompleteness vs CRSP is disclosed as a residual bias.

---

## IV. Risk Management & Exit Discipline

### 4.1 Hard Stop-Loss (M2 Specification)

\[
P_{\text{stop}} = P_{\text{entry}} \times (1 - 0.05)
\]

- Trigger: session **low** ≤ stop price → exit at `min(open, stop)` path (modeled).
- **No discretion.** Earnings gaps may fill **through** the stop (see §VI Gap Risk).

### 4.2 Trailing Take-Profit (Uncapped)

Activation when peak unrealized gain ≥ **+15%**:

\[
\text{TrailFloor} = \max\!\big(Kijun_{26},\; PeakHigh - 2.5 \times ATR_{14}\big)
\]

Exit on **close** below `TrailFloor`. No artificial profit cap (*Let Winners Run*).

### 4.3 Operational Guardrails (Live Stack)

| Control | Implementation |
|---|---|
| Order idempotency | SQLite **WAL** mode; persistent order keys |
| Concurrency | `ORDER_MUTEX` / single-writer execution path |
| Slot ceiling | Hard max open names = regime slot count |
| Macro overlay (optional live) | MSI 2.0 risk index available in platform; **C1–M2 backtest core does not cash to 50% on MSI** (prior MSI-gate ablation underperformed) |

---

## V. Historical Track Record & Audit

**Audit stack (all tables below):** slippage 10 bps · fee 8 bps · next-open · PIT Dynamic-60 · QQQ/QLD proxy as specified.

### 5.1 Lump-Sum Performance — C1 Baseline vs M2 Engine

**Initial simulated capital:** USD 100,000 (research NAV; scale-invariant to CAGR/MDD/Sharpe).

#### Window A — Full Cycle (2018-08-31 → 2026-08-28)

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win % | Profit Factor | vs QQQ excess CAGR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ B&H | 18.39% | −35.62% | 0.819 | 0.516 | — | — | — | — |
| QLD B&H | 27.90% | −63.79% | 0.752 | 0.437 | — | — | — | — |
| **C1 v1 (Baseline)** | **18.47%** | **−41.71%** | **0.725** | **0.443** | 325 | 24.0% | 1.489 | **+0.08%p** |
| **M2 Engine** | **22.00%** | **−51.13%** | **0.761** | **0.430** | 286 | 28.3% | 1.825 | **+3.61%p** |

#### Window B — AI Mega-Cycle (2023-08-31 → 2026-08-28)

| Book | CAGR | MDD | Sharpe | Calmar | Trades | Win % | Profit Factor | vs QQQ excess CAGR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ B&H | 23.93% | −22.88% | 1.152 | 1.046 | — | — | — | — |
| QLD B&H | 39.92% | −42.34% | 1.028 | 0.943 | — | — | — | — |
| **C1 v1 (Baseline)** | **31.81%** | **−26.96%** | **1.043** | **1.180** | 129 | 25.6% | 1.760 | **+7.88%p** |
| **M2 Engine** | **30.23%** | **−39.50%** | **0.875** | **0.765** | 128 | 30.5% | 1.931 | **+6.30%p** |

**Interpretation:** M2 raises full-cycle CAGR and Profit Factor at the cost of deeper MDD and a modest AI-window CAGR trade-off versus C1. Selection between C1 and M2 is a **drawdown-budget** decision, not a capital-structure decision (§5.3).

### 5.2 Dollar-Cost Averaging Track Record

**Cash-flow schedule:** Seed **$8,000** at \(T_0\) + **$400** on each month’s first U.S. session.  
**Total invested (8y):** **$46,400** (97 cash-flow events including seed).  
**Performance metric:** Money-Weighted Rate of Return (**MWRR / XIRR**).  
**M2 configuration in this table (tuning / DCA audit):** bear sleeve **2 × 40%** + hard stop **−5.0%** (identical to lump-sum M2 that printed 8y CAGR 22.00%).

| Book | Total Invested | Final Equity | Net Profit | Net ROI | XIRR (MWRR) | DCA MDD | vs QQQ Profit |
|---|---:|---:|---:|---:|---:|---:|---:|
| SPY DCA | $46,400 | $93,009 | $46,609 | 100.5% | 14.47% | −32.42% | −$25,696 |
| QQQ DCA | $46,400 | $118,705 | $72,305 | 155.8% | 19.49% | −28.90% | — |
| QLD DCA | $46,400 | $199,134 | $152,734 | 329.2% | 30.22% | −59.37% | +$80,430 |
| **C1 v1 DCA** | $46,400 | $147,265 | $100,865 | 217.4% | 23.94% | −39.99% | **+$28,561** |
| **M2 DCA** | $46,400 | **$174,683** | **+$128,283** | **+276.5%** | **27.48%** | **−48.15%** | **+$55,979** |

> **M2 DCA vs QQQ DCA:** approximately **+$56,000** excess terminal wealth on identical contributions.

### 5.3 Capital-Structure Sensitivity (Stress)

*Question tested:* Does changing seed vs contribution mix flip C1 vs M2 terminal ranking?  
*Answer:* **No.** Across all three stressed schedules, **M2 leads final equity; C1 leads DCA MDD.**

> **Definition note:** Sensitivity runs isolate the stop change — **M2 = C1 slot/leverage identical + hard stop −5% only** (bear weights remain Baseline 2 × 25%). Terminal figures below are therefore *not* mechanically identical to §5.2’s bear-40/40 M2; both audits still rank M2 first on final equity.

| Scenario | Seed / Contribution | Total Invested | M2 Final Equity | C1 Final Equity | Gap (M2 − C1) |
|---|---|---:|---:|---:|---:|
| **Heavy DCA** | $3,000 + $600/mo | $60,600 | **$246,778** | $186,047 | **+$60,732** |
| **Heavy Lump-sum** | $30,000 + $300/mo | $58,800 | **$282,133** | $202,594 | **+$79,539** |
| **Step-up DCA** | $8,000; $300/mo → $800/mo after 2022-09-01 | $60,800 | **$219,158** | $173,492 | **+$45,666** |

**Investor implication:** Choose **C1** if the mandate’s *hard* drawdown tolerance is ≈ −40%; choose **M2** if the mandate prioritizes terminal wealth and accepts ≈ −45% to −51% path MDD.

### 5.4 Rejected / Ablated Designs (Transparency)

The following were tested under identical friction and **permanently discarded** for the production C1–M2 engine:

| Ablation | Result | Decision |
|---|---|---|
| SMA200 50% cash sleeve | Underperformed QQQ B&H | Rejected |
| High-hurdle solo satellite (+10pt RS) | 8y CAGR ~7% | Rejected |
| Weekly 3–5d mean-reversion | Negative CAGR | Rejected |
| PEAD (+15% surprise, pure 60d hold) | Lost to C1 both windows | **PEAD research closed** |
| MSI climate gate (stocks → QQQ/cash) | Cut V-recoveries; 8y ~2% | Rejected as C1 replacement |
| Entry squeeze (kijun≤3.5% + ATR contraction) | Over-filtered leaders; CAGR collapsed | Rejected |
| Early trail (+12%) / Wide trail (+18%, ATR 3) | Destroyed or diluted right tail | Rejected |
| Pure S&P timing (SSO/UPRO/SH switches) | All lost to C1 on 8y CAGR | Index timing not a substitute |

---

## VI. Risk Factors & Disclosures

### 6.1 Market Risk
Nasdaq and S&P drawdowns can and will produce **losses of invested capital**. Simulated peak-to-trough declines of approximately **−40% (C1)** to **−51% (M2 lump-sum)** have already been observed in-sample.

### 6.2 Leverage & Volatility Decay
The 1.5× module (QQQ+QLD) introduces **path-dependent decay** in choppy bull regimes even when the spot index is flat-to-up. Gross exposure is cash-constrained but still amplifies left-tail events while QLD is held.

### 6.3 Execution, Gap & Model Risk
- Pre-market earnings/news gaps may fill **beyond** the −5% hard stop.  
- Slippage may exceed the audited 10 bps in stress liquidity.  
- PIT Dynamic-60 is **not** CRSP-complete; missing bankrupt/acquired names bias results.  
- Parameter choices (stop −5%, trail +15%, RS weights) were researched in-sample; **overfitting risk remains**.

### 6.4 Liquidity & Concentration
Three-name conviction sizing implies **issuer concentration**. A single leadership name at 50% NAV can dominate P&amp;L.

### 6.5 Operational & Technology Risk
Live deployment depends on Korea Investment & Securities (KIS) API availability, process uptime of `PortfolioGuardian`, and correct clock synchronization to US cash equity sessions.

### 6.6 Past Performance Disclaimer

> **PAST SIMULATED PERFORMANCE IS NOT INDICATIVE OF FUTURE RESULTS.**  
> No representation is made that any account will or is likely to achieve profits or losses similar to those shown. Hypothetical results have inherent limitations, including the benefit of hindsight in rule selection. Live trading involves risk of loss **greater than or equal to** the entire amount invested.

### 6.7 No Offer; Eligibility
This PPM/KIID-style memorandum does **not** constitute an offer to sell or a solicitation to buy any security or fund interest in any jurisdiction where such offer would be unlawful. Interests, if any, would be offered only via definitive subscription documents to eligible investors.

---

## VII. Technology & Infrastructure Specification

| Layer | Specification |
|---|---|
| **Language / Runtime** | Python 3.11+ |
| **API Surface** | FastAPI service plane (`server.py`) |
| **State / Orders** | SQLite 3 with **WAL** mode; idempotent order keys |
| **Broker Connectivity** | Korea Investment & Securities (**KIS**) OpenAPI + WebSocket market data |
| **Risk Daemon** | 24/7 background monitor — `PortfolioGuardian` (stop/trail/slot enforcement) |
| **Research SSOT** | `research_and_backtests/` engines: `qqq_beat_alpha_backtester.py` (C1), `c1_v1_tuning.py` (M2 controls), `c1_v1_dca_simulation.py`, `dca_capital_sensitivity.py` |
| **Artifact Audit Trail** | JSON caches under `data/backtest_cache/` (`c1_v1_tuning_results.json`, `c1_v1_dca_results.json`, `dca_capital_sensitivity_results.json`) |

### 7.1 High-Water Mark (Conceptual Reporting)

For investor reporting, strategy NAV high-water mark (HWM) is defined as:

\[
HWM_t = \max_{0 \le s \le t} NAV_s
\]

Drawdown at \(t\):

\[
DD_t = \frac{NAV_t}{HWM_t} - 1
\]

DCA mandates additionally report **invested-capital ratio** \(NAV_t / \sum \text{Contributions}_{0..t}\) and its drawdown (see research artifacts: `invested_ratio_mdd_pct`).

---

## VIII. Appendix — Glossary

| Term | Meaning |
|---|---|
| **Alpha** | Excess return vs a stated benchmark after beta adjustment (here also shown as excess CAGR and Jensen α in research JSON) |
| **Asymmetric Payoff** | Limited loss per trade (−5% stop) vs uncapped gain via trailing |
| **Calmar Ratio** | CAGR / \|MDD\| |
| **Composite RS** | Multi-horizon relative strength blend (21/63/126d) |
| **DCA** | Dollar-cost averaging — fixed contributions on a schedule |
| **HWM** | High-Water Mark |
| **KIID** | Key Investor Information Document (UCITS-style factsheet format) |
| **MDD** | Maximum peak-to-trough drawdown |
| **MWRR / XIRR** | Money-weighted rate of return for irregular cash flows |
| **PIT** | Point-in-Time universe — no future listings |
| **PPM** | Private Placement Memorandum |
| **Sharpe Ratio** | Annualized mean excess return / volatility (rf ≈ 0 in research tables) |

---

## IX. Document Control

| Version | Date | Authoring Context | Change |
|---|---|---|---|
| 1.0 | 2026-09-08 | Al-Sangmoo Quant Research / Cloud Agent audit chain | Initial C1–M2 PPM incorporating lump-sum, DCA, and capital-sensitivity audits |

**End of Memorandum**
