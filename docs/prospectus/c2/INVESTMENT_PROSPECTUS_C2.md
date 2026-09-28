# Institutional Private Placement Memorandum (PPM / KIID)

## Al-Sangmoo Dynamic Alpha Hedge Strategy
### *(C-2 Production Quantitative Engine)*

| Attribute | Specification |
|---|---|
| **Document Type** | Private Placement Memorandum (PPM) & KIID Key Investor Information |
| **Strategy Identifier** | `C-2` (Equal Sizing 34/33/33 + Dual-Clock Stopping Architecture) |
| **Asset Class** | Quantitative Systematic — Trend-Following & Dynamic Regime Overlay |
| **Base Currency** | USD ($) |
| **Valuation Frequency** | Daily at US Regular Market Close |
| **Freeze Date** | 2026-09-14 (KST) |
| **Audit Sample Period** | 2019-09-30 to 2026-08-28 (6.9 Years) |
| **Legal Classification** | Algorithmic Trading Workstation — Qualified Institutional Investors |

> This document describes the audited backtest track record and execution constitution of the frozen C-2 quantitative engine. It is not an offer to the general public.

---

## I. Executive Summary

### 1.1 Fund Identity & Investment Thesis

The **C-2 Production Quantitative Engine** models 17 years of institutional proprietary trading rules (Ichimoku 26-day Kijun-sen, Volume Dry-Up, and Relative Strength leadership) with Point-In-Time SEC N-PORT quarterly holdings.

- **Zero Cash Drag**: Unallocated cash is permanently parked in the **QQQ Cash Proxy sleeve**.
- **Dynamic Leverage Overlay**: Conditioned on `SPY ≥ SMA200` and `VIX < 20`, scaling up to **1.5x QLD overlay**, reverting strictly to 1.0x Core QQQ when market stress appears.

### 1.2 Audited 7-Year Track Record (Net of Frictions)

*Frictions modeled: Next-day open fill, 10 bps slippage, 8 bps commission, survivorship-controlled SEC N-PORT PIT quarterly holdings.*

| Candidate Engine | 7y CAGR | 7y MDD | Profit Factor | Total Trades | Status |
|:---|---:|---:|---:|---:|:---|
| **C-2 (Production Freeze)** | **32.76%** | **−38.85%** | **2.15** | **114** | **FROZEN CHAMPION** |
| *C Cluster Conservative Prior* | *26.0% ~ 31.0%* | *−36% ~ −39%* | *2.0 ~ 2.2* | *~110* | *Live Execution Prior* |
| C1-M2 (Legacy Baseline) | 15.41% | −34.47% | 1.71 | 199 | Whipsawed by intraday -5% tick stop (134 exits) |
| QQQ Passive Buy & Hold | 19.8% | −35.1% | 1.35 | - | Nasdaq-100 Benchmark |

---

## II. The 4 Pillars of C-2 Architecture

1. **Dual Entry Gates**:
   - **Trend Gate**: `QQQ Close ≥ SMA20` (confirmed prior day close).
   - **Alpha Gate**: `Stock 3M Composite RS ≥ QQQ Composite RS`.
2. **Equal Sizing Slots (34/33/33)**:
   - **Bull Regime**: 3 Slots equal distribution (34% / 33% / 33%), eliminating single-stock concentration penalty.
   - **Bear Regime**: 2 Slots defensive distribution (25% / 25%), holding 50% core QQQ cash proxy.
3. **Dual-Clock Stopping Protocol**:
   - **EOD Hard Stop**: `Close ≤ Entry × 0.93` (−7.0%), evaluated only during 15:50–16:00 ET closing window.
   - **Emergency Stop**: `Last ≤ Entry × 0.90` (−10.0%), monitored 24/7 during regular hours by Guardian daemon.
   - **Legacy intraday -5% tick stop permanently deleted**.
4. **Uncapped Trailing Take-Profit**:
   - Armed at **+18.0% peak gain**.
   - Floor: `max(Kijun-26, Peak − 3.0 × ATR14)`.
   - **Standalone Kijun exit permanently disabled**.

---

## III. Pre-Trade Guardrails & Execution

- **Single Asset Cap**: Bull 39.0% (34% + 5%p buffer), Bear 30.0% (25% + 5%p buffer).
- **Whole-Share Sizing**: Floor division `int(allocation // price)`, residual cash remains in liquid USD balance.
- **SSOT Binding**: `al_sangmoo/core/constants.py`.

*Al-Sangmoo Quant Strategy Group · Version 2.0 (C-2 Production Frozen)*
