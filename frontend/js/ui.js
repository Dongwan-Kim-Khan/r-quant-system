/**
 * R QUANT TERMINAL v2: UI RENDERING & EVENT DISPATCHER MODULE
 * Professional Bloomberg Dark Terminal Aesthetic (Zero Emojis).
 * Al-Sangmoo GS-Quant Upgraded 3-Slot Trading Cockpit.
 */
import { ApiClient } from './api.js?v=4.3.2';
import { ChartEngine } from './chart.js?v=4.3.2';
import { QuantDecoder } from './decoder.js?v=4.3.2';
import { deriveStopPrice, deriveTargetPrice, isMarketTicker, SLOT_WEIGHTS_BULL, SLOT_WEIGHTS_BEAR, isCashProxyTicker, extractMsiScore, classifyMsiStance, resolveQuickBuyQty } from './constants.js?v=4.3.2';

export const UI = {
    currentSelectedTicker: "",
    currentSelectedPrice: 0.0,
    latestDashboardData: null,
    latestPortfolioData: null,
    MAX_LOGS: 300,
    MAX_TOASTS: 5,

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    toast(message, level = "info") {
        let host = document.getElementById("toastHost");
        if (!host) {
            host = document.createElement("div");
            host.id = "toastHost";
            host.className = "toast-host";
            host.setAttribute("aria-live", "polite");
            document.body.appendChild(host);
        }
        const node = document.createElement("div");
        node.className = `toast toast-${level}`;
        node.textContent = String(message || "");
        host.appendChild(node);
        while (host.childElementCount > this.MAX_TOASTS) {
            host.removeChild(host.firstElementChild);
        }
        setTimeout(() => {
            if (node.parentNode) node.parentNode.removeChild(node);
        }, 6500);
    },

    renderDashboard(data) {
        if (!data) return;
        this.latestDashboardData = data;
        
        const updatedTime = data.last_updated || (data.kpis && data.kpis.last_updated) || new Date().toLocaleTimeString();
        const lastUpEl = document.getElementById("lastUpdated");
        if (lastUpEl) lastUpEl.textContent = `UPDATED: ${updatedTime}`;

        const isBullRegime = (data.slot_allocation_summary && data.slot_allocation_summary.is_bull_regime !== undefined) 
            ? data.slot_allocation_summary.is_bull_regime 
            : true;

        try { this.renderMacro(data.macro, isBullRegime); } catch (e) { console.warn("[UI] renderMacro error:", e); }
        try { this.renderDynamicSectors(data.universe_sectors); } catch (e) { console.warn("[UI] renderDynamicSectors error:", e); }
        try { this.renderPortfolio(data.portfolio); } catch (e) { console.warn("[UI] renderPortfolio error:", e); }
        try { this.renderTopPicks(data.top_conviction_pick, data.top_conviction_runner_up); } catch (e) { console.warn("[UI] renderTopPicks error:", e); }
        try { this.renderTradeLogs(data.execution_logs); } catch (e) { console.warn("[UI] renderTradeLogs error:", e); }
    },

    renderMacro(macro, isBullRegime) {
        if (!macro) return;
        const mc = macro.macro_climate || {};
        const mg = macro.macro_gauges || {};

        const stanceEl = document.getElementById("macroStanceBadge");
        if (stanceEl) {
            stanceEl.textContent = isBullRegime ? "[REGIME: BULL]" : "[REGIME: BEAR]";
            stanceEl.style.color = isBullRegime ? "#34d399" : "#f87171";
            stanceEl.style.borderColor = isBullRegime ? "#059669" : "#dc2626";
            stanceEl.style.background = isBullRegime ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)";
        }

        const headlineEl = document.getElementById("macroHeadline");
        if (headlineEl) {
            headlineEl.textContent = mc.macro_headline || "[MACRO DIRECTIVE: DUAL MOMENTUM + 3M RS CONVICTION SELECTION]";
        }

        const updateVal = (id, val, prefix = '', suffix = '') => {
            const el = document.getElementById(id);
            if (el && val !== undefined) el.textContent = `${prefix}${val}${suffix}`;
        };

        if (mg.us10y) updateVal("gaugeUs10yVal", mg.us10y.val, '', '%');
        if (mg.dxy) updateVal("gaugeDxyVal", mg.dxy.val);
        if (mg.vix) updateVal("gaugeVixVal", mg.vix.val);
        if (mg.wti) updateVal("gaugeWtiVal", mg.wti.val, '$');
        if (mg.gold) updateVal("gaugeGoldVal", mg.gold.val, '$');
    },

    renderDynamicSectors(sectors) {
        const host = document.getElementById("dynamicSectorsContainer");
        if (!host) return;
        const rows = Array.isArray(sectors) ? sectors.slice() : [];
        rows.sort((a, b) => (Number(a.rank) || 99) - (Number(b.rank) || 99));
        const badge = document.getElementById("universeTotalBadge");
        const total = rows.reduce((n, s) => n + Number(s.quota || 0), 0) || 60;
        if (badge) badge.textContent = `${total} UNIVERSE`;
        if (!rows.length) {
            host.innerHTML = `<div style="color:var(--text-muted);font-size:11px;text-align:center;padding:8px;">NO SECTOR DATA</div>`;
            return;
        }
        host.innerHTML = rows.map((s) => {
            const quota = Number(s.quota) || 0;
            const rs = Number(s.rs_3m) || 0;
            const width = Math.max(0, Math.min(100, (quota / 14) * 100));
            const rsColor = rs >= 0 ? "#34d399" : "#f87171";
            const sign = rs >= 0 ? "+" : "";
            const name = this.escapeHtml(String(s.sector || ""));
            const etf = this.escapeHtml(String(s.etf || ""));
            const stocks = (s.stocks || s.selected_stocks || []).map((t) => String(t)).join(" ");
            return `<div class="sector-row" title="${this.escapeHtml(stocks)}">` +
                `<span class="sector-rank">#${Number(s.rank) || 0}</span><span class="sector-name">${name} (${etf})</span>` +
                `<span class="sector-bar-track"><span class="sector-bar-fill" style="width:${width.toFixed(1)}%;"></span></span>` +
                `<span class="sector-quota">${quota} / 60</span><span class="sector-rs" style="color:${rsColor};">${sign}${rs.toFixed(1)}% RS</span></div>`;
        }).join("");
    },

    renderKPIs(kpis, portfolioData, isBullRegime) {
        const p = portfolioData || {};
        const holdings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];

        const effectiveIsBull = (isBullRegime !== undefined && isBullRegime !== null)
            ? Boolean(isBullRegime)
            : ((this.latestDashboardData && this.latestDashboardData.slot_allocation_summary && this.latestDashboardData.slot_allocation_summary.is_bull_regime !== undefined)
                ? this.latestDashboardData.slot_allocation_summary.is_bull_regime
                : true);

        const totalEquityUsd = Number(p.total_equity_usd !== undefined ? p.total_equity_usd : (p.total_equity || 7500.0));
        const totalEquityKrw = Number(p.total_equity_krw || Math.round(totalEquityUsd * (p.usd_krw_rate || 1380)));
        const freeCashUsd = Number(p.free_cash_usd !== undefined ? p.free_cash_usd : Math.max(0, totalEquityUsd - (p.total_eval || 0)));
        const freeCashKrw = Number(p.free_cash_krw !== undefined ? p.free_cash_krw : Math.round(freeCashUsd * (p.usd_krw_rate || 1380)));
        const cashRatioPct = Number(p.cash_ratio_pct !== undefined ? p.cash_ratio_pct : (totalEquityUsd > 0 ? (freeCashUsd / totalEquityUsd * 100) : 100));
        const totalEvalUsd = Number(p.total_eval !== undefined ? p.total_eval : 0.0);
        const pnlPct = Number(p.overall_pnl_pct || 0.0);
        const pnlAmt = Number(p.overall_pnl_amount || 0.0);
        const maxSlots = effectiveIsBull ? 3 : 2;
        const satCount = holdings.filter((h) => !isCashProxyTicker(h.ticker)).length;
        const availableSlots = Math.max(0, maxSlots - satCount);

        const initialCapitalUsd = Number(p.initial_capital_usd || p.base_account_usd || (totalEquityUsd - pnlAmt) || 7500.0);
        const totalCumulativePnlUsd = totalEquityUsd - initialCapitalUsd;
        const totalCumulativeReturnPct = (initialCapitalUsd > 0) ? (totalCumulativePnlUsd / initialCapitalUsd) * 100.0 : 0.0;
        const isTotalPos = totalCumulativePnlUsd >= 0;

        const realizedPnlUsd = Number(p.realized_pnl_amount || 0.0);
        const realizedPnlPct = Number(p.realized_pnl_pct || 0.0);
        const unrealizedPnlUsd = Number(p.unrealized_pnl_amount || 0.0);
        const unrealizedPnlPct = Number(p.unrealized_pnl_pct || 0.0);

        // 1. Total Equity Card
        const fmtUsd = (n) => `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const eqUsdEl = document.getElementById("kpiTotalEquityUsd");
        if (eqUsdEl) eqUsdEl.textContent = fmtUsd(totalEquityUsd);
        const eqKrwEl = document.getElementById("kpiTotalEquityKrw");
        if (eqKrwEl) {
            const retColor = isTotalPos ? 'var(--accent-green)' : 'var(--accent-red)';
            const rlzColor = realizedPnlUsd >= 0 ? '#34d399' : '#f87171';
            eqKrwEl.innerHTML = `INIT: ${fmtUsd(initialCapitalUsd)} | RETURN: <strong style="color:${retColor}">${isTotalPos ? '+' : ''}${totalCumulativeReturnPct.toFixed(2)}% (${isTotalPos ? '+' : ''}$${totalCumulativePnlUsd.toFixed(2)})</strong> <span style="font-size:10px; color:${rlzColor}; margin-left:4px;">[실현: ${realizedPnlPct >= 0 ? '+' : ''}${realizedPnlPct.toFixed(2)}%]</span>`;
        }
        // 2. Free Cash Card
        const cashUsdEl = document.getElementById("kpiFreeCashUsd");
        if (cashUsdEl) cashUsdEl.textContent = fmtUsd(freeCashUsd);
        const cashTextEl = document.getElementById("kpiCashRatioText");
        if (cashTextEl) {
            cashTextEl.textContent = `RATIO: ${cashRatioPct.toFixed(1)}% (KRW: ${freeCashKrw.toLocaleString()}) | ${availableSlots > 0 ? `${availableSlots} SLOT${availableSlots === 1 ? '' : 'S'} READY` : 'SLOTS FULL'}`;
            cashTextEl.style.color = availableSlots > 0 ? '#a7f3d0' : '#fca5a5';
        }
        // 3. Holdings Evaluation Card
        const evalUsdEl = document.getElementById("kpiHoldingsEvalUsd");
        if (evalUsdEl) evalUsdEl.textContent = fmtUsd(totalEvalUsd);
        const pnlTextEl = document.getElementById("kpiPnlText");
        if (pnlTextEl) {
            const isUnrealPos = unrealizedPnlUsd >= 0, isRlzPos = realizedPnlUsd >= 0;
            pnlTextEl.innerHTML = `평가: <span style="color:${isUnrealPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isUnrealPos ? '+' : ''}${unrealizedPnlPct.toFixed(2)}% (${isUnrealPos ? '+' : ''}$${unrealizedPnlUsd.toFixed(2)})</span> | 실현: <span style="color:${isRlzPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isRlzPos ? '+' : ''}${realizedPnlPct.toFixed(2)}% (${isRlzPos ? '+' : ''}$${realizedPnlUsd.toFixed(2)})</span>`;
        }
        // 4. Market Regime Card — MSI score + stance (SSOT)
        const dashMacro = (this.latestDashboardData && this.latestDashboardData.macro) || {};
        const msiScore = extractMsiScore(dashMacro);
        const msiStance = dashMacro.msi_stance || (dashMacro.macro_climate && dashMacro.macro_climate.msi_stance) || classifyMsiStance(msiScore);
        const msiColor = msiStance === "CASH_EXIT" ? "#f87171" : (msiStance === "DEFENSE_HOLD" ? "#c084fc" : (msiStance === "SELECTIVE_BUY" ? "#38bdf8" : "#34d399"));
        const regimeEl = document.getElementById("kpiRegimeBadge");
        if (regimeEl) { regimeEl.textContent = `MSI: ${msiScore.toFixed(1)} PT · ${msiStance}`; regimeEl.style.color = msiColor; }
        const regimeDescEl = document.getElementById("kpiRegimeDesc");
        if (regimeDescEl) { regimeDescEl.textContent = effectiveIsBull ? "[BULL] 50/30/20 + QQQ PROXY (1.5x IF VIX<20)" : "[BEAR] 25/25 SLOTS + QQQ CORE (LEV OFF)"; }
    },

    renderEngineOverlay(slotSummary, macro, portfolioData, riskConstitution) {
        const p = portfolioData || {};
        const holdings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];
        const proxyHoldings = holdings.filter((h) => isCashProxyTicker(h.ticker));
        const proxyVal = proxyHoldings.reduce((acc, h) => acc + Number(h.current_price || h.buy_price || 0) * Number(h.quantity || 0), 0);
        const proxyQty = proxyHoldings.reduce((acc, h) => acc + Number(h.quantity || 0), 0);
        const summary = slotSummary || {};
        const lev = (summary.leverage) || (macro && macro.leverage) || {};
        const leverageOn = Boolean(lev.leverage_mode || lev.qld_allowed);

        const qStatus = document.getElementById("qqqProxyStatus"), qDetail = document.getElementById("qqqProxyDetail");
        if (qStatus) { qStatus.textContent = proxyVal > 0 ? `PARKED $${proxyVal.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "IDLE CASH → QQQ READY"; qStatus.style.color = proxyVal > 0 ? "#38bdf8" : "#94a3b8"; }
        if (qDetail) { qDetail.textContent = proxyQty > 0 ? `QQQ/QLD ${proxyQty.toFixed(0)} sh · Cash Proxy Overlay` : "주도주 미진입 시 유휴 NAV를 QQQ에 파킹"; }

        const levStatus = document.getElementById("leverageStatus"), levDetail = document.getElementById("leverageDetail"), levBadge = document.getElementById("leverageBadge");
        const levLabel = leverageOn ? "1.5x (QLD Boost)" : "1.0x (QQQ Core)";
        if (levStatus) { levStatus.textContent = levLabel; levStatus.style.color = leverageOn ? "#fbbf24" : "#c084fc"; }
        if (levDetail) { const spyOk = lev.spy_above_sma200, vixOk = lev.vix_below_20; levDetail.textContent = `SPY≥SMA200=${spyOk === null || spyOk === undefined ? "?" : spyOk} · VIX<20=${vixOk === null || vixOk === undefined ? "?" : vixOk}`; }
        if (levBadge) {
            levBadge.textContent = leverageOn ? "LEV: 1.5x" : "LEV: 1.0x";
            levBadge.className = leverageOn ? "status-pill-chip active-green" : "status-pill-chip";
            levBadge.title = leverageOn ? "SPY≥SMA200 & VIX<20 — QQQ+QLD 1.5x boost active" : "Core 1.0x QQQ (leverage conditions not met)";
        }

        const autoBadge = document.getElementById("autopilotBadge");
        if (autoBadge && riskConstitution) {
            const sl = riskConstitution.stop_loss_pct ?? -0.05;
            autoBadge.title = `C1-M2 Autopilot · stop ${(sl * 100).toFixed(1)}% · 50/30/20 + QQQ Proxy`;
        }
    },

    renderSlotVisualizer(portfolioData, isBullRegime, topPick, slotSummary) {
        const grid = document.getElementById("slotVisualizerGrid");
        const summaryText = document.getElementById("slotSummaryText");
        if (!grid) return;

        const p = portfolioData || this.latestPortfolioData || (this.latestDashboardData && this.latestDashboardData.portfolio) || {};
        const allHoldings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];

        // Distinguish satellite leadership names from cash proxy (QQQ / QLD)
        const sats = allHoldings.filter((h) => !isCashProxyTicker(h.ticker));
        const proxies = allHoldings.filter((h) => isCashProxyTicker(h.ticker));

        // Unified list of slot assignments: satellite leadership picks first, then cash proxies fill residual slots
        const assignedHoldings = [...sats, ...proxies];

        const weights = isBullRegime ? SLOT_WEIGHTS_BULL : SLOT_WEIGHTS_BEAR;
        const maxSlots = weights.length;

        if (summaryText) {
            const weightLabel = isBullRegime ? "50/30/20" : "25/25";
            summaryText.textContent = `${sats.length} / ${maxSlots} SLOTS · ${weightLabel}`;
            if (sats.length === 0) {
                summaryText.style.color = '#94a3b8';
            } else if (sats.length < maxSlots) {
                summaryText.style.color = '#38bdf8';
            } else if (sats.length === maxSlots) {
                summaryText.style.color = '#34d399';
            } else {
                summaryText.style.color = '#fbbf24';
            }
        }

        const totalEquity = Number(p.total_equity_usd !== undefined ? p.total_equity_usd : (p.total_equity || 7500.0));
        const totalCards = Math.max(3, assignedHoldings.length);
        grid.style.gridTemplateColumns = totalCards > 3 ? 'repeat(auto-fit, minmax(160px, 1fr))' : 'repeat(3, 1fr)';

        let slotHtml = '';
        for (let i = 0; i < totalCards; i++) {
            const targetWeight = weights[i];
            const targetPctLabel = targetWeight !== undefined ? `${Math.round(targetWeight * 100)}%` : (isBullRegime ? "AUX" : (i === 2 ? "50%" : "AUX"));
            const targetUsd = targetWeight !== undefined ? Math.round(totalEquity * targetWeight) : 0;

            if (i < assignedHoldings.length) {
                const h = assignedHoldings[i];
                const isProxy = isCashProxyTicker(h.ticker);
                const pnl = Number(h.pnl_pct || 0);
                const isPos = pnl >= 0;
                const pnlColor = isPos ? '#34d399' : '#f87171';
                const curPrice = Number(h.current_price || h.buy_price || 0);
                const buyPrice = Number(h.buy_price || 0);
                const isTrailing = Boolean(h.is_trailing_active);
                const curQty = Number(h.quantity || 1);
                const curVal = Number(h.current_value || curPrice * curQty);
                const actualWeightPct = totalEquity > 0 ? ((curVal / totalEquity) * 100).toFixed(1) : '0.0';

                let badgeText = '[HOLDING]';
                let badgeStyle = 'background:rgba(255,255,255,0.08); color:var(--text-secondary);';
                if (isProxy) {
                    badgeText = '[CASH PROXY]';
                    badgeStyle = 'background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3);';
                } else if (isTrailing) {
                    badgeText = '[TRAILING]';
                    badgeStyle = 'background:rgba(251,191,36,0.15); color:#fbbf24; border:1px solid rgba(251,191,36,0.3);';
                }

                const stopLossPrice = Number(h.stop_loss_price || deriveStopPrice(buyPrice));
                const trailingFloor = Number(h.trailing_floor || deriveTargetPrice(buyPrice));
                const slOrTpText = isTrailing
                    ? `TP: $${trailingFloor.toFixed(2)}`
                    : `SL: $${stopLossPrice.toFixed(2)}`;

                let headerLabel;
                if (targetWeight !== undefined) {
                    headerLabel = `SLOT #${i + 1} TARGET ${targetPctLabel} · NOW ${actualWeightPct}%`;
                } else if (isProxy) {
                    headerLabel = `PROXY SLEEVE · NOW ${actualWeightPct}%`;
                } else {
                    headerLabel = `SLOT #${i + 1} AUX · NOW ${actualWeightPct}%`;
                }

                slotHtml += `<div class="slot-card occupied" style="background:var(--surface-card); border:1px solid var(--hairline-dark); border-top:3px solid ${isProxy ? '#38bdf8' : pnlColor}; border-radius:14px; padding:12px 14px; cursor:pointer;" onclick="window.TerminalUI.selectStock('${this.escapeHtml(h.ticker)}', ${curPrice})">` +
                    `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="font-size:10px; font-weight:800; color:var(--text-muted); font-family:'JetBrains Mono';">${headerLabel}</span><span style="font-size:9px; font-weight:800; padding:2px 8px; border-radius:9999px; font-family:'JetBrains Mono'; ${badgeStyle}">${badgeText}</span></div>` +
                    `<div style="display:flex; justify-content:space-between; align-items:baseline;"><strong style="font-size:15px; color:#ffffff; font-family:'JetBrains Mono';">${this.escapeHtml(h.ticker)}</strong><span style="font-size:14px; font-weight:800; color:${pnlColor}; font-family:'JetBrains Mono';">${isPos ? '+' : ''}${pnl.toFixed(1)}%</span></div>` +
                    `<div style="font-size:11px; color:var(--text-muted); margin-top:4px; display:flex; justify-content:space-between; font-family:'JetBrains Mono';"><span>$${curPrice.toFixed(2)} (${curQty} SH)</span><span style="color:${isTrailing ? '#fbbf24' : (isProxy ? '#38bdf8' : '#f87171')}; font-weight:700;">${slOrTpText}</span></div></div>`;
            } else if (targetWeight !== undefined) {
                slotHtml += `<div class="slot-card empty" style="background:var(--surface-deep); border:1px dashed rgba(52,211,153,0.3); border-radius:14px; padding:12px 14px; display:flex; flex-direction:column; justify-content:center; text-align:center;">` +
                    `<div style="font-size:10px; font-weight:800; color:#34d399; font-family:'JetBrains Mono';">SLOT #${i + 1} [EMPTY · ${targetPctLabel}]</div><div style="font-size:12px; font-weight:700; color:#ffffff; margin-top:3px; font-family:'JetBrains Mono';">$${targetUsd.toLocaleString()}</div><div style="font-size:10px; color:var(--text-muted); margin-top:3px; font-family:'JetBrains Mono';">C1-M2 진입 가능</div></div>`;
            } else {
                slotHtml += `<div class="slot-card locked" style="background:var(--surface-deep); border:1px dashed rgba(73,79,223,0.4); border-radius:14px; padding:12px 14px; display:flex; flex-direction:column; justify-content:center; text-align:center;">` +
                    `<div style="font-size:10px; font-weight:800; color:#c7d2fe; font-family:'JetBrains Mono';">SLOT #${i + 1} [CORE PROXY · 50%]</div><div style="font-size:12px; font-weight:700; color:#e0e7ff; margin-top:3px; font-family:'JetBrains Mono';">BEAR CORE QQQ</div><div style="font-size:10px; color:var(--text-muted); margin-top:3px; font-family:'JetBrains Mono';">위성 25/25 외 잔여 50% 파킹</div></div>`;
            }
        }
        grid.innerHTML = slotHtml;
    },

    renderTopPicks(topPick, runnerUp) {
        const container = document.getElementById("topPicksContainer");
        if (!container) return;

        const renderCard = (pick, rankNum, badgeBg, badgeColor) => {
            if (!pick) {
                return `<div style="background:var(--surface-card); border:1px dashed var(--hairline-dark); border-radius:16px; padding:16px; text-align:center; color:var(--text-muted); font-size:11px; font-family:'JetBrains Mono';">RANK #${rankNum} NO CANDIDATE</div>`;
            }

            const ticker = this.escapeHtml(pick.ticker || '');
            const name = this.escapeHtml(pick.name || ticker);
            const price = Number(pick.price || 0), score = Number(pick.conviction_score || 0), rs3m = Number(pick.rs_3m || pick.momentum_3m || 0), isBreakout = Boolean(pick.is_breakout);
            const sizing = pick.sizing || {}, shares = sizing.shares || 1, allocUsd = Number(sizing.allocated_usd || price * shares), allocKrw = sizing.allocated_krw || Math.round(allocUsd * 1380);

            return `<div class="top-pick-card" style="background:var(--surface-card); border:1px solid var(--hairline-dark); border-radius:16px; padding:14px 16px; cursor:pointer;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">` +
                `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="font-size:10px; font-weight:800; background:${badgeBg}; color:${badgeColor}; padding:2px 8px; border-radius:9999px; font-family:'JetBrains Mono';">${rankNum === 1 ? 'RANK #1' : 'RANK #2'}</span><span style="font-size:11px; font-weight:800; color:var(--primary-bright); font-family:'JetBrains Mono';">${score} PT</span></div>` +
                `<div style="display:flex; justify-content:space-between; align-items:baseline;"><div><span style="font-size:17px; font-weight:800; color:#ffffff; font-family:'JetBrains Mono';">${ticker}</span><span style="font-size:11.5px; color:var(--text-muted); margin-left:6px;">${name}</span></div><span style="font-size:15px; font-weight:800; color:#34d399; font-family:'JetBrains Mono';">$${price.toFixed(2)}</span></div>` +
                `<div style="font-size:11px; color:var(--text-secondary); margin-top:6px; font-family:'JetBrains Mono'; display:flex; justify-content:space-between;"><span>RS: <strong style="color:#fbbf24;">${rs3m >= 0 ? '+' : ''}${rs3m.toFixed(1)}%</strong> ${isBreakout ? '[돌파]' : ''}</span><span style="color:var(--primary-bright); font-weight:700;">${shares}주 ($${allocUsd.toFixed(0)})</span></div>` +
                `<div style="margin-top:10px;"><button class="btn-primary-pill" style="width:100%; padding:7px 10px; font-size:11px; font-weight:700; font-family:'JetBrains Mono';" onclick="event.stopPropagation(); window.TerminalUI.executeQuickBuy('${ticker}', ${price}, ${shares}, ${allocUsd}, ${allocKrw}, this)">BUY ${shares}주 ($${allocUsd.toFixed(0)})</button></div></div>`;
        };

        container.innerHTML = `${renderCard(topPick, 1, "var(--primary)", "#ffffff")}${renderCard(runnerUp, 2, "rgba(52,211,153,0.15)", "#34d399")}`;
    },

    async executeQuickBuy(ticker, price, shares, allocUsd, allocKrw, btnEl) {
        if (btnEl && btnEl.disabled) return;
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.textContent = "[PROCESSING...]";
        }

        try {
            const res = await ApiClient.buyStock(ticker, price, shares);
            console.log("[ORDER SUCCESS]", res);
            if (window.TerminalApp) window.TerminalApp.refreshData();
        } catch (err) {
            this.toast(`Order Failed: ${err.message}`, "error");
        } finally {
            if (btnEl) {
                setTimeout(() => {
                    btnEl.disabled = false;
                    btnEl.textContent = `[QUICK BUY ${shares} SH] ($${allocUsd.toFixed(0)})`;
                }, 2000);
            }
        }
    },

    renderPortfolio(portfolioData) {
        const pBody = document.getElementById("portfolioTableBody");
        if (!pBody) return;

        const p = portfolioData || {};
        this.latestPortfolioData = p;
        if (this.latestDashboardData) {
            this.latestDashboardData.portfolio = p;
        }

        const holdings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];

        if (holdings.length === 0) {
            pBody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:28px; font-family:'Inter', sans-serif; font-size:12px;">No active holdings. Select a top conviction pick or search above to enter a slot.</td></tr>`;
        } else {
            pBody.innerHTML = holdings.map(h => {
                const pnlVal = Number(h.pnl_pct || 0);
                const isPos = pnlVal >= 0;
                const pnlColor = isPos ? 'var(--accent-green)' : 'var(--accent-red)';
                const safeTicker = this.escapeHtml(h.ticker || '');
                const safeId = Number(h.id);
                const safeCurrentPrice = Number(h.current_price || h.buy_price || 0);
                const safeBuyPrice = Number(h.buy_price || 0);
                const safeQty = Number(h.quantity || 1);
                const pnlAmt = Number(h.pnl_amount !== undefined ? h.pnl_amount : (safeCurrentPrice - safeBuyPrice) * safeQty);
                const pnlAmtFormatted = `${pnlAmt >= 0 ? '+$' : '-$'}${Math.abs(pnlAmt).toFixed(2)}`;

                const stopLossP = Number(h.stop_loss_price || deriveStopPrice(safeBuyPrice));
                const trailingP = Number(h.trailing_floor || deriveTargetPrice(safeBuyPrice));
                const isTrailing = Boolean(h.is_trailing_active);

                return `<tr><td><strong class="ticker-pill ${isPos ? 'bull' : 'bear'}" style="cursor:pointer;" onclick="window.TerminalUI.selectStock('${safeTicker}', ${safeCurrentPrice})">${safeTicker}</strong></td>` +
                    `<td style="font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${safeBuyPrice.toFixed(2)}</td><td style="font-family:'JetBrains Mono'; font-weight:700; color:${pnlColor}; font-variant-numeric:tabular-nums;">$${safeCurrentPrice.toFixed(2)}</td>` +
                    `<td style="font-family:'JetBrains Mono'; font-weight:600; color:var(--primary-bright); font-variant-numeric:tabular-nums;">${safeQty}</td><td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">${isPos ? '+' : ''}${pnlVal.toFixed(2)}% <span style="font-size:10px; opacity:0.85;">(${pnlAmtFormatted})</span></td>` +
                    `<td style="color:#f87171; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${stopLossP.toFixed(2)}</td>` +
                    `<td style="color:#fbbf24; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${trailingP.toFixed(2)} ${isTrailing ? '<span style="font-size:9px; background:rgba(251,191,36,0.15); color:#fbbf24; padding:2px 6px; border-radius:9999px; border:1px solid rgba(251,191,36,0.3); font-weight:700;">[TRAILING]</span>' : ''}</td>` +
                    `<td><button class="btn-exit-holding" data-holding-id="${safeId}" data-current-price="${safeCurrentPrice}" style="background:rgba(226,59,74,0.12); color:#f87171; border:1px solid rgba(226,59,74,0.35); padding:3px 10px; border-radius:9999px; font-size:10px; cursor:pointer; font-weight:700; font-family:'Inter', sans-serif; transition:all 0.15s ease;">EXIT</button></td></tr>`;
            }).join('');

            // Attach exit event listeners
            pBody.querySelectorAll('.btn-exit-holding').forEach(btn => {
                btn.onclick = async (e) => {
                    const id = btn.getAttribute('data-holding-id');
                    const price = btn.getAttribute('data-current-price');
                    if (!confirm(`Execute full position liquidation? (Price: $${price})`)) return;
                    try {
                        btn.disabled = true;
                        btn.textContent = "CLOSING...";
                        await ApiClient.sellStock(id, price);
                        if (window.TerminalApp) window.TerminalApp.refreshData();
                    } catch (err) {
                        this.toast(`Exit Failed: ${err.message}`, "error");
                        btn.disabled = false;
                        btn.textContent = "EXIT";
                    }
                };
            });
        }

        const isBullRegime = (this.latestDashboardData && this.latestDashboardData.slot_allocation_summary && this.latestDashboardData.slot_allocation_summary.is_bull_regime !== undefined)
            ? this.latestDashboardData.slot_allocation_summary.is_bull_regime
            : true;

        // Synchronize C1-M2 Slot Allocator in 100% lockstep with Active Portfolio
        try {
            this.renderSlotVisualizer(
                p,
                isBullRegime,
                this.latestDashboardData ? this.latestDashboardData.top_conviction_pick : null,
                this.latestDashboardData ? this.latestDashboardData.slot_allocation_summary : null,
                this.latestDashboardData ? this.latestDashboardData.macro : null
            );
        } catch (e) {
            console.warn("[UI] renderSlotVisualizer sync error:", e);
        }

        // Synchronize QQQ Cash Proxy & Leverage overlay
        try {
            this.renderEngineOverlay(
                this.latestDashboardData ? this.latestDashboardData.slot_allocation_summary : null,
                this.latestDashboardData ? this.latestDashboardData.macro : null,
                p,
                this.latestDashboardData ? this.latestDashboardData.risk_constitution : null
            );
        } catch (e) {
            console.warn("[UI] renderEngineOverlay sync error:", e);
        }

        // Synchronize KPIs
        try {
            this.renderKPIs(
                this.latestDashboardData ? this.latestDashboardData.kpis : null,
                p,
                isBullRegime
            );
        } catch (e) {
            console.warn("[UI] renderKPIs sync error:", e);
        }
    },

    defaultChartTarget(data) {
        if (!data) return null;
        const named = [
            data.top_conviction_pick,
            data.top_conviction_runner_up,
            (data.tier1 && data.tier1[0]),
            (data.dual_consensus && data.dual_consensus[0]),
            (data.tier2 && data.tier2[0]),
            (data.strat1_exclusive && data.strat1_exclusive[0]),
        ];
        for (const item of named) {
            const tk = item && item.ticker;
            if (isMarketTicker(tk)) {
                return { ticker: String(tk).toUpperCase(), price: Number(item.price || item.current_price || 0) };
            }
        }
        const holdings = (data.portfolio && data.portfolio.holdings) || [];
        const real = holdings.find((h) => isMarketTicker(h && h.ticker));
        if (real) {
            return { ticker: String(real.ticker).toUpperCase(), price: Number(real.current_price || real.buy_price || 0) };
        }
        return null;
    },

    selectStock(ticker, price) {
        if (!ticker) return;
        const cleanTicker = ticker.trim().toUpperCase();
        if (!isMarketTicker(cleanTicker)) return;
        const p = Number(price || 0.0);
        this.currentSelectedTicker = cleanTicker;
        this.currentSelectedPrice = p;

        // Immediate Header DOM Update for zero-latency responsiveness
        const curTickerEl = document.getElementById("curTicker");
        const curPriceEl = document.getElementById("curPrice");
        if (curTickerEl) curTickerEl.textContent = cleanTicker;
        if (curPriceEl && p > 0) curPriceEl.textContent = `$${p.toFixed(2)}`;

        ChartEngine.loadChart(cleanTicker, p);
        this.updateQuickBuyConsole(cleanTicker, p);
    },

    updateQuickBuyConsole(ticker, price) {
        const p = price || 0.0;
        const qbTicker = document.getElementById("qbTicker");
        const qbPrice = document.getElementById("qbPrice");
        const qbBuyPrice = document.getElementById("qbBuyPrice");
        const qbStop = document.getElementById("qbStopVal");
        const qbTarget = document.getElementById("qbTargetVal");

        if (qbTicker) qbTicker.textContent = ticker;
        if (qbPrice) qbPrice.textContent = `$${p.toFixed(2)}`;
        if (qbBuyPrice) qbBuyPrice.value = p.toFixed(2);
        if (qbStop) qbStop.textContent = `$${deriveStopPrice(p).toFixed(2)}`;
        if (qbTarget) qbTarget.textContent = `$${deriveTargetPrice(p).toFixed(2)}`;
        const qbQty = document.getElementById("qbQty");
        if (qbQty && p > 0) qbQty.value = String(resolveQuickBuyQty(ticker, p, this.latestDashboardData, this.latestPortfolioData));
    },

    renderTradeLogs(logs) {
        const tBody = document.getElementById("tradeLogTableBody");
        const countEl = document.getElementById("tradeLogCount");
        if (!tBody) return;

        const tradeList = Array.isArray(logs) ? logs.slice(-this.MAX_LOGS) : [];
        if (countEl) countEl.textContent = `${tradeList.length} EXECUTIONS LOGGED`;

        if (tradeList.length === 0) {
            tBody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:18px; color:var(--text-muted); font-family:'JetBrains Mono'; font-size:11px;">No execution logs recorded yet.</td></tr>`;
            return;
        }

        tBody.innerHTML = tradeList.map(item => {
            const side = String(item.side || 'BUY').toUpperCase();
            const isBuy = side === 'BUY';
            const sideColor = isBuy ? '#34d399' : '#f87171';
            const sideBg = isBuy ? 'rgba(52,211,153,0.15)' : 'rgba(248,113,113,0.15)';
            const ticker = this.escapeHtml(item.ticker || '');
            const price = Number(item.price || 0);
            const qty = Number(item.quantity || item.qty || 1);
            const totAmt = Number(item.total_amount || (price * qty));
            const rawTs = this.escapeHtml(item.timestamp || '');
            const ts = rawTs.includes(' ') ? rawTs.split(' ')[1] : rawTs;
            const msg = this.escapeHtml(item.message || item.order_type || 'MARKETABLE_ORDER');
            const status = this.escapeHtml(item.status || 'FILLED');

            // Parse or compute Trade PnL %
            let pnlDisplay = '<span style="color:var(--text-muted); font-family:\'JetBrains Mono\'; font-size:10px;">[ENTRY]</span>';
            if (!isBuy) {
                let pnlVal = null;
                if (item.pnl_pct !== undefined && item.pnl_pct !== null && !isNaN(Number(item.pnl_pct))) {
                    pnlVal = Number(item.pnl_pct);
                } else {
                    const match = String(item.message || '').match(/([+\-]\d+(?:\.\d+)?)\s*%/);
                    if (match) {
                        pnlVal = parseFloat(match[1]);
                    }
                }

                if (pnlVal !== null) {
                    const isPos = pnlVal >= 0;
                    const pnlColor = isPos ? '#34d399' : '#f87171';
                    pnlDisplay = `<strong style="color:${pnlColor}; font-family:'JetBrains Mono'; font-size:11px;">${isPos ? '+' : ''}${pnlVal.toFixed(2)}%</strong>`;
                } else {
                    pnlDisplay = '<span style="color:var(--text-secondary); font-family:\'JetBrains Mono\'; font-size:10.5px;">-</span>';
                }
            }

            return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.1s ease;">` +
                `<td style="padding:7px 12px; font-family:'JetBrains Mono'; color:var(--text-muted); font-size:10.5px;">${ts}</td>` +
                `<td style="padding:7px 12px;"><span style="background:${sideBg}; color:${sideColor}; padding:2px 8px; border-radius:9999px; font-weight:800; font-family:'JetBrains Mono'; font-size:9.5px;">${side}</span></td>` +
                `<td style="padding:7px 12px;"><strong style="color:#ffffff; font-family:'JetBrains Mono'; cursor:pointer; font-size:11px;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">${ticker}</strong></td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:var(--primary-bright); font-weight:700; font-size:11px;">${qty} SH</td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:#ffffff; font-size:11px;">$${price.toFixed(2)}</td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; font-weight:700; color:#ffffff; font-size:11px;">$${totAmt.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>` +
                `<td style="padding:7px 12px; text-align:right;">${pnlDisplay}</td>` +
                `<td style="padding:7px 12px; font-family:'Inter', sans-serif; color:var(--text-secondary); font-size:10.5px; max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${msg}">${msg}</td>` +
                `<td style="padding:7px 12px; text-align:center;"><span style="background:rgba(52,211,153,0.12); color:#34d399; padding:2px 6px; border-radius:4px; font-size:9px; font-weight:700; font-family:'JetBrains Mono';">[${status}]</span></td></tr>`;
        }).join('');

        while (tBody.childElementCount > this.MAX_LOGS) {
            tBody.removeChild(tBody.firstElementChild);
        }
    }
};
