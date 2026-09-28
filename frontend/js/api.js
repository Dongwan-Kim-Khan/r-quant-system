/**
 * R QUANT TERMINAL: REST API CLIENT MODULE
 * Handles all backend HTTP endpoints with error handling, base URL resolution, and AbortController support.
 */

export class ApiError extends Error {
    constructor(message, { status = null, kind = 'http', data = null } = {}) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.kind = kind;
        this.data = data;
    }
}

export function errorDetail(errData, fallback = '요청 처리에 실패했습니다.') {
    if (!errData) return fallback;
    if (typeof errData === 'string' && errData.trim()) {
        return errData.trim();
    }
    if (typeof errData.detail === 'string' && errData.detail.trim()) {
        return errData.detail.trim();
    }
    if (Array.isArray(errData.detail) && errData.detail.length > 0) {
        const msgs = errData.detail.map(d => {
            if (typeof d === 'string') return d;
            if (d && typeof d.msg === 'string') return d.msg;
            return JSON.stringify(d);
        }).filter(Boolean);
        if (msgs.length > 0) return msgs.join('; ');
    }
    if (typeof errData.message === 'string' && errData.message.trim()) {
        return errData.message.trim();
    }
    return fallback;
}

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
        let res;
        try {
            res = await fetch(`${base}/api/dashboard`);
        } catch (err) {
            console.warn('[API] /api/dashboard network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 대시보드를 불러오지 못했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '대시보드 피드를 불러오지 못했습니다.');
            console.warn(`[API] /api/dashboard HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
    },

    async getExecutionLogs(limit = 300) {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(
                `${base}/api/execution-logs?limit=${encodeURIComponent(limit)}`,
                { cache: 'no-store' }
            );
        } catch (err) {
            console.warn('[API] /api/execution-logs network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 체결 내역을 불러오지 못했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '체결 내역을 불러오지 못했습니다.');
            console.warn(`[API] /api/execution-logs HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
    },

    /**
     * Fetch active portfolio holdings and financial equity summary
     */
    async getPortfolioData(reconcile = false) {
        const base = getBaseUrl();
        const suffix = reconcile ? '?reconcile=true' : '';
        let res;
        try {
            res = await fetch(`${base}/api/portfolio${suffix}`, { cache: 'no-store' });
        } catch (err) {
            console.warn('[API] /api/portfolio network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 포트폴리오를 불러오지 못했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '포트폴리오 조회 실패');
            console.warn(`[API] /api/portfolio HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
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
                    this._chartAbortController.abort('superseded');
                }
                return cached.data;
            }
        }

        if (this._chartAbortController && this._inflightChartTicker !== cleanTicker) {
            this._chartAbortController.abort('superseded');
        }
        this._inflightChartTicker = cleanTicker;
        const controller = new AbortController();
        this._chartAbortController = controller;
        const signal = controller.signal;
        const base = getBaseUrl();
        let timedOut = false;
        const timeoutId = setTimeout(() => {
            timedOut = true;
            controller.abort('timeout');
        }, 8000);

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

            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '차트 데이터를 불러오지 못했습니다.');
            console.warn(`[API] /api/chart/${cleanTicker} HTTP ${res.status}:`, detail);
            if (res.status === 404) {
                return { notFound: true, status: 404, detail };
            }
            return {
                error: true,
                status: res.status,
                kind: 'http',
                detail,
                data: errData
            };
        } catch (err) {
            if (err.name === 'AbortError' || signal.aborted) {
                const reason = (signal.reason === 'timeout' || timedOut) ? 'timeout' : 'superseded';
                return { aborted: true, reason };
            }
            console.warn(`[API] /api/chart/${cleanTicker} network error:`, err);
            return {
                error: true,
                status: null,
                kind: 'network',
                detail: err.message || '네트워크 오류로 차트를 불러오지 못했습니다.'
            };
        } finally {
            clearTimeout(timeoutId);
        }
    },

    /**
     * Search universe stocks by ticker or Korean name
     */
    async searchStocks(query) {
        if (!query || !query.trim()) return [];
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/search?q=${encodeURIComponent(query.trim())}`);
        } catch (err) {
            console.warn('[API] Stock search network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 종목 검색에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '종목 검색에 실패했습니다.');
            console.warn(`[API] Stock search HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        const data = await res.json();
        return data.results || data.suggestions || [];
    },

    /**
     * Execute quick portfolio buy
     */
    async buyHolding(order) {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/portfolio/buy`, {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(order)
            });
        } catch (err) {
            console.warn('[API] Buy order network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 매수 주문에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '매수 주문 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Execute portfolio sell/exit
     */
    async sellHolding(id, sellPrice, reason = "MANUAL_SELL") {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/portfolio/sell/${id}`, {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ sell_price: Number(sellPrice), reason: reason })
            });
        } catch (err) {
            console.warn('[API] Sell order network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 매도 주문에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '매도 주문 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Reset all simulated portfolio holdings
     */
    async resetPortfolio() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/portfolio/reset`, { method: 'POST', headers: authHeaders() });
        } catch (err) {
            console.warn('[API] Reset portfolio network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 포트폴리오 초기화에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '포트폴리오 초기화 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Trigger on-demand background 3-Gate quant scan
     */
    async triggerScanNow() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/scan_now`, { method: 'POST', headers: authHeaders() });
        } catch (err) {
            console.warn('[API] Scan trigger network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 스캔 요청에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '스캔 요청 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Fetch KIS Broker live status and connection info
     */
    async getBrokerStatus() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/broker/status`);
        } catch (err) {
            console.warn('[API] Failed to get broker status (network):', err);
            throw new ApiError(err.message || '네트워크 오류로 브로커 상태 조회 실패', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '브로커 상태 조회 실패');
            console.warn(`[API] /api/broker/status HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
    },

    /**
     * Trigger 1-time daily reconciliation audit (check_sync)
     */
    async triggerReconciliation() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/broker/reconcile`, { method: 'POST', headers: authHeaders() });
        } catch (err) {
            console.warn('[API] Reconciliation network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 대조 요청에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '대조 요청 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Fetch Autonomous Portfolio Guardian status and rules
     */
    async getGuardianStatus() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/guardian/status`);
        } catch (err) {
            console.warn('[API] Failed to get guardian status (network):', err);
            throw new ApiError(err.message || '네트워크 오류로 가디언 상태 조회 실패', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '가디언 상태 조회 실패');
            console.warn(`[API] /api/guardian/status HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
    },

    /**
     * Toggle Guardian auto-execution (On / Off)
     */
    async toggleGuardian(enabled) {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/guardian/toggle`, {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ enabled })
            });
        } catch (err) {
            console.warn('[API] Toggle guardian network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 가디언 토글에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '가디언 설정 변경 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
        return await res.json();
    },

    /**
     * Fetch Full-Auto Pilot Trader status
     */
    async getAutoPilotStatus() {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/autopilot/status`);
        } catch (err) {
            console.warn('[API] Failed to get autopilot status (network):', err);
            throw new ApiError(err.message || '네트워크 오류로 오토파일럿 상태 조회 실패', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '오토파일럿 상태 조회 실패');
            console.warn(`[API] /api/autopilot/status HTTP ${res.status}:`, detail);
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }

        return await res.json();
    },

    /**
     * Toggle Full-Auto Pilot buying (On / Off)
     */
    async toggleAutoPilot(enabled) {
        const base = getBaseUrl();
        let res;
        try {
            res = await fetch(`${base}/api/autopilot/toggle`, {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ enabled })
            });
        } catch (err) {
            console.warn('[API] Toggle autopilot network error:', err);
            throw new ApiError(err.message || '네트워크 오류로 오토파일럿 토글에 실패했습니다.', { kind: 'network' });
        }

        if (!res.ok) {
            let errData = null;
            try { errData = await res.json(); } catch (_) {}
            const detail = errorDetail(errData, '오토파일럿 설정 변경 실패');
            throw new ApiError(detail, { status: res.status, kind: 'http', data: errData });
        }
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
