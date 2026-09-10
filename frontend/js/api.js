/**
 * R QUANT TERMINAL: REST API CLIENT MODULE
 * Handles all backend HTTP endpoints with error handling, base URL resolution, and AbortController support.
 */

function getBaseUrl() {
    if (typeof window !== 'undefined' && (window.location.protocol === 'file:' || !window.location.host)) {
        return 'http://localhost:8000';
    }
    return '';
}

function authHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    let key = '';
    try {
        if (typeof window !== 'undefined') {
            key = String(window.AL_SANGMOO_API_KEY || window.localStorage.getItem('AL_SANGMOO_API_KEY') || '').trim();
        }
    } catch (e) { /* ignore */ }
    if (key) headers['X-API-Key'] = key;
    return headers;
}

export const ApiClient = {
    // Current in-flight chart abort controller
    _chartAbortController: null,
    _inflightChartTicker: null,

    /**
     * Fetch complete executive dashboard feed
     */
    async getDashboardData() {
        const base = getBaseUrl();
        try {
            const res = await fetch(`${base}/api/dashboard`);
            if (res.ok) {
                return await res.json();
            }
        } catch (err) {
            console.warn('[API] /api/dashboard failed, attempting local cache fallback...', err);
        }

        // Fallback to static JSON if offline or local file
        try {
            const resLocal = await fetch('dashboard_data.json');
            if (resLocal.ok) {
                return await resLocal.json();
            }
        } catch (err) {
            console.error('[API] Local dashboard_data.json fallback also failed:', err);
        }
        return null;
    },

    /**
     * Fetch active portfolio holdings and financial equity summary
     */
    async getPortfolioData() {
        const base = getBaseUrl();
        try {
            const res = await fetch(`${base}/api/portfolio`);
            if (res.ok) {
                return await res.json();
            }
        } catch (err) {
            console.warn('[API] /api/portfolio fetch failed:', err);
        }
        return null;
    },

    // In-memory client cache for instant chart switching (0ms response)
    _chartMemoryCache: new Map(),

    /**
     * Fetch modular chart data for a given ticker
     */
    async getChartData(ticker) {
        if (!ticker) return null;
        const cleanTicker = ticker.trim().toUpperCase();

        // 1. Fast in-memory client cache (< 0.01ms)
        if (this._chartMemoryCache.has(cleanTicker)) {
            const cached = this._chartMemoryCache.get(cleanTicker);
            const cachedTk = String(cached?.data?.ticker || "").toUpperCase();
            if (Date.now() - cached.timestamp < 3000 && (!cachedTk || cachedTk === cleanTicker)) {
                if (this._chartAbortController && this._inflightChartTicker !== cleanTicker) {
                    this._chartAbortController.abort();
                }
                return cached.data;
            }
        }

        if (this._chartAbortController && this._inflightChartTicker !== cleanTicker) {
            this._chartAbortController.abort();
        }
        this._inflightChartTicker = cleanTicker;
        this._chartAbortController = new AbortController();
        const signal = this._chartAbortController.signal;
        const base = getBaseUrl();
        const timeoutId = setTimeout(() => this._chartAbortController.abort(), 8000);

        try {
            const res = await fetch(`${base}/api/chart/${encodeURIComponent(cleanTicker)}?_=${Date.now()}`, { signal, cache: "no-store" });
            if (res.ok) {
                const data = await res.json();
                const payloadTk = String(data?.ticker || "").toUpperCase();
                if (payloadTk && payloadTk !== cleanTicker) {
                    console.warn(`[API] chart payload ticker ${payloadTk} != ${cleanTicker}`);
                    return null;
                }
                this._chartMemoryCache.set(cleanTicker, { timestamp: Date.now(), data });
                return data;
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                return { aborted: true };
            }
            console.warn(`[API] /api/chart/${cleanTicker} fetch failed:`, err);
        } finally {
            clearTimeout(timeoutId);
        }

        return null;
    },

    /**
     * Search universe stocks by ticker or Korean name
     */
    async searchStocks(query) {
        if (!query || !query.trim()) return [];
        const base = getBaseUrl();
        try {
            const res = await fetch(`${base}/api/search?q=${encodeURIComponent(query.trim())}`);
            if (res.ok) {
                const data = await res.json();
                return data.results || data.suggestions || [];
            }
        } catch (err) {
            console.warn('[API] Stock search failed:', err);
        }
        return [];
    },

    /**
     * Execute quick portfolio buy
     */
    async buyHolding(order) {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/portfolio/buy`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(order)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Buy order failed' }));
            throw new Error(err.detail || 'Buy order failed');
        }
        return await res.json();
    },

    /**
     * Execute portfolio sell/exit
     */
    async sellHolding(id, sellPrice, reason = "MANUAL_SELL") {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/portfolio/sell/${id}`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ sell_price: Number(sellPrice), reason: reason })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Sell order failed' }));
            throw new Error(err.detail || 'Sell order failed');
        }
        return await res.json();
    },

    /**
     * Reset all simulated portfolio holdings
     */
    async resetPortfolio() {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/portfolio/reset`, { method: 'POST', headers: authHeaders() });
        if (!res.ok) {
            throw new Error('Portfolio reset failed');
        }
        return await res.json();
    },

    /**
     * Trigger on-demand background 3-Gate quant scan
     */
    async triggerScanNow() {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/scan_now`, { method: 'POST', headers: authHeaders() });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Scan trigger failed' }));
            throw new Error(err.detail || 'Scan trigger failed');
        }
        return await res.json();
    },

    /**
     * Fetch KIS Broker live status and connection info
     */
    async getBrokerStatus() {
        try {
            const base = getBaseUrl();
            const res = await fetch(`${base}/api/broker/status`);
            if (res.ok) return await res.json();
        } catch (e) {
            console.warn('[API] Failed to get broker status:', e);
        }
        return null;
    },

    /**
     * Trigger 1-time daily reconciliation audit (check_sync)
     */
    async triggerReconciliation() {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/broker/reconcile`, { method: 'POST', headers: authHeaders() });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Reconciliation failed' }));
            throw new Error(err.detail || 'Reconciliation failed');
        }
        return await res.json();
    },

    /**
     * Fetch Autonomous Portfolio Guardian status and rules
     */
    async getGuardianStatus() {
        try {
            const base = getBaseUrl();
            const res = await fetch(`${base}/api/guardian/status`);
            if (res.ok) return await res.json();
        } catch (e) {
            console.warn('[API] Failed to get guardian status:', e);
        }
        return null;
    },

    /**
     * Toggle Guardian auto-execution (On / Off)
     */
    async toggleGuardian(enabled) {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/guardian/toggle`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ enabled })
        });
        if (!res.ok) throw new Error('Failed to toggle guardian');
        return await res.json();
    },

    /**
     * Fetch Full-Auto Pilot Trader status
     */
    async getAutoPilotStatus() {
        try {
            const base = getBaseUrl();
            const res = await fetch(`${base}/api/autopilot/status`);
            if (res.ok) return await res.json();
        } catch (e) {
            console.warn('[API] Failed to get autopilot status:', e);
        }
        return null;
    },

    /**
     * Toggle Full-Auto Pilot buying (On / Off)
     */
    async toggleAutoPilot(enabled) {
        const base = getBaseUrl();
        const res = await fetch(`${base}/api/autopilot/toggle`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ enabled })
        });
        if (!res.ok) throw new Error('Failed to toggle autopilot');
        return await res.json();
    },

    // Convenience Aliases for UI Dispatcher
    async buyStock(ticker, price, qty) {
        return await this.buyHolding({ ticker, buy_price: Number(price), quantity: Number(qty) });
    },

    async sellStock(id, price, reason = "MANUAL_SELL") {
        return await this.sellHolding(id, price, reason);
    },

    async triggerScan() {
        return await this.triggerScanNow();
    },

    async syncBroker() {
        return await this.triggerReconciliation();
    }
};


