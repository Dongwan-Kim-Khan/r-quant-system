/**
 * R QUANT TERMINAL v2: UI RENDERING & EVENT DISPATCHER MODULE
 * Professional Bloomberg Dark Terminal Aesthetic (Zero Emojis).
 * Al-Sangmoo GS-Quant Upgraded 3-Slot Trading Cockpit.
 */
import { ApiClient } from './api.js?v=4.0.6';
import { ChartEngine } from './chart.js?v=4.0.6';
import { QuantDecoder } from './decoder.js?v=4.0.6';

export const UI = {
    currentSelectedTicker: "",
    currentSelectedPrice: 0.0,
    latestDashboardData: null,

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
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
        try { this.renderKPIs(data.kpis, data.portfolio, isBullRegime); } catch (e) { console.warn("[UI] renderKPIs error:", e); }
        try { this.renderSlotVisualizer(data.portfolio, isBullRegime, data.top_conviction_pick); } catch (e) { console.warn("[UI] renderSlotVisualizer error:", e); }
        try { this.renderTopPicks(data.top_conviction_pick, data.top_conviction_runner_up); } catch (e) { console.warn("[UI] renderTopPicks error:", e); }
        try { this.renderPortfolio(data.portfolio); } catch (e) { console.warn("[UI] renderPortfolio error:", e); }
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

    renderKPIs(kpis, portfolioData, isBullRegime) {
        const p = portfolioData || {};
        const holdings = Array.isArray(p.holdings) ? p.holdings : [];

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
        const availableSlots = Math.max(0, maxSlots - holdings.length);

        const initialCapitalUsd = Number(p.initial_capital_usd || p.base_account_usd || (totalEquityUsd - pnlAmt) || 7500.0);
        const totalCumulativePnlUsd = totalEquityUsd - initialCapitalUsd;
        const totalCumulativeReturnPct = (initialCapitalUsd > 0) ? (totalCumulativePnlUsd / initialCapitalUsd) * 100.0 : 0.0;
        const isTotalPos = totalCumulativePnlUsd >= 0;

        const realizedPnlUsd = Number(p.realized_pnl_amount || 0.0);
        const realizedPnlPct = Number(p.realized_pnl_pct || 0.0);
        const unrealizedPnlUsd = Number(p.unrealized_pnl_amount || 0.0);
        const unrealizedPnlPct = Number(p.unrealized_pnl_pct || 0.0);

        // 1. Total Equity Card
        const eqUsdEl = document.getElementById("kpiTotalEquityUsd");
        if (eqUsdEl) eqUsdEl.textContent = `$${totalEquityUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const eqKrwEl = document.getElementById("kpiTotalEquityKrw");
        if (eqKrwEl) {
            const retColor = isTotalPos ? 'var(--accent-green)' : 'var(--accent-red)';
            const rlzColor = realizedPnlUsd >= 0 ? '#34d399' : '#f87171';
            eqKrwEl.innerHTML = `INIT: $${initialCapitalUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} | RETURN: <strong style="color:${retColor}">${isTotalPos ? '+' : ''}${totalCumulativeReturnPct.toFixed(2)}% (${isTotalPos ? '+' : ''}$${totalCumulativePnlUsd.toFixed(2)})</strong> <span style="font-size:10px; color:${rlzColor}; margin-left:4px;">[실현: ${realizedPnlPct >= 0 ? '+' : ''}${realizedPnlPct.toFixed(2)}%]</span>`;
        }

        // 2. Free Cash Card
        const cashUsdEl = document.getElementById("kpiFreeCashUsd");
        if (cashUsdEl) cashUsdEl.textContent = `$${freeCashUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const cashTextEl = document.getElementById("kpiCashRatioText");
        if (cashTextEl) {
            cashTextEl.textContent = `RATIO: ${cashRatioPct.toFixed(1)}% (KRW: ${freeCashKrw.toLocaleString()}) | ${availableSlots > 0 ? `${availableSlots} SLOTS READY` : 'SLOTS FULL'}`;
            cashTextEl.style.color = availableSlots > 0 ? '#a7f3d0' : '#fca5a5';
        }

        // 3. Holdings Evaluation Card
        const evalUsdEl = document.getElementById("kpiHoldingsEvalUsd");
        if (evalUsdEl) evalUsdEl.textContent = `$${totalEvalUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const pnlTextEl = document.getElementById("kpiPnlText");
        if (pnlTextEl) {
            const isUnrealPos = unrealizedPnlUsd >= 0;
            const isRlzPos = realizedPnlUsd >= 0;
            pnlTextEl.innerHTML = `평가: <span style="color:${isUnrealPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isUnrealPos ? '+' : ''}${unrealizedPnlPct.toFixed(2)}% (${isUnrealPos ? '+' : ''}$${unrealizedPnlUsd.toFixed(2)})</span> | 실현: <span style="color:${isRlzPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isRlzPos ? '+' : ''}${realizedPnlPct.toFixed(2)}% (${isRlzPos ? '+' : ''}$${realizedPnlUsd.toFixed(2)})</span>`;
        }

        // 4. Market Regime Card
        const regimeEl = document.getElementById("kpiRegimeBadge");
        if (regimeEl) {
            regimeEl.textContent = effectiveIsBull ? "[BULL REGIME]" : "[BEAR REGIME]";
            regimeEl.style.color = effectiveIsBull ? "#34d399" : "#f87171";
        }
        const regimeDescEl = document.getElementById("kpiRegimeDesc");
        if (regimeDescEl) {
            regimeDescEl.textContent = effectiveIsBull ? "BULL MARKET: 3-SLOT 100% CAPITAL DEPLOYMENT" : "BEAR MARKET: 2-SLOT 50% CASH DEFENSE";
        }
    },

    renderSlotVisualizer(portfolioData, isBullRegime, topPick) {
        const grid = document.getElementById("slotVisualizerGrid");
        const summaryText = document.getElementById("slotSummaryText");
        if (!grid) return;

        const p = portfolioData || {};
        const holdings = Array.isArray(p.holdings) ? p.holdings : [];
        const maxSlots = isBullRegime ? 3 : 2;
        
        if (summaryText) {
            summaryText.textContent = `${holdings.length} / ${maxSlots} SLOTS OCCUPIED`;
            summaryText.style.color = holdings.length >= maxSlots ? '#f87171' : '#38bdf8';
        }

        const totalEquity = Number(p.total_equity_usd !== undefined ? p.total_equity_usd : (p.total_equity || 7500.0));
        const curHoldingsVal = holdings.reduce((acc, h) => acc + Number(h.current_value || (Number(h.current_price || h.buy_price || 0) * Number(h.quantity || 1))), 0);
        const maxEquityDeployable = totalEquity * (isBullRegime ? 1.0 : 0.50); // 100% in Bull, 50% in Bear
        const remainingDeployable = Math.max(0, maxEquityDeployable - curHoldingsVal);
        const emptySlotsCount = Math.max(1, maxSlots - holdings.length);
        const targetSlotUsd = Math.round(remainingDeployable / emptySlotsCount);
        const slotWeightPct = totalEquity > 0 ? ((targetSlotUsd / totalEquity) * 100).toFixed(1) : (isBullRegime ? '33.3' : '25.0');
        const cashReserveUsd = Math.round(totalEquity * 0.5);

        let slotHtml = '';
        for (let i = 0; i < 3; i++) {
            if (i < holdings.length) {
                // Occupied Slot
                const h = holdings[i];
                const pnl = Number(h.pnl_pct || 0);
                const isPos = pnl >= 0;
                const pnlColor = isPos ? '#34d399' : '#f87171';
                const curPrice = Number(h.current_price || h.buy_price || 0);
                const buyPrice = Number(h.buy_price || 0);
                const isTrailing = Boolean(h.is_trailing_active);
                const curQty = Number(h.quantity || 1);
                const curVal = Number(h.current_value || curPrice * curQty);
                const actualWeightPct = totalEquity > 0 ? ((curVal / totalEquity) * 100).toFixed(1) : '0.0';

                slotHtml += `
                    <div class="slot-card occupied" style="background:var(--surface-card); border:1px solid var(--hairline-dark); border-top:3px solid ${pnlColor}; border-radius:14px; padding:12px 14px; cursor:pointer;" onclick="window.TerminalUI.selectStock('${this.escapeHtml(h.ticker)}', ${curPrice})">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <span style="font-size:10px; font-weight:800; color:var(--text-muted); font-family:'JetBrains Mono';">SLOT #${i + 1} (${actualWeightPct}%)</span>
                            <span style="font-size:9px; font-weight:800; padding:2px 8px; border-radius:9999px; font-family:'JetBrains Mono'; ${isTrailing ? 'background:rgba(251,191,36,0.15); color:#fbbf24;' : 'background:rgba(255,255,255,0.08); color:var(--text-secondary);'}">${isTrailing ? '[TRAILING]' : '[HOLDING]'}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:baseline;">
                            <strong style="font-size:15px; color:#ffffff; font-family:'JetBrains Mono';">${this.escapeHtml(h.ticker)}</strong>
                            <span style="font-size:14px; font-weight:800; color:${pnlColor}; font-family:'JetBrains Mono';">${isPos ? '+' : ''}${pnl.toFixed(1)}%</span>
                        </div>
                        <div style="font-size:11px; color:var(--text-muted); margin-top:4px; display:flex; justify-content:space-between; font-family:'JetBrains Mono';">
                            <span>$${curPrice.toFixed(2)} (${curQty} SH)</span>
                            <span style="color:${isTrailing ? '#fbbf24' : '#f87171'}; font-weight:700;">${isTrailing ? `TP: $${Number(h.trailing_floor || curPrice * 0.93).toFixed(2)}` : `SL: $${Number(h.stop_loss_price || buyPrice * 0.96).toFixed(2)}`}</span>
                        </div>
                    </div>
                `;
            } else if (i < maxSlots) {
                // Empty Available Slot (Dynamically sizes to fill the 50% Bear or 100% Bull Cap)
                slotHtml += `
                    <div class="slot-card empty" style="background:var(--surface-deep); border:1px dashed rgba(52,211,153,0.3); border-radius:14px; padding:12px 14px; display:flex; flex-direction:column; justify-content:center; text-align:center;">
                        <div style="font-size:10px; font-weight:800; color:#34d399; font-family:'JetBrains Mono';">SLOT #${i + 1} [EMPTY]</div>
                        <div style="font-size:12px; font-weight:700; color:#ffffff; margin-top:3px; font-family:'JetBrains Mono';">$${targetSlotUsd.toLocaleString()} (${slotWeightPct}%)</div>
                        <div style="font-size:10px; color:var(--text-muted); margin-top:3px; font-family:'JetBrains Mono';">진입 가능</div>
                    </div>
                `;
            } else {
                // Locked Slot (Bear Mode 50% Cash Defense)
                slotHtml += `
                    <div class="slot-card locked" style="background:var(--surface-deep); border:1px dashed rgba(73,79,223,0.4); border-radius:14px; padding:12px 14px; display:flex; flex-direction:column; justify-content:center; text-align:center;">
                        <div style="font-size:10px; font-weight:800; color:#c7d2fe; font-family:'JetBrains Mono';">SLOT #${i + 1} [현금 방어]</div>
                        <div style="font-size:12px; font-weight:700; color:#e0e7ff; margin-top:3px; font-family:'JetBrains Mono';">50% 대피 ($${cashReserveUsd.toLocaleString()})</div>
                        <div style="font-size:10px; color:var(--text-muted); margin-top:3px; font-family:'JetBrains Mono';">하락장 쉴드</div>
                    </div>
                `;
            }
        }
        grid.innerHTML = slotHtml;
    },

    renderTopPicks(topPick, runnerUp) {
        const container = document.getElementById("topPicksContainer");
        if (!container) return;

        const renderCard = (pick, rankNum, badgeBg, badgeColor) => {
            if (!pick) {
                return `
                    <div style="background:var(--surface-card); border:1px dashed var(--hairline-dark); border-radius:16px; padding:16px; text-align:center; color:var(--text-muted); font-size:11px; font-family:'JetBrains Mono';">
                        RANK #${rankNum} NO CANDIDATE
                    </div>
                `;
            }

            const ticker = this.escapeHtml(pick.ticker || '');
            const name = this.escapeHtml(pick.name || ticker);
            const price = Number(pick.price || 0);
            const score = Number(pick.conviction_score || 0);
            const rs3m = Number(pick.rs_3m || pick.momentum_3m || 0);
            const isBreakout = Boolean(pick.is_breakout);
            const sizing = pick.sizing || {};
            const shares = sizing.shares || 1;
            const allocUsd = Number(sizing.allocated_usd || price * shares);
            const allocKrw = sizing.allocated_krw || Math.round(allocUsd * 1380);

            return `
                <div class="top-pick-card" style="background:var(--surface-card); border:1px solid var(--hairline-dark); border-radius:16px; padding:14px 16px; cursor:pointer;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <span style="font-size:10px; font-weight:800; background:${badgeBg}; color:${badgeColor}; padding:2px 8px; border-radius:9999px; font-family:'JetBrains Mono';">${rankNum === 1 ? 'RANK #1' : 'RANK #2'}</span>
                        <span style="font-size:11px; font-weight:800; color:var(--primary-bright); font-family:'JetBrains Mono';">${score} PT</span>
                    </div>

                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <div>
                            <span style="font-size:17px; font-weight:800; color:#ffffff; font-family:'JetBrains Mono';">${ticker}</span>
                            <span style="font-size:11.5px; color:var(--text-muted); margin-left:6px;">${name}</span>
                        </div>
                        <span style="font-size:15px; font-weight:800; color:#34d399; font-family:'JetBrains Mono';">$${price.toFixed(2)}</span>
                    </div>

                    <div style="font-size:11px; color:var(--text-secondary); margin-top:6px; font-family:'JetBrains Mono'; display:flex; justify-content:space-between;">
                        <span>RS: <strong style="color:#fbbf24;">${rs3m >= 0 ? '+' : ''}${rs3m.toFixed(1)}%</strong> ${isBreakout ? '[돌파]' : ''}</span>
                        <span style="color:var(--primary-bright); font-weight:700;">${shares}주 ($${allocUsd.toFixed(0)})</span>
                    </div>

                    <div style="margin-top:10px;">
                        <button class="btn-primary-pill" style="width:100%; padding:7px 10px; font-size:11px; font-weight:700; font-family:'JetBrains Mono';" onclick="event.stopPropagation(); window.TerminalUI.executeQuickBuy('${ticker}', ${price}, ${shares}, ${allocUsd}, ${allocKrw}, this)">
                            BUY ${shares}주 ($${allocUsd.toFixed(0)})
                        </button>
                    </div>
                </div>
            `;
        };

        container.innerHTML = `
            ${renderCard(topPick, 1, "var(--primary)", "#ffffff")}
            ${renderCard(runnerUp, 2, "rgba(52,211,153,0.15)", "#34d399")}
        `;
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
            alert(`Order Failed: ${err.message}`);
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
        const holdings = Array.isArray(p.holdings) ? p.holdings : [];

        if (holdings.length === 0) {
            pBody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:28px; font-family:'Inter', sans-serif; font-size:12px;">No active holdings. Select a top conviction pick or search above to enter a slot.</td></tr>`;
            return;
        }

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

            const stopLossP = Number(h.stop_loss_price || safeBuyPrice * 0.96);
            const trailingP = Number(h.trailing_floor || (safeBuyPrice * 1.15));
            const isTrailing = Boolean(h.is_trailing_active);

            return `
                <tr>
                    <td><strong class="ticker-pill ${isPos ? 'bull' : 'bear'}" style="cursor:pointer;" onclick="window.TerminalUI.selectStock('${safeTicker}', ${safeCurrentPrice})">${safeTicker}</strong></td>
                    <td style="font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${safeBuyPrice.toFixed(2)}</td>
                    <td style="font-family:'JetBrains Mono'; font-weight:700; color:${pnlColor}; font-variant-numeric:tabular-nums;">$${safeCurrentPrice.toFixed(2)}</td>
                    <td style="font-family:'JetBrains Mono'; font-weight:600; color:var(--primary-bright); font-variant-numeric:tabular-nums;">${safeQty}</td>
                    <td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">${isPos ? '+' : ''}${pnlVal.toFixed(2)}% <span style="font-size:10px; opacity:0.85;">(${pnlAmtFormatted})</span></td>
                    <td style="color:#f87171; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${stopLossP.toFixed(2)}</td>
                    <td style="color:#fbbf24; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">
                        $${trailingP.toFixed(2)} ${isTrailing ? '<span style="font-size:9px; background:rgba(251,191,36,0.15); color:#fbbf24; padding:2px 6px; border-radius:9999px; border:1px solid rgba(251,191,36,0.3); font-weight:700;">[TRAILING]</span>' : ''}
                    </td>
                    <td>
                        <button class="btn-exit-holding" data-holding-id="${safeId}" data-current-price="${safeCurrentPrice}" style="background:rgba(226,59,74,0.12); color:#f87171; border:1px solid rgba(226,59,74,0.35); padding:3px 10px; border-radius:9999px; font-size:10px; cursor:pointer; font-weight:700; font-family:'Inter', sans-serif; transition:all 0.15s ease;">EXIT</button>
                    </td>
                </tr>
            `;
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
                    alert(`Exit Failed: ${err.message}`);
                    btn.disabled = false;
                    btn.textContent = "EXIT";
                }
            };
        });
    },

    selectStock(ticker, price) {
        if (!ticker) return;
        const cleanTicker = ticker.trim().toUpperCase();
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
        if (qbStop) qbStop.textContent = `$${(p * 0.96).toFixed(2)}`;
        if (qbTarget) qbTarget.textContent = `$${(p * 1.15).toFixed(2)}`;
    },

    renderTradeLogs(logs) {
        const tBody = document.getElementById("tradeLogTableBody");
        const countEl = document.getElementById("tradeLogCount");
        if (!tBody) return;

        const tradeList = Array.isArray(logs) ? logs : [];
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

            return `
                <tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.1s ease;">
                    <td style="padding:7px 12px; font-family:'JetBrains Mono'; color:var(--text-muted); font-size:10.5px;">${ts}</td>
                    <td style="padding:7px 12px;"><span style="background:${sideBg}; color:${sideColor}; padding:2px 8px; border-radius:9999px; font-weight:800; font-family:'JetBrains Mono'; font-size:9.5px;">${side}</span></td>
                    <td style="padding:7px 12px;"><strong style="color:#ffffff; font-family:'JetBrains Mono'; cursor:pointer; font-size:11px;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">${ticker}</strong></td>
                    <td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:var(--primary-bright); font-weight:700; font-size:11px;">${qty} SH</td>
                    <td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:#ffffff; font-size:11px;">$${price.toFixed(2)}</td>
                    <td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; font-weight:700; color:#ffffff; font-size:11px;">$${totAmt.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td style="padding:7px 12px; text-align:right;">${pnlDisplay}</td>
                    <td style="padding:7px 12px; font-family:'Inter', sans-serif; color:var(--text-secondary); font-size:10.5px; max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${msg}">${msg}</td>
                    <td style="padding:7px 12px; text-align:center;"><span style="background:rgba(52,211,153,0.12); color:#34d399; padding:2px 6px; border-radius:4px; font-size:9px; font-weight:700; font-family:'JetBrains Mono';">[${status}]</span></td>
                </tr>
            `;
        }).join('');
    }
};
