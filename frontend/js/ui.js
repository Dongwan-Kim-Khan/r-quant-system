// R QUANT TERMINAL v2: UI RENDERING & EVENT DISPATCHER MODULE (C1-M2 Cockpit)
import { ApiClient } from './api.js?v=4.4.8';
import { ChartEngine } from './chart.js?v=4.4.8';
import { QuantDecoder } from './decoder.js?v=4.4.8';
import { deriveStopPrice, deriveTargetPrice, isMarketTicker, SLOT_WEIGHTS_BULL, SLOT_WEIGHTS_BEAR, isCashProxyTicker, extractMsiScore, classifyMsiStance, resolveQuickBuyQty } from './constants.js?v=4.4.8';

export const UI = {
    currentSelectedTicker: "",
    currentSelectedPrice: 0.0,
    latestDashboardData: null,
    latestPortfolioData: null,
    MAX_LOGS: 300,
    MAX_TOASTS: 5,
    recentTradesQueue: [],
    _lastErrorToasts: {},
    systemHealthState: {
        ws: "CONNECTING",
        kis: "unknown",       // "live" | "simulation" | "unreachable" | "unknown"
        guardian: "unknown",  // "active" | "standby" | "unknown"
        autopilot: "unknown", // "active" | "standby" | "unknown"
        leverage: "1.5x",
    },

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

    _holdingsMarkUsd(holdings) {
        const rows = Array.isArray(holdings) ? holdings : [];
        return rows.reduce((sum, h) => {
            if (!h || typeof h !== "object") return sum;
            const qty = Number(h.quantity || 0);
            const px = Number(h.current_price || h.buy_price || 0);
            let marked = (qty > 0 && px > 0) ? qty * px : 0;
            if (marked <= 0) marked = Number(h.current_value || h.eval_amount || 0);
            return sum + Math.max(0, marked);
        }, 0);
    },

    sanitizePortfolioNav(portfolio) {
        if (!portfolio || typeof portfolio !== "object") return portfolio;
        const p = Object.assign({}, portfolio);
        const mark = this._holdingsMarkUsd(p.holdings);
        const equity = Number(p.total_equity_usd || 0);
        const cash = Math.max(0, Number(p.free_cash_usd || 0));
        if (mark > 1 && equity / mark > 3) {
            p.total_equity_usd = mark + cash;
            p.total_eval = mark;
            p.equity_source = p.equity_source && String(p.equity_source).toUpperCase() === "BROKER"
                ? "HOLDINGS"
                : (p.equity_source || "LOCAL");
        }
        return p;
    },

    preferLivePortfolio(feedPortfolio) {
        const live = this.latestPortfolioData;
        const feed = this.sanitizePortfolioNav(feedPortfolio);
        if (live && Array.isArray(live.holdings)) {
            return this.sanitizePortfolioNav(live);
        }
        return feed;
    },

    renderDashboard(data) {
        if (!data) return;
        const livePort = this.preferLivePortfolio(data.portfolio);
        if (livePort) data.portfolio = livePort;
        this.latestDashboardData = data;
        
        const updatedTime = data.last_updated || (data.kpis && data.kpis.last_updated) || new Date().toLocaleTimeString();
        const lastUpEl = document.getElementById("lastUpdated");
        if (lastUpEl) lastUpEl.textContent = `UPDATED: ${updatedTime}`;

        // Error detection & non-repeating toast
        const errors = [];
        if (data.portfolio_error) errors.push(data.portfolio_error);
        if (data.universe_sectors_error) errors.push(data.universe_sectors_error);
        if (data.execution_logs_error) errors.push("체결 내역을 불러오지 못했습니다.");
        if (data.macro_error) errors.push(data.macro_error);

        this._lastErrorToasts = this._lastErrorToasts || {};
        errors.forEach(errMsg => {
            if (!this._lastErrorToasts[errMsg]) {
                this.toast(errMsg, "error");
                this._lastErrorToasts[errMsg] = true;
            }
        });
        Object.keys(this._lastErrorToasts).forEach(k => {
            if (!errors.includes(k)) {
                delete this._lastErrorToasts[k];
            }
        });

        const isBullRegime = (data.slot_allocation_summary && data.slot_allocation_summary.is_bull_regime !== undefined) 
            ? data.slot_allocation_summary.is_bull_regime 
            : true;

        try { this.renderMediaWall(data.macro, data.execution_logs, isBullRegime, data.slot_allocation_summary); } catch (e) { console.warn("[UI] renderMediaWall error:", e); }
        try { this.renderMacro(data.macro, isBullRegime); } catch (e) { console.warn("[UI] renderMacro error:", e); }
        try { this.renderDynamicSectors(data.universe_sectors, data.universe_sectors_error); } catch (e) { console.warn("[UI] renderDynamicSectors error:", e); }
        try { this.renderPortfolio(livePort); } catch (e) { console.warn("[UI] renderPortfolio error:", e); }
        try { this.renderTopPicks(data.top_conviction_pick, data.top_conviction_runner_up); } catch (e) { console.warn("[UI] renderTopPicks error:", e); }
        try { this.renderTradeLogs(data.execution_logs, data.execution_logs_error); } catch (e) { console.warn("[UI] renderTradeLogs error:", e); }
        try { this.renderRegime(data.macro, isBullRegime, data.slot_allocation_summary); } catch (e) { console.warn("[UI] renderRegime error:", e); }
    },

    renderMacro(macro, isBullRegime) {
        if (!macro) return;
        const mc = macro.macro_climate || {};
        const mg = macro.macro_gauges || {};

        const msiEl = document.getElementById("mediaWallMsi");
        if (msiEl) {
            let dt = "";
            if (this.latestDashboardData && this.latestDashboardData.last_updated) {
                const dPart = String(this.latestDashboardData.last_updated).split(' ')[0];
                const parts = dPart.split('-');
                if (parts.length >= 3) dt = parts[1] + '/' + parts[2];
                else dt = dPart;
            } else {
                const now = new Date();
                dt = String(now.getMonth()+1).padStart(2, '0') + '/' + String(now.getDate()).padStart(2, '0');
            }
            msiEl.textContent = `MSI:${extractMsiScore(macro).toFixed(1)} ${dt}`;
            msiEl.style.color = "#fbbf24";
        }
    },

    renderDynamicSectors(sectors, errorMsg) {
        const host = document.getElementById("dynamicSectorsContainer");
        if (!host) return;
        if (errorMsg || sectors === undefined || sectors === null) {
            host.innerHTML = `<div style="color:var(--text-muted);font-size:11px;text-align:center;padding:8px;">SECTOR LOAD FAILED</div>`;
            return;
        }
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
        const p = this.sanitizePortfolioNav(portfolioData || {});
        const holdings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];

        const effectiveIsBull = (isBullRegime !== undefined && isBullRegime !== null)
            ? Boolean(isBullRegime)
            : ((this.latestDashboardData && this.latestDashboardData.slot_allocation_summary && this.latestDashboardData.slot_allocation_summary.is_bull_regime !== undefined)
                ? this.latestDashboardData.slot_allocation_summary.is_bull_regime
                : true);

        const totalEquityUsd = Number(p.total_equity_usd !== undefined ? p.total_equity_usd : (p.total_equity || 0.0));
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

        const initialCapitalUsd = Number(p.initial_capital_usd || p.base_account_usd || 0.0);
        const totalCumulativePnlUsd = p.overall_pnl_amount !== undefined ? Number(p.overall_pnl_amount) : (totalEquityUsd - initialCapitalUsd);
        const totalCumulativeReturnPct = p.overall_pnl_pct !== undefined ? Number(p.overall_pnl_pct) : (initialCapitalUsd > 0 ? (totalCumulativePnlUsd / initialCapitalUsd) * 100.0 : 0.0);
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
            eqKrwEl.innerHTML = `INIT: ${fmtUsd(initialCapitalUsd)} | RETURN: <strong style="color:${retColor}">${isTotalPos ? '+' : ''}${totalCumulativeReturnPct.toFixed(2)}% (${isTotalPos ? '+' : ''}${fmtUsd(totalCumulativePnlUsd)})</strong>`;
        }
        // 2. Free Cash Card
        const cashUsdEl = document.getElementById("kpiFreeCashUsd");
        if (cashUsdEl) cashUsdEl.textContent = fmtUsd(freeCashUsd);
        const cashTextEl = document.getElementById("kpiCashRatioText");
        if (cashTextEl) {
            cashTextEl.textContent = `${cashRatioPct.toFixed(1)}% · ${availableSlots > 0 ? `${availableSlots} SLOT${availableSlots === 1 ? '' : 'S'} READY` : 'SLOTS FULL'}`;
            cashTextEl.style.color = availableSlots > 0 ? '#a7f3d0' : '#fca5a5';
        }
        // 3. Holdings Evaluation Card
        const evalUsdEl = document.getElementById("kpiHoldingsEvalUsd");
        if (evalUsdEl) evalUsdEl.textContent = fmtUsd(totalEvalUsd);
        const pnlTextEl = document.getElementById("kpiPnlText");
        if (pnlTextEl) {
            const isUnrealPos = unrealizedPnlUsd >= 0, isRlzPos = realizedPnlUsd >= 0;
            pnlTextEl.innerHTML = `평가: <span style="color:${isUnrealPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isUnrealPos ? '+' : ''}${unrealizedPnlPct.toFixed(2)}% (${isUnrealPos ? '+' : ''}${fmtUsd(unrealizedPnlUsd)})</span> | 실현: <span style="color:${isRlzPos ? 'var(--accent-green)' : 'var(--accent-red)'}">${isRlzPos ? '+' : ''}${realizedPnlPct.toFixed(2)}% (${isRlzPos ? '+' : ''}${fmtUsd(realizedPnlUsd)})</span>`;
        }
        const srcEl = document.getElementById("equitySourceTag");
        if (srcEl) {
            const src = String(p.equity_source || "LOCAL").toUpperCase();
            const synced = p.broker_synced_at ? ` · ${p.broker_synced_at}` : "";
            if (src === "BROKER") {
                srcEl.textContent = `BROKER SSOT${synced}`;
                srcEl.style.color = "#34d399";
            } else if (src === "HOLDINGS") {
                srcEl.textContent = `LOT NAV${synced}`;
                srcEl.style.color = "#38bdf8";
            } else {
                srcEl.textContent = "LOCAL LEDGER";
                srcEl.style.color = "var(--text-muted)";
            }
        }
        // 4. Market Regime Card — S&P 500 Price vs SMA200 (SSOT)
        const dashMacro = (this.latestDashboardData && this.latestDashboardData.macro) || {};
        const slotSum = (this.latestDashboardData && this.latestDashboardData.slot_allocation_summary) || {};
        try { this.renderRegime(dashMacro, effectiveIsBull, slotSum); } catch (e) { console.warn("[UI] renderRegime error:", e); }
        try { this.renderAllocationPie(p, holdings, totalEquityUsd, freeCashUsd); } catch (e) { console.warn("[UI] renderAllocationPie error:", e); }
    },

    _donutSlice(cx, cy, rOut, rIn, startFrac, endFrac) {
        const tau = Math.PI * 2;
        const a0 = startFrac * tau - Math.PI / 2;
        const a1 = endFrac * tau - Math.PI / 2;
        const large = (endFrac - startFrac) > 0.5 ? 1 : 0;
        const p = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
        const [x0, y0] = p(rOut, a0);
        const [x1, y1] = p(rOut, a1);
        const [xi1, yi1] = p(rIn, a1);
        const [xi0, yi0] = p(rIn, a0);
        return `M ${x0.toFixed(3)} ${y0.toFixed(3)} A ${rOut} ${rOut} 0 ${large} 1 ${x1.toFixed(3)} ${y1.toFixed(3)} L ${xi1.toFixed(3)} ${yi1.toFixed(3)} A ${rIn} ${rIn} 0 ${large} 0 ${xi0.toFixed(3)} ${yi0.toFixed(3)} Z`;
    },

    renderAllocationPie(portfolio, holdings, totalEquityUsd, freeCashUsd) {
        const svg = document.getElementById("portfolioPie");
        const legend = document.getElementById("portfolioPieLegend");
        if (!svg || !legend) return;
        const equity = Math.max(0, Number(totalEquityUsd || 0));
        const cash = Math.max(0, Number(freeCashUsd || 0));
        const rows = Array.isArray(holdings) ? holdings : [];
        const palette = ["#34d399", "#fbbf24", "#c084fc", "#f87171", "#60a5fa", "#fb7185", "#22d3ee"];
        const slices = [];
        rows.forEach((h, i) => {
            const qty = Number(h.quantity || 0);
            const px = Number(h.current_price || h.buy_price || 0);
            const val = Number(h.current_value || (qty * px) || 0);
            if (val <= 0) return;
            const tk = String(h.ticker || "").toUpperCase();
            slices.push({
                ticker: tk,
                value: val,
                color: tk === "QLD" ? "#818cf8" : (isCashProxyTicker(tk) ? "#38bdf8" : palette[i % palette.length]),
            });
        });
        if (cash > 0.5) slices.push({ ticker: "CASH", value: cash, color: "#64748b" });
        const total = slices.reduce((s, x) => s + x.value, 0) || equity || 1;
        const fmt = (n) => `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
        if (!slices.length) {
            svg.innerHTML = `<circle cx="60" cy="60" r="44" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="16"/><text x="60" y="64" text-anchor="middle" fill="#94a3b8" font-size="9" font-family="JetBrains Mono">EMPTY</text>`;
            legend.innerHTML = `<li><span class="alloc-name" style="color:var(--text-muted);">NO LOTS</span></li>`;
            return;
        }
        let acc = 0;
        const paths = [];
        const full = slices.length === 1 && slices[0].value / total > 0.999;
        slices.forEach((sl) => {
            const frac = sl.value / total;
            if (full) {
                paths.push(`<circle cx="60" cy="60" r="36" fill="none" stroke="${sl.color}" stroke-width="16"/>`);
            } else {
                const start = acc;
                acc += frac;
                paths.push(`<path d="${this._donutSlice(60, 60, 44, 28, start, acc)}" fill="${sl.color}"></path>`);
            }
        });
        svg.innerHTML = `${paths.join("")}<circle cx="60" cy="60" r="24" fill="var(--surface-deep)"/>`;
        legend.innerHTML = slices.map((sl) => {
            const pct = (sl.value / (equity || total)) * 100;
            return `<li><span class="alloc-swatch" style="background:${sl.color}"></span><span class="alloc-name">${this.escapeHtml(sl.ticker)}</span><span class="alloc-pct">${pct.toFixed(1)}%</span><span class="alloc-val">${fmt(sl.value)}</span></li>`;
        }).join("");
    },

    isConfirmedFill(trade) {
        if (!trade || typeof trade !== "object") return false;
        if (trade.filled === true) return true;
        if (trade.filled === false) return false;
        const action = String(trade.action || "").toUpperCase();
        const status = String(trade.broker_status || trade.status || "").toUpperCase();
        if (action.includes("SUBMITTED") || action.includes("PENDING")) return false;
        if (status.includes("SUBMITTED") || status.includes("PENDING") || status.includes("AWAITING")) return false;
        return true;
    },

    shouldShowTradeAlert(trade) {
        if (!trade || typeof trade !== "object") return false;
        const action = String(trade.action || "").toUpperCase();
        const status = String(trade.broker_status || trade.status || "").toUpperCase();
        if (action.includes("PENDING") || status.includes("PERSISTENT_ORDER_PENDING")) return false;
        return true;
    },

    formatExecutionTs(raw) {
        const s = String(raw || "").trim();
        if (!s) return "--";
        const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)/);
        if (m) return `${m[1]}-${m[2]}-${m[3]} ${m[4]}`;
        return s;
    },

    addMediaWallTradeAlert(trade) {
        if (!this.shouldShowTradeAlert(trade)) return;
        const alertObj = {
            ...trade,
            _alertId: Date.now() + Math.random(),
            _addedAt: Date.now(),
        };
        this.recentTradesQueue.unshift(alertObj);
        if (this.recentTradesQueue.length > 5) this.recentTradesQueue.pop();

        const dash = this.latestDashboardData || {};
        const isBull = (dash.slot_allocation_summary && dash.slot_allocation_summary.is_bull_regime !== undefined)
            ? dash.slot_allocation_summary.is_bull_regime : true;
        this.renderMediaWall(dash.macro, null, isBull, dash.slot_allocation_summary);

        // Auto-remove after 12 seconds so recent fill alert slides away
        setTimeout(() => {
            this.recentTradesQueue = (this.recentTradesQueue || []).filter(t => t._alertId !== alertObj._alertId);
            const d = this.latestDashboardData || {};
            const b = (d.slot_allocation_summary && d.slot_allocation_summary.is_bull_regime !== undefined)
                ? d.slot_allocation_summary.is_bull_regime : true;
            this.renderMediaWall(d.macro, null, b, d.slot_allocation_summary);
        }, 12000);
    },

    renderMediaWall(macro, executionLogs, isBullRegime, slotSummary) {
        const track = document.getElementById("mediaWallTrack");
        if (!track) return;
        const m = macro || (this.latestDashboardData && this.latestDashboardData.macro) || {};
        const mg = m.macro_gauges || {};
        const lev = (slotSummary && slotSummary.leverage) || m.leverage || {};
        const spyClose = Number(lev.spy_close || 760.79);
        const spySma = Number(lev.spy_sma200 || 713.42);
        const spyDiff = spySma > 0 ? ((spyClose - spySma) / spySma * 100).toFixed(1) : "0.0";
        const qqqClose = Number(m.qqq_close || (slotSummary && slotSummary.qqq_close) || 709.76);

        const items = [];

        // 1. MSI Gate Indicator (Format: MSI:81.9 mm/dd)
        const mc = m.macro_climate || {};
        const msiScore = (mc.msi_score !== undefined) ? Number(mc.msi_score) : (m.msi_score !== undefined ? Number(m.msi_score) : null);
        const dateStr = String((this.latestDashboardData && (this.latestDashboardData.last_updated || (this.latestDashboardData.kpis && this.latestDashboardData.kpis.last_updated))) || m.fetched_at || "");
        const dateMatch = dateStr.match(/\d{4}-(\d{2})-(\d{2})/);
        const mmdd = dateMatch ? `${dateMatch[1]}/${dateMatch[2]}` : `${String(new Date().getMonth() + 1).padStart(2, '0')}/${String(new Date().getDate()).padStart(2, '0')}`;
        if (msiScore !== null) {
            const msiColor = msiScore >= 75 ? "#f87171" : (msiScore >= 50 ? "#fbbf24" : "#34d399");
            items.push(`<span class="media-wall-item"><strong style="color:${msiColor};">MSI: ${msiScore.toFixed(1)} (${mmdd})</strong></span>`);
        }

        // 2. Core Regime Indicator
        items.push(`<span class="media-wall-item"><strong style="color:${isBullRegime ? '#34d399' : '#f87171'};">[${isBullRegime ? 'BULL REGIME' : 'BEAR REGIME'}]</strong> SPY≥SMA200 (${isBullRegime ? '+' : ''}${spyDiff}%)</span>`);

        // 3. Real-time Trade Alerts (방금 발생한 거래만 표시, 시간 경과 후 자동 소멸)
        const trades = [...(this.recentTradesQueue || [])];
        for (const t of trades.slice(0, 3)) {
            if (!this.shouldShowTradeAlert(t)) continue;
            const filled = this.isConfirmedFill(t);
            const side = String(t.side || t.action || "TRADE").toUpperCase();
            const isBuy = side.includes("BUY");
            const alertClass = filled
                ? (isBuy ? "media-wall-trade-alert" : "media-wall-trade-alert alert-sell")
                : "media-wall-trade-alert alert-submitted";
            const tk = this.escapeHtml(String(t.ticker || ""));
            const shares = Number(t.shares || t.quantity || t.qty || 0);
            const px = Number(t.buy_price || t.sell_price || t.price || 0);
            const pnl = (filled && t.pnl_pct !== undefined && t.pnl_pct !== null && Number(t.pnl_pct) !== 0)
                ? ` (${Number(t.pnl_pct) >= 0 ? '+' : ''}${Number(t.pnl_pct).toFixed(1)}%)` : '';
            const tag = filled ? "[체결]" : "[접수]";
            items.push(`<span class="${alertClass}"><span class="trade-pulse"></span>⚡ ${tag} ${tk} ${side} ${shares > 0 ? `${shares}주 ` : ''}@ $${px.toFixed(2)}${pnl}</span>`);
        }

        // 4. Financial Macro Climate Gauges
        if (mg.us10y) items.push(`<span class="media-wall-item"><span>US10Y:</span> <strong style="color:#f87171;">${mg.us10y.val}%</strong></span>`);
        if (mg.dxy) items.push(`<span class="media-wall-item"><span>DXY:</span> <strong style="color:#38bdf8;">${mg.dxy.val}</strong></span>`);
        if (mg.vix) items.push(`<span class="media-wall-item"><span>VIX:</span> <strong style="color:#34d399;">${mg.vix.val}</strong></span>`);
        if (mg.wti) items.push(`<span class="media-wall-item"><span>WTI:</span> <strong style="color:#f87171;">$${mg.wti.val}</strong></span>`);
        if (mg.gold) items.push(`<span class="media-wall-item"><span>GOLD:</span> <strong style="color:#fbbf24;">$${mg.gold.val}</strong></span>`);

        // 5. Benchmarks
        items.push(`<span class="media-wall-item"><span>S&amp;P 500:</span> <strong style="color:#38bdf8;">$${spyClose.toFixed(2)}</strong> (SMA200: $${spySma.toFixed(2)})</span>`);
        items.push(`<span class="media-wall-item"><span>QQQ:</span> <strong style="color:#c084fc;">$${qqqClose.toFixed(2)}</strong></span>`);

        // Join items and duplicate for continuous seamless marquee translation
        const divider = '<span style="color:rgba(255,255,255,0.2); margin:0 8px;">•</span>';
        const contentHtml = items.join(divider);
        track.innerHTML = contentHtml + divider + contentHtml;
    },

    renderRegime(macro, isBullRegime, slotSummary) {
        const m = macro || (this.latestDashboardData && this.latestDashboardData.macro) || {};
        const lev = (slotSummary && slotSummary.leverage) || m.leverage || {};
        const spyClose = Number(lev.spy_close || 760.79);
        const spySma200 = Number(lev.spy_sma200 || 713.42);
        const effectiveIsBull = (isBullRegime !== undefined && isBullRegime !== null)
            ? Boolean(isBullRegime)
            : (spyClose >= spySma200);

        const diffPct = spySma200 > 0 ? ((spyClose - spySma200) / spySma200 * 100) : 0;
        const isDiffPos = diffPct >= 0;

        const tagEl = document.getElementById("regimeStatusTag");
        if (tagEl) {
            tagEl.textContent = effectiveIsBull ? "SPY ≥ SMA200" : "SPY < SMA200";
            tagEl.className = effectiveIsBull ? "status-badge status-good" : "status-badge status-bad";
        }

        const badgeEl = document.getElementById("kpiRegimeBadge");
        if (badgeEl) {
            badgeEl.textContent = effectiveIsBull ? "[BULL REGIME]" : "[BEAR REGIME]";
            badgeEl.style.color = effectiveIsBull ? "var(--accent-green)" : "var(--accent-red)";
        }

        const diffEl = document.getElementById("spySmaDiff");
        if (diffEl) {
            diffEl.textContent = `${isDiffPos ? '+' : ''}${diffPct.toFixed(1)}% ${isDiffPos ? 'ABOVE' : 'BELOW'} SMA`;
            diffEl.style.color = isDiffPos ? "#38bdf8" : "#f87171";
        }

        const spyPxEl = document.getElementById("spyCurrentPrice");
        if (spyPxEl) spyPxEl.textContent = `$${spyClose.toFixed(2)}`;

        const smaPxEl = document.getElementById("spySma200Price");
        if (smaPxEl) smaPxEl.textContent = `$${spySma200.toFixed(2)}`;



        // Time-series chart rendering
        const history = m.spy_history || (slotSummary && slotSummary.spy_history);
        this.renderRegimeChart(history, spyClose, spySma200, effectiveIsBull);
    },

    renderRegimeChart(history, currentSpy, currentSma, isBull) {
        const svg = document.getElementById("regimeSvgChart");
        if (!svg) return;

        let points = [];
        if (Array.isArray(history) && history.length >= 10) {
            points = history.map(h => ({
                date: h.date,
                close: Number(h.close),
                sma: Number(h.sma200 || currentSma),
            }));
        } else {
            // Synthetic smooth trajectory leading to current quotes
            const N = 45;
            const startSma = currentSma * 0.985;
            const startSpy = isBull ? currentSma * 0.965 : currentSma * 1.025;
            for (let i = 0; i < N; i++) {
                const prog = i / (N - 1);
                const s = startSma + (currentSma - startSma) * prog;
                const wave = Math.sin(prog * Math.PI * 3.5) * (currentSma * 0.007);
                const c = startSpy + (currentSpy - startSpy) * Math.pow(prog, 0.9) + wave;
                points.push({ close: c, sma: s });
            }
            points[points.length - 1].close = currentSpy;
            points[points.length - 1].sma = currentSma;
        }

        if (!points.length) return;

        const width = 280;
        const height = 54;
        const padTop = 6;
        const padBottom = 8;
        const drawH = height - padTop - padBottom;

        const allValues = [];
        points.forEach(p => {
            allValues.push(p.close);
            if (p.sma) allValues.push(p.sma);
        });
        const minVal = Math.min(...allValues) * 0.995;
        const maxVal = Math.max(...allValues) * 1.005;
        const valRange = (maxVal - minVal) || 1;

        const getX = (idx) => (idx / (points.length - 1)) * width;
        const getY = (val) => height - padBottom - ((val - minVal) / valRange) * drawH;

        let spyPathD = `M ${getX(0).toFixed(1)} ${getY(points[0].close).toFixed(1)}`;
        for (let i = 1; i < points.length; i++) {
            spyPathD += ` L ${getX(i).toFixed(1)} ${getY(points[i].close).toFixed(1)}`;
        }

        let smaPathD = `M ${getX(0).toFixed(1)} ${getY(points[0].sma).toFixed(1)}`;
        for (let i = 1; i < points.length; i++) {
            smaPathD += ` L ${getX(i).toFixed(1)} ${getY(points[i].sma).toFixed(1)}`;
        }

        let areaPathD = spyPathD + ` L ${width} ${height} L 0 ${height} Z`;

        const spyPathEl = document.getElementById("regimeSpyPath");
        if (spyPathEl) {
            spyPathEl.setAttribute("d", spyPathD);
            spyPathEl.setAttribute("stroke", isBull ? "#34d399" : "#f87171");
        }

        const smaPathEl = document.getElementById("regimeSmaPath");
        if (smaPathEl) {
            smaPathEl.setAttribute("d", smaPathD);
        }

        const areaPathEl = document.getElementById("regimeAreaPath");
        if (areaPathEl) {
            areaPathEl.setAttribute("d", areaPathD);
            areaPathEl.setAttribute("fill", isBull ? "url(#bullGrad)" : "url(#bearGrad)");
        }

        const dotEl = document.getElementById("regimeSpyDot");
        if (dotEl) {
            const lastX = getX(points.length - 1);
            const lastY = getY(points[points.length - 1].close);
            dotEl.setAttribute("cx", lastX.toFixed(1));
            dotEl.setAttribute("cy", lastY.toFixed(1));
            dotEl.setAttribute("fill", isBull ? "#34d399" : "#f87171");
        }
    },

    updateSystemHealth(partial) {
        this.systemHealthState = { ...this.systemHealthState, ...(partial || {}) };
        const st = this.systemHealthState;

        const isWsOk = st.ws === "CONNECTED";
        const isWsWarn = st.ws === "CONNECTING" || st.ws === "REAUTHENTICATING";
        const isKisLive = st.kis === "live";
        const isKisSimulation = st.kis === "simulation";
        const isKisFail = st.kis === "unreachable" || st.kis === "unknown";

        const isGuardianActive = st.guardian === "active" || st.guardian === true;
        const isGuardianStandby = st.guardian === "standby" || st.guardian === false;
        const isGuardianFail = st.guardian === "unreachable" || st.guardian === "unknown";

        const isAutopilotActive = st.autopilot === "active" || st.autopilot === true;
        const isAutopilotStandby = st.autopilot === "standby" || st.autopilot === false;
        const isAutopilotFail = st.autopilot === "unreachable" || st.autopilot === "unknown";

        // Overall status: Green (All OK), Yellow (Partial/Warn), Red (Severe Error)
        let overall = "GREEN";
        let labelText = "LIVE";
        if (st.ws === "DISCONNECTED" || st.ws === "ERROR") {
            overall = "RED";
            labelText = "ERROR";
        } else if (isKisFail || isGuardianFail || isAutopilotFail || !isWsOk) {
            overall = "YELLOW";
            labelText = "WARN";
        } else if (isKisSimulation || isGuardianStandby || isAutopilotStandby) {
            overall = "YELLOW";
            if (isGuardianStandby && isAutopilotStandby) labelText = "STANDBY";
            else if (isGuardianStandby) labelText = "DISABLED";
            else if (isAutopilotStandby) labelText = "STANDBY";
            else labelText = "WARN";
        }

        const sign = document.getElementById("systemStatusSign");
        const label = document.getElementById("systemStatusLabel");
        if (sign && label) {
            sign.className = `system-status-sign ${overall === 'GREEN' ? 'status-green' : (overall === 'YELLOW' ? 'status-yellow' : 'status-red')}`;
            label.textContent = labelText;
        }

        const tag = document.getElementById("popoverOverallTag");
        if (tag) {
            tag.className = `popover-tag ${overall === 'GREEN' ? 'tag-green' : (overall === 'YELLOW' ? 'tag-yellow' : 'tag-red')}`;
            tag.textContent = overall === 'GREEN' ? 'ALL NORMAL' : (overall === 'YELLOW' ? 'PARTIAL / WARN' : 'CRITICAL ERROR');
        }

        // Popover detail elements
        const dotWs = document.getElementById("popDotWs"), valWs = document.getElementById("popValWs");
        if (dotWs) dotWs.className = `popover-dot ${isWsOk ? 'dot-green' : (isWsWarn ? 'dot-yellow' : 'dot-red')}`;
        if (valWs) valWs.textContent = st.ws;

        const dotKis = document.getElementById("popDotKis"), valKis = document.getElementById("popValKis");
        if (dotKis) {
            dotKis.className = `popover-dot ${isKisLive ? 'dot-green' : (isKisSimulation ? 'dot-yellow' : (st.kis === 'unreachable' ? 'dot-red' : 'dot-yellow'))}`;
        }
        if (valKis) {
            if (st.kis === "live") valKis.textContent = "LIVE (실계좌)";
            else if (st.kis === "simulation") valKis.textContent = "SIMULATION (모의)";
            else if (st.kis === "unreachable") valKis.textContent = "상태 조회 실패";
            else valKis.textContent = "확인 중";
        }

        const dotG = document.getElementById("popDotGuardian"), valG = document.getElementById("popValGuardian");
        if (dotG) {
            dotG.className = `popover-dot ${isGuardianActive ? 'dot-green' : (isGuardianStandby ? 'dot-yellow' : (st.guardian === 'unknown' ? 'dot-yellow' : 'dot-red'))}`;
        }
        if (valG) {
            if (isGuardianActive) valG.textContent = "-7% EOD / -10% 비상 (ON)";
            else if (isGuardianStandby) valG.textContent = "DISABLED (OFF)";
            else if (st.guardian === "unknown") valG.textContent = "확인 중";
            else valG.textContent = "상태 조회 실패";
        }

        const dotA = document.getElementById("popDotAutopilot"), valA = document.getElementById("popValAutopilot");
        if (dotA) {
            dotA.className = `popover-dot ${isAutopilotActive ? 'dot-green' : (isAutopilotStandby ? 'dot-yellow' : (st.autopilot === 'unknown' ? 'dot-yellow' : 'dot-red'))}`;
        }
        if (valA) {
            if (isAutopilotActive) valA.textContent = "C-2 34/33/33 (ON)";
            else if (isAutopilotStandby) valA.textContent = "STANDBY (OFF)";
            else if (st.autopilot === "unknown") valA.textContent = "확인 중";
            else valA.textContent = "상태 조회 실패";
        }

        const dotLev = document.getElementById("popDotLeverage"), valLev = document.getElementById("popValLeverage");
        if (dotLev) dotLev.className = `popover-dot ${st.leverage && String(st.leverage).includes('1.5') ? 'dot-green' : 'dot-yellow'}`;
        if (valLev) valLev.textContent = st.leverage || "1.0x (Core)";
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
            autoBadge.title = `C-2 Autopilot · stop ${(sl * 100).toFixed(1)}% · 34/33/33 + QQQ Proxy`;
        }
    },

    renderSlotVisualizer(portfolioData, isBullRegime, topPick, slotSummary) {
        const grid = document.getElementById("slotVisualizerGrid");
        const summaryText = document.getElementById("slotSummaryText");
        const p = this.sanitizePortfolioNav(
            portfolioData || this.latestPortfolioData || (this.latestDashboardData && this.latestDashboardData.portfolio) || {}
        );
        const allHoldings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];
        const sats = allHoldings.filter((h) => !isCashProxyTicker(h.ticker));
        const maxSlots = (isBullRegime ? SLOT_WEIGHTS_BULL : SLOT_WEIGHTS_BEAR).length;

        if (summaryText) {
            const weightLabel = isBullRegime ? "34/33/33" : "25/25";
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
        if (grid) grid.innerHTML = "";
    },

    renderTopPicks(topPick, runnerUp) {
        const container = document.getElementById("topPicksContainer");
        if (!container) return;

        const renderCard = (pick, rankNum, badgeBg, badgeColor) => {
            if (!pick) {
                return `<div style="background:var(--surface-card); border:1px dashed var(--hairline-dark); border-radius:16px; padding:16px; text-align:center; color:var(--text-muted); font-size:11px; font-family:'JetBrains Mono';">RANK #${rankNum} NO CANDIDATE</div>`;
            }

            const ticker = this.escapeHtml(pick.ticker || '');
            const price = Number(pick.price || 0), score = Number(pick.conviction_score || 0), rs3m = Number(pick.rs_3m || pick.momentum_3m || 0), isBreakout = Boolean(pick.is_breakout);
            const sizing = pick.sizing || {}, shares = sizing.shares || 1, allocUsd = Number(sizing.allocated_usd || price * shares), allocKrw = sizing.allocated_krw || Math.round(allocUsd * 1380);

            return `<div class="top-pick-card" style="background:var(--surface-card); border:1px solid var(--hairline-dark); border-radius:12px; padding:10px 12px; cursor:pointer;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">` +
                `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;"><span style="font-size:10px; font-weight:800; background:${badgeBg}; color:${badgeColor}; padding:2px 8px; border-radius:9999px; font-family:'JetBrains Mono';">${rankNum === 1 ? 'RANK #1' : 'RANK #2'}</span><span style="font-size:11px; font-weight:800; color:var(--primary-bright); font-family:'JetBrains Mono';">${score} PT</span></div>` +
                `<div style="display:flex; justify-content:space-between; align-items:baseline;"><div><span style="font-size:16px; font-weight:800; color:#ffffff; font-family:'JetBrains Mono';">${ticker}</span></div><span style="font-size:14px; font-weight:800; color:#34d399; font-family:'JetBrains Mono';">$${price.toFixed(2)}</span></div>` +
                `<div style="font-size:10.5px; color:var(--text-secondary); margin-top:4px; font-family:'JetBrains Mono'; display:flex; justify-content:space-between;"><span>RS: <strong style="color:#fbbf24;">${rs3m >= 0 ? '+' : ''}${rs3m.toFixed(1)}%</strong> ${isBreakout ? '[돌파]' : ''}</span><span style="color:var(--primary-bright); font-weight:700;">${shares}주</span></div>` +
                `<div style="margin-top:8px;"><button class="btn-primary-pill" style="width:100%; padding:6px 8px; font-size:10.5px; font-weight:700; font-family:'JetBrains Mono';" onclick="event.stopPropagation(); window.TerminalUI.executeQuickBuy('${ticker}', ${price}, ${shares}, ${allocUsd}, ${allocKrw}, this)">BUY ${shares}주 ($${allocUsd.toFixed(0)})</button></div></div>`;
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
            const st = String(res?.status || "").toLowerCase();
            const msg = res?.message || `${ticker} 주문 처리되었습니다.`;
            if (st === "submitted") {
                this.toast(msg, "info");
            } else {
                this.toast(msg, "success");
                if (window.TerminalApp) {
                    window.TerminalApp.refreshData().catch(e => console.warn("[UI] refreshData after buy failed:", e));
                }
            }
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

        const p = this.sanitizePortfolioNav(portfolioData || {});
        this.latestPortfolioData = p;
        if (this.latestDashboardData) {
            this.latestDashboardData.portfolio = p;
        }

        const holdings = Array.isArray(p.holdings) ? p.holdings.filter((h) => isMarketTicker(h && h.ticker)) : [];
        const totalEquity = Number(p.total_equity_usd !== undefined ? p.total_equity_usd : (p.total_equity || 0));

        if (holdings.length === 0) {
            pBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-muted); padding:28px; font-family:'Inter', sans-serif; font-size:12px;">No active holdings. Select a top conviction pick or search above to enter a slot.</td></tr>`;
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
                const lotValue = Number(h.current_value || (safeCurrentPrice * safeQty));
                const weightPct = totalEquity > 0 ? ((lotValue / totalEquity) * 100) : 0;
                const isProxy = isCashProxyTicker(h.ticker);

                const stopLossP = Number(h.stop_loss_price || deriveStopPrice(safeBuyPrice));
                const trailingP = Number(h.trailing_floor || deriveTargetPrice(safeBuyPrice));
                const isTrailing = Boolean(h.is_trailing_active);
                const proxyMark = isProxy ? ' <span style="font-size:9px; color:#38bdf8; font-weight:700;">PROXY</span>' : '';

                return `<tr><td><strong class="ticker-pill ${isPos ? 'bull' : 'bear'}" style="cursor:pointer;" onclick="window.TerminalUI.selectStock('${safeTicker}', ${safeCurrentPrice})">${safeTicker}</strong>${proxyMark}</td>` +
                    `<td style="font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${safeBuyPrice.toFixed(2)}</td><td style="font-family:'JetBrains Mono'; font-weight:700; color:${pnlColor}; font-variant-numeric:tabular-nums;">$${safeCurrentPrice.toFixed(2)}</td>` +
                    `<td style="font-family:'JetBrains Mono'; font-weight:600; color:var(--primary-bright); font-variant-numeric:tabular-nums;">${safeQty}</td>` +
                    `<td class="holding-value" style="font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">$${lotValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>` +
                    `<td class="holding-weight" style="font-family:'JetBrains Mono'; font-weight:700; color:var(--primary-bright); font-variant-numeric:tabular-nums;">${weightPct.toFixed(1)}%</td>` +
                    `<td style="color:${pnlColor}; font-weight:700; font-family:'JetBrains Mono'; font-variant-numeric:tabular-nums;">${isPos ? '+' : ''}${pnlVal.toFixed(2)}% <span style="font-size:10px; opacity:0.85;">(${pnlAmtFormatted})</span></td>` +
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
                        const res = await ApiClient.sellStock(id, price);
                        const st = String(res?.status || "").toLowerCase();
                        const msg = res?.message || "매도 주문 처리되었습니다.";
                        if (st === "submitted") {
                            this.toast(msg, "info");
                            btn.disabled = false;
                            btn.textContent = "EXIT";
                        } else {
                            this.toast(msg, "success");
                            if (window.TerminalApp) {
                                window.TerminalApp.refreshData().catch(e => console.warn("[UI] refreshData after sell failed:", e));
                            }
                        }
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

        // Slot occupancy is a derived view of the live holdings table (SSOT)
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

    renderTradeLogs(logs, hasError = false) {
        const tBody = document.getElementById("tradeLogTableBody");
        const countEl = document.getElementById("tradeLogCount");
        if (!tBody) return;

        const container = document.getElementById("tradeLogContainer");
        if (container) {
            container.style.display = "";
            container.classList.add("matched-panel");
        }

        if (hasError || logs === undefined || logs === null) {
            if (countEl) countEl.textContent = "ERROR";
            tBody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:18px; color:#ef4444; font-family:'JetBrains Mono'; font-size:11px;">체결 내역을 불러오지 못했습니다.</td></tr>`;
            return;
        }

        const tradeList = Array.isArray(logs) ? logs.slice(0, this.MAX_LOGS) : [];
        if (countEl) countEl.textContent = `${tradeList.length} LOGS`;

        if (tradeList.length === 0) {
            tBody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:18px; color:var(--text-muted); font-family:'JetBrains Mono'; font-size:11px;">No execution logs recorded yet.</td></tr>`;
            return;
        }

        tBody.innerHTML = tradeList.map(item => {
            const side = String(item.side || 'BUY').toUpperCase();
            const isBuy = side === 'BUY';
            const isSell = side === 'SELL';
            const sideColor = isBuy ? '#34d399' : (isSell ? '#f87171' : '#60a5fa');
            const sideBg = isBuy
                ? 'rgba(52,211,153,0.15)'
                : (isSell ? 'rgba(248,113,113,0.15)' : 'rgba(96,165,250,0.15)');
            const ticker = this.escapeHtml(item.ticker || '');
            const price = Number(item.price || 0);
            const qtyRaw = item.quantity !== undefined && item.quantity !== null
                ? item.quantity
                : (item.qty !== undefined && item.qty !== null ? item.qty : 1);
            const qty = Number(qtyRaw);
            const totAmt = Number(item.total_amount || (price * qty));
            const ts = this.formatExecutionTs(item.timestamp);
            const msg = this.escapeHtml(item.message || item.order_type || 'MARKETABLE_ORDER');
            const statusRaw = String(item.status || 'FILLED').toUpperCase();
            const status = this.escapeHtml(statusRaw);
            const submitted = statusRaw.includes("SUBMIT") || statusRaw.includes("PENDING") || statusRaw.includes("AWAIT");
            const statusBg = submitted ? "rgba(251,191,36,0.14)" : (statusRaw.includes("FAIL") || statusRaw.includes("REJECT") ? "rgba(248,113,113,0.14)" : "rgba(52,211,153,0.12)");
            const statusFg = submitted ? "#fbbf24" : (statusRaw.includes("FAIL") || statusRaw.includes("REJECT") ? "#f87171" : "#34d399");

            // Parse or compute Trade PnL %
            let pnlDisplay = '<span style="color:var(--text-muted); font-family:\'JetBrains Mono\'; font-size:10px;">[ENTRY]</span>';
            if (isSell) {
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
            } else if (!isBuy) {
                pnlDisplay = '<span style="color:#60a5fa; font-family:\'JetBrains Mono\'; font-size:10px;">[SYNC]</span>';
            }

            return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04); transition:background 0.1s ease;">` +
                `<td style="padding:7px 12px; font-family:'JetBrains Mono'; color:var(--text-muted); font-size:10.5px; white-space:nowrap;">${ts}</td>` +
                `<td style="padding:7px 12px;"><span style="background:${sideBg}; color:${sideColor}; padding:2px 8px; border-radius:9999px; font-weight:800; font-family:'JetBrains Mono'; font-size:9.5px;">${side}</span></td>` +
                `<td style="padding:7px 12px;"><strong style="color:#ffffff; font-family:'JetBrains Mono'; cursor:pointer; font-size:11px;" onclick="window.TerminalUI.selectStock('${ticker}', ${price})">${ticker}</strong></td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:var(--primary-bright); font-weight:700; font-size:11px;">${qty} SH</td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; color:#ffffff; font-size:11px;">$${price.toFixed(2)}</td>` +
                `<td style="padding:7px 12px; text-align:right; font-family:'JetBrains Mono'; font-weight:700; color:#ffffff; font-size:11px;">$${totAmt.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>` +
                `<td style="padding:7px 12px; text-align:right;">${pnlDisplay}</td>` +
                `<td style="padding:7px 12px; font-family:'Inter', sans-serif; color:var(--text-secondary); font-size:10.5px; max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${msg}">${msg}</td>` +
                `<td style="padding:7px 12px; text-align:center;"><span style="background:${statusBg}; color:${statusFg}; padding:2px 6px; border-radius:4px; font-size:9px; font-weight:700; font-family:'JetBrains Mono';">[${status}]</span></td></tr>`;
        }).join('');

        while (tBody.childElementCount > this.MAX_LOGS) {
            tBody.removeChild(tBody.firstElementChild);
        }
    }
};
