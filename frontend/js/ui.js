/**
 * R QUANT TERMINAL: UI RENDERING & EVENT DISPATCHER MODULE
 * Renders 3-Tier Cards, Signal Tracker, Macro Gauges, Portfolio Tables, and Search.
 */

import { ApiClient } from './api.js';
import { ChartEngine } from './chart.js';
import { QuantDecoder } from './decoder.js';

export const UI = {
    currentSelectedTicker: "NVDA",
    currentSelectedPrice: 0.0,
    latestDashboardData: null,

    // Safe HTML Escape to prevent XSS (SEC-01 compliance)
    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    /**
     * Render full dashboard feed
     */
    renderDashboard(data) {
        if (!data) return;
        this.latestDashboardData = data;

        const updatedTime = data.last_updated || (data.kpis && data.kpis.last_updated) || new Date().toLocaleString();
        const lastUpEl = document.getElementById("lastUpdated");
        if (lastUpEl) lastUpEl.textContent = `Updated: ${updatedTime}`;

        // 1. Macro Climate & Gauges
        this.renderMacro(data.macro);

        // 2. KPIs
        this.renderKPIs(data.kpis);

        // 3. 3-Tier Tactical Quant Signal Cards
        this.renderTacticalCards(data);

        // 4. Unified Signal Tracker Table
        this.renderSignalTracker(data.signal_tracker || []);

        // 5. Portfolio Table
        this.renderPortfolio(data.portfolio || []);

        // 6. Recommendation History
        this.renderDailyHistory(data.daily_history || []);
    },

    renderMacro(macro) {
        if (!macro) return;

        // Macro Climate & Directive
        if (macro.macro_climate) {
            const mc = macro.macro_climate;
            const hl = document.getElementById("macroHeadline");
            const dt = document.getElementById("macroDirectiveText");
            const sb = document.getElementById("macroStanceBadge");
            const ms = document.getElementById("msiScoreBadge");
            const needle = document.getElementById("msiNeedle");

            if (hl && mc.action_headline) hl.textContent = mc.action_headline;
            if (dt && mc.narrative) dt.textContent = mc.narrative;
            if (sb && mc.macro_stance) sb.textContent = mc.macro_stance;

            if (mc.msi_score !== undefined) {
                if (ms) ms.textContent = `${mc.msi_score.toFixed(1)} / 100`;
                if (needle) {
                    const pct = Math.min(100, Math.max(0, mc.msi_score));
                    needle.style.left = `${pct}%`;
                }
            }

            // MSI Factor Breakdown
            if (mc.breakdown) {
                const b = mc.breakdown;
                const hVal = document.getElementById("msiHardVal");
                const hSub = document.getElementById("msiHardSub");
                const nVal = document.getElementById("msiNlpVal");
                const nSub = document.getElementById("msiNlpSub");
                const sVal = document.getElementById("msiShockVal");
                const sSub = document.getElementById("msiShockSub");

                if (hVal && b.hard_4axis_score !== undefined) hVal.textContent = `${b.hard_4axis_score.toFixed(1)} / 60.0 pt`;
                if (hSub && b.hard_stance) hSub.textContent = `금융 4대 축: ${b.hard_stance}`;

                if (nVal && b.nlp_sentiment_score !== undefined) nVal.textContent = `${b.nlp_sentiment_score.toFixed(1)} / 25.0 pt`;
                if (nSub && b.nlp_stance) nSub.textContent = `방송 지침: ${b.nlp_stance}`;

                if (sVal && b.shock_penalty_score !== undefined) sVal.textContent = `${b.shock_penalty_score.toFixed(1)} / 15.0 pt`;
                if (sSub && b.shock_stance) sSub.textContent = `외생 충격: ${b.shock_stance}`;
            }
        }

        // Live Financial Gauges
        if (macro.gauges) {
            const g = macro.gauges;
            const updateGauge = (valId, badgeId, obj) => {
                if (!obj) return;
                const vEl = document.getElementById(valId);
                const bEl = document.getElementById(badgeId);
                if (vEl && obj.val) vEl.textContent = obj.val;
                if (bEl && obj.badge) {
                    bEl.textContent = obj.badge;
                    const isGood = obj.badge === 'SOFT' || obj.badge === 'NORMAL' || obj.badge === 'STABLE';
                    bEl.style.background = isGood ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
                    bEl.style.color = isGood ? '#34d399' : '#f87171';
                }
            };

            updateGauge("gaugeUs10yVal", "gaugeUs10yBadge", g.us10y);
            updateGauge("gaugeDxyVal", "gaugeDxyBadge", g.dxy);
            updateGauge("gaugeVixVal", "gaugeVixBadge", g.vix);
            updateGauge("gaugeWtiVal", "gaugeWtiBadge", g.wti);
            updateGauge("gaugeGoldVal", "gaugeGoldBadge", g.gold);
        }

        if (macro.stream_title) {
            const sLink = document.getElementById("streamLink");
            if (sLink) sLink.textContent = macro.stream_title;
        }
        if (macro.macro_summary) {
            const mNarr = document.getElementById("macroNarrative");
            if (mNarr) mNarr.textContent = `| ${macro.macro_summary}`;
        }
    },

    renderKPIs(kpis) {
        if (!kpis) return;
        const countEl = document.getElementById("kpiCount");
        const invEl = document.getElementById("kpiInvested");
        const evalEl = document.getElementById("kpiEval");
        const pnlEl = document.getElementById("kpiPnl");

        if (countEl && kpis.active_count !== undefined) countEl.textContent = kpis.active_count;
        if (invEl && kpis.total_invested !== undefined) invEl.textContent = `$${Number(kpis.total_invested).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        if (evalEl && kpis.total_eval !== undefined) evalEl.textContent = `$${Number(kpis.total_eval).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        if (pnlEl && kpis.total_pnl_pct !== undefined) {
            const pnlPct = Number(kpis.total_pnl_pct);
            const pnlVal = Number(kpis.total_pnl_val || 0);
            const isPos = pnlPct >= 0;
            pnlEl.textContent = `${isPos ? '+' : ''}${pnlPct.toFixed(2)}% ($${isPos ? '+' : ''}${pnlVal.toFixed(2)})`;
            pnlEl.style.color = isPos ? 'var(--accent-green)' : 'var(--accent-red)';
        }
    },

    renderTacticalCards(data) {
        const renderGroup = (containerId, items, defaultBorder) => {
            const container = document.getElementById(containerId);
            if (!container) return;
            if (!items || items.length === 0) {
                container.innerHTML = '<div style="color:var(--text-muted); font-size:11px; padding:8px 0; text-align:center;">조건 만족 종목 없음</div>';
                return;
            }

            container.innerHTML = items.map(item => {
                const tk = this.escapeHtml(item.ticker);
                const name = this.escapeHtml(item.name || tk);
                const price = Number(item.price || 0);
                const score = item.score !== undefined ? item.score : 90;
                const reason = this.escapeHtml(item.reason || item.quant_reason || '조건 부합');
                const isActive = tk === this.currentSelectedTicker ? 'active' : '';

                return `
                    <div class="rec-item ${isActive}" data-ticker="${tk}" data-price="${price}" style="border-left: 3px solid ${defaultBorder};">
                        <div>
                            <div class="rec-item-title">${tk} <span style="font-size:11px; color:#94a3b8; font-weight:normal;">${name}</span></div>
                            <div class="rec-item-sub">${reason}</div>
                        </div>
                        <div style="text-align:right;">
                            <div class="rec-item-price">$${price.toFixed(2)}</div>
                            <div style="font-size:10px; color:${defaultBorder}; font-weight:bold; font-family:'JetBrains Mono';">${score} pt</div>
                        </div>
                    </div>
                `;
            }).join('');
        };

        // Tier 2: Structural Pullback
        renderGroup("strat1ExclusiveCards", data.strat1_exclusive || [], "#10b981");

        // Tier 1: Dual Consensus Macro Leader
        renderGroup("dualConsensusCards", data.dual_consensus || [], "#fbbf24");

        // Tier 3: Cloud Bounce Sniper
        renderGroup("strat2ExclusiveCards", data.strat2_exclusive || [], "#f87171");
    },

    renderSignalTracker(signals) {
        const tbody = document.getElementById("signalTrackerBody");
        if (!tbody) return;

        if (!signals || signals.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:16px;">활성화된 전략 신호가 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = signals.map(sig => {
            const tk = this.escapeHtml(sig.ticker);
            const name = this.escapeHtml(sig.name || tk);
            const price = Number(sig.price || 0);
            const score = sig.score || 90;
            const origin = sig.origin || 'QUANT_DISCOVERY';
            const sector = this.escapeHtml(sig.sector || '-');

            let tierBadge = '<span class="strategy-pill strategy-pullback-pill">TIER 2 (눌림목)</span>';
            if (sig.tier === 'TIER_1' || origin.includes('DUAL') || origin.includes('VIKINGS')) {
                tierBadge = '<span class="origin-badge origin-vikings">TIER 1 (거시주도)</span>';
            } else if (sig.tier === 'TIER_3' || sig.strategy_type === 'STRATEGY_2_SNIPER') {
                tierBadge = '<span class="strategy-pill strategy-sniper-pill">TIER 3 (스나이퍼)</span>';
            }

            const originBadge = origin.includes('VIKINGS') 
                ? '<span class="origin-badge origin-vikings">VIKINGS</span>'
                : '<span class="origin-badge origin-quant">QUANT</span>';

            const tgt = (price * 1.15).toFixed(2);
            const stop = (price * 0.97).toFixed(2);

            return `
                <tr style="cursor:pointer;" onclick="window.TerminalUI.selectStock('${tk}', ${price})">
                    <td><span class="ticker-pill bull">${tk}</span> <span style="color:#94a3b8; font-size:11px;">${name}</span></td>
                    <td>${tierBadge}</td>
                    <td>${originBadge}</td>
                    <td style="font-family:'JetBrains Mono'; font-weight:700;">$${price.toFixed(2)}</td>
                    <td style="font-family:'JetBrains Mono'; color:#34d399;">$${tgt} (+15%)</td>
                    <td style="font-family:'JetBrains Mono'; color:#f87171;">$${stop} (-3%)</td>
                    <td><span style="font-family:'JetBrains Mono'; font-weight:800; color:#fbbf24;">${score} pt</span></td>
                </tr>
            `;
        }).join('');
    },

    renderPortfolio(holdings) {
        const tbody = document.getElementById("portfolioTableBody");
        if (!tbody) return;

        if (!holdings || holdings.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:16px;">보유 중인 포지션이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = holdings.map(h => {
            const id = h.id;
            const tk = this.escapeHtml(h.ticker);
            const qty = h.qty || 1;
            const buyPrice = Number(h.buy_price || 0);
            const curPrice = Number(h.current_price || buyPrice);
            const pnlPct = Number(h.pnl_pct || (((curPrice - buyPrice)/buyPrice)*100));
            const isPos = pnlPct >= 0;

            return `
                <tr>
                    <td><span class="ticker-pill ${isPos ? 'bull' : 'bear'}" onclick="window.TerminalUI.selectStock('${tk}', ${curPrice})">${tk}</span></td>
                    <td style="font-family:'JetBrains Mono';">${qty}주</td>
                    <td style="font-family:'JetBrains Mono';">$${buyPrice.toFixed(2)}</td>
                    <td style="font-family:'JetBrains Mono'; font-weight:700;">$${curPrice.toFixed(2)}</td>
                    <td style="font-family:'JetBrains Mono'; font-weight:700; color:${isPos ? '#34d399' : '#f87171'};">
                        ${isPos ? '+' : ''}${pnlPct.toFixed(2)}%
                    </td>
                    <td style="font-size:11px; color:#94a3b8;">${this.escapeHtml(h.exit_advice || '보유')}</td>
                    <td>
                        <button class="btn-exit-holding" data-holding-id="${id}" data-current-price="${curPrice}" style="background:#ef4444; border:none; color:white; padding:3px 8px; border-radius:2px; font-size:10px; cursor:pointer; font-weight:700;">
                            청산
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    },

    renderDailyHistory(history) {
        const tbody = document.getElementById("dailyRecHistoryBody");
        if (!tbody) return;

        if (!history || history.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:16px;">추천 이력이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = history.map(rec => {
            const dateStr = this.escapeHtml(rec.date);
            const renderPills = (arr, cls) => {
                if (!arr || arr.length === 0) return '-';
                return arr.map(item => {
                    const tk = typeof item === 'string' ? item : item.ticker;
                    const price = typeof item === 'object' ? item.price : null;
                    return `<span class="ticker-pill ${cls}" onclick="window.TerminalUI.selectStock('${this.escapeHtml(tk)}', ${price})">${this.escapeHtml(tk)}</span>`;
                }).join(' ');
            };

            return `
                <tr>
                    <td style="font-family:'JetBrains Mono'; font-weight:700;">${dateStr}</td>
                    <td>${renderPills(rec.bull_picks, 'bull')}</td>
                    <td>${renderPills(rec.neutral_picks, 'neutral')}</td>
                    <td>${renderPills(rec.bear_picks, 'bear')}</td>
                </tr>
            `;
        }).join('');
    },

    async selectStock(ticker, price = null) {
        this.currentSelectedTicker = ticker;
        const curTickerEl = document.getElementById("curTicker");
        const qbTickerEl = document.getElementById("qbTicker");
        if (curTickerEl) curTickerEl.textContent = ticker;
        if (qbTickerEl) qbTickerEl.textContent = ticker;

        // Update active highlight on cards
        document.querySelectorAll(".rec-item").forEach(card => {
            if (card.getAttribute("data-ticker") === ticker) card.classList.add("active");
            else card.classList.remove("active");
        });

        if (price !== null && Number(price) > 0) {
            this.currentSelectedPrice = Number(price);
            const formatted = `$${this.currentSelectedPrice.toFixed(2)}`;
            const cpEl = document.getElementById("curPrice");
            const qpEl = document.getElementById("qbPrice");
            const bpEl = document.getElementById("qbBuyPrice");
            if (cpEl) cpEl.textContent = formatted;
            if (qpEl) qpEl.textContent = formatted;
            if (bpEl) bpEl.value = this.currentSelectedPrice.toFixed(2);
            this.updateTargetStop();
        }

        // Fetch chart data via ApiClient
        const chartData = await ApiClient.getChartData(ticker);
        if (chartData && !chartData.aborted && chartData.candles) {
            this.currentSelectedPrice = Number(chartData.latest_close);
            const formatted = `$${this.currentSelectedPrice.toFixed(2)}`;
            const cpEl = document.getElementById("curPrice");
            const qpEl = document.getElementById("qbPrice");
            const bpEl = document.getElementById("qbBuyPrice");
            if (cpEl) cpEl.textContent = formatted;
            if (qpEl) qpEl.textContent = formatted;
            if (bpEl) bpEl.value = this.currentSelectedPrice.toFixed(2);
            this.updateTargetStop();

            ChartEngine.renderData(chartData);
            QuantDecoder.update(ticker, chartData, this.latestDashboardData);
        } else if (!chartData || !chartData.aborted) {
            ChartEngine.renderNotFound(ticker);
            QuantDecoder.update(ticker, null, this.latestDashboardData);
        }
    },

    updateTargetStop() {
        const buyInput = document.getElementById("qbBuyPrice");
        const price = buyInput ? parseFloat(buyInput.value) : this.currentSelectedPrice;
        if (isNaN(price) || price <= 0) return;

        const tgtP = (price * 1.15).toFixed(2);
        const stopP = (price * 0.97).toFixed(2);
        const tgtEl = document.getElementById("qbTargetVal");
        const stopEl = document.getElementById("qbStopVal");

        if (tgtEl) tgtEl.textContent = `$${tgtP}`;
        if (stopEl) stopEl.textContent = `$${stopP}`;
    },

    initSearch() {
        const input = document.getElementById("stockSearchInput");
        const dropdown = document.getElementById("searchDropdown");
        if (!input || !dropdown) return;

        let debounceTimer = null;
        input.addEventListener("input", (e) => {
            clearTimeout(debounceTimer);
            const q = e.target.value.trim();
            if (!q) {
                dropdown.style.display = "none";
                return;
            }
            debounceTimer = setTimeout(async () => {
                const results = await ApiClient.searchStocks(q);
                if (results.length === 0) {
                    dropdown.innerHTML = '<div style="padding:10px 14px; color:#64748b; font-size:11px;">검색 결과 없음 (Enter 누르면 직접 조회)</div>';
                    dropdown.style.display = "block";
                    return;
                }
                dropdown.innerHTML = results.map(r => `
                    <div class="search-result-item" data-ticker="${this.escapeHtml(r.ticker)}" style="padding:8px 12px; border-bottom:1px solid #1e293b; cursor:pointer; display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <span style="color:#38bdf8; font-weight:700; font-family:'JetBrains Mono';">${this.escapeHtml(r.ticker)}</span>
                            <span style="color:#cbd5e1; font-size:11px; margin-left:6px;">${this.escapeHtml(r.name)}</span>
                        </div>
                        <span style="color:#64748b; font-size:10px;">${this.escapeHtml(r.sector || '')}</span>
                    </div>
                `).join('');
                dropdown.style.display = "block";
            }, 180);
        });

        dropdown.addEventListener("click", (e) => {
            const item = e.target.closest(".search-result-item");
            if (item) {
                const tk = item.getAttribute("data-ticker");
                input.value = tk;
                dropdown.style.display = "none";
                this.selectStock(tk);
            }
        });

        // Keyboard Shortcut '/'
        window.addEventListener("keydown", (e) => {
            if (e.key === "/" && document.activeElement !== input) {
                e.preventDefault();
                input.focus();
            }
        });
    },

    initDelegation() {
        // Event delegation for portfolio sell buttons
        const pBody = document.getElementById("portfolioTableBody");
        if (pBody) {
            pBody.addEventListener("click", async (e) => {
                const btn = e.target.closest(".btn-exit-holding");
                if (btn) {
                    const id = parseInt(btn.getAttribute("data-holding-id"), 10);
                    const price = parseFloat(btn.getAttribute("data-current-price"));
                    if (!isNaN(id) && !isNaN(price)) {
                        try {
                            btn.disabled = true;
                            btn.textContent = "...";
                            await ApiClient.sellHolding(id, price);
                            const fresh = await ApiClient.getDashboardData();
                            this.renderDashboard(fresh);
                        } catch (err) {
                            alert(err.message || '청산 처리 실패');
                            btn.disabled = false;
                            btn.textContent = "청산";
                        }
                    }
                }
            });
        }

        // Quick Buy Button
        const btnBuy = document.getElementById("btnExecuteBuy");
        if (btnBuy) {
            btnBuy.addEventListener("click", async () => {
                const ticker = this.currentSelectedTicker;
                const buyInput = document.getElementById("qbBuyPrice");
                const price = buyInput ? parseFloat(buyInput.value) : this.currentSelectedPrice;
                const qty = 1;
                if (!ticker || isNaN(price) || price <= 0) return;

                try {
                    btnBuy.disabled = true;
                    btnBuy.textContent = "주문 중...";
                    await ApiClient.buyHolding({ ticker, buy_price: price, qty });
                    const fresh = await ApiClient.getDashboardData();
                    this.renderDashboard(fresh);
                } catch (err) {
                    alert(err.message || '매수 주문 실패');
                } finally {
                    btnBuy.disabled = false;
                    btnBuy.textContent = "포트폴리오 즉시 매수";
                }
            });
        }

        // Reset Portfolio Button
        const btnReset = document.getElementById("btnResetPortfolio");
        if (btnReset) {
            btnReset.addEventListener("click", async () => {
                if (!confirm("시뮬레이션 포트폴리오를 전체 초기화하시겠습니까?")) return;
                try {
                    await ApiClient.resetPortfolio();
                    const fresh = await ApiClient.getDashboardData();
                    this.renderDashboard(fresh);
                } catch (err) {
                    alert(err.message || '초기화 실패');
                }
            });
        }

        // Scan Now Button
        const btnScan = document.getElementById("btnScanNow");
        if (btnScan) {
            btnScan.addEventListener("click", async () => {
                try {
                    btnScan.disabled = true;
                    btnScan.textContent = "스캔 진행 중...";
                    await ApiClient.triggerScanNow();
                } catch (err) {
                    alert(err.message || '스캔 시작 실패');
                    btnScan.disabled = false;
                    btnScan.textContent = "Run Live Scan";
                }
            });
        }
    }
};

window.TerminalUI = UI;
