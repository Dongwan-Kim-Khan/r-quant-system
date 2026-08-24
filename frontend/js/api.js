/**
 * R QUANT TERMINAL: REST API CLIENT MODULE
 * Handles all backend HTTP endpoints with error handling and AbortController support.
 */

export const ApiClient = {
    // Current in-flight chart abort controller
    _chartAbortController: null,

    /**
     * Fetch complete executive dashboard feed
     */
    async getDashboardData() {
        try {
            const res = await fetch('/api/dashboard');
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
     * Fetch modular chart data for a given ticker
     */
    async getChartData(ticker) {
        if (this._chartAbortController) {
            this._chartAbortController.abort();
        }
        this._chartAbortController = new AbortController();
        const signal = this._chartAbortController.signal;

        try {
            const res = await fetch(`/api/chart/${encodeURIComponent(ticker)}`, { signal });
            if (res.ok) {
                return await res.json();
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                return { aborted: true };
            }
            console.warn(`[API] /api/chart/${ticker} fetch failed:`, err);
        }

        return null;
    },

    /**
     * Search universe stocks by ticker or Korean name
     */
    async searchStocks(query) {
        if (!query || !query.trim()) return [];
        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query.trim())}`);
            if (res.ok) {
                const data = await res.json();
                return data.results || [];
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
        const res = await fetch('/api/portfolio/buy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
    async sellHolding(id, currentPrice) {
        const res = await fetch(`/api/portfolio/sell/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_price: currentPrice })
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
        const res = await fetch('/api/portfolio/reset', { method: 'POST' });
        if (!res.ok) {
            throw new Error('Portfolio reset failed');
        }
        return await res.json();
    },

    /**
     * Trigger on-demand background 3-Gate quant scan
     */
    async triggerScanNow() {
        const res = await fetch('/api/scan_now', { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Scan trigger failed' }));
            throw new Error(err.detail || 'Scan trigger failed');
        }
        return await res.json();
    }
};
