// R QUANT TERMINAL: WebSocket hub, heartbeat, and TerminalApp controller.
import { ApiClient } from './api.js?v=4.4.8';
import { UI } from './ui.js?v=4.4.8';
import { ChartEngine } from './chart.js?v=4.4.8';

export const ConnectionState = { DISCONNECTED: "DISCONNECTED", CONNECTING: "CONNECTING", CONNECTED: "CONNECTED", REAUTHENTICATING: "REAUTHENTICATING", ERROR: "ERROR" };

export const WebSocketClient = {
    socket: null,
    reconnectAttempts: 0,
    maxReconnectDelay: 10000,
    reconnectTimeoutId: null,
    pollingInterval: null,
    currentPollingMs: 0,
    executionLogInterval: null,
    heartbeatInterval: null,
    lastPongReceived: Date.now(),
    isSocketConnected: false,
    connectionState: "DISCONNECTED",
    _scanTimeoutId: null,
    _consecutivePollFailures: 0,
    _isOriginBlocked: false,
    _isWsLimit: false,

    init() {
        this._setupLifecycleListeners();
        this._startExecutionLogPolling();
        this.connect();
    },

    _setupLifecycleListeners() {
        const wake = () => this.checkAndReconnect();
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") wake();
        });
        window.addEventListener("focus", wake);
        window.addEventListener("pageshow", wake);
        window.addEventListener("online", wake);
        try {
            const worker = new Worker(URL.createObjectURL(new Blob(
                ["setInterval(function(){postMessage('tick');},3000);"],
                { type: "application/javascript" }
            )));
            worker.onmessage = () => {
                if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                    try { this.socket.send("ping"); } catch (e) {}
                } else if (!this.socket || this.socket.readyState === WebSocket.CLOSED) {
                    this.checkAndReconnect();
                }
            };
        } catch (e) {}
        setInterval(() => { if (document.visibilityState === "visible") wake(); }, 10000);
    },

    checkAndReconnect(resetAttempts = true) {
        if (this._isOriginBlocked) {
            this._hydrateFromHttp();
            this._startHttpPolling();
            return;
        }
        const wsOpen = this.socket && this.socket.readyState === WebSocket.OPEN;
        const isStale = (Date.now() - this.lastPongReceived) > 30000;
        if (wsOpen && isStale) {
            console.warn("[WS] Socket is OPEN but pong > 30s. Closing before reconnect.");
            try { this.socket.close(); } catch (e) {}
            this.socket = null;
        } else if (wsOpen && !isStale && this.connectionState === ConnectionState.CONNECTED) {
            this._stopHttpPolling();
            return;
        }
        if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
            return;
        }
        if (!this._isWsLimit) {
            this._setConnectionState(ConnectionState.REAUTHENTICATING);
        }
        this._hydrateFromHttp();
        this._startHttpPolling();
        if (resetAttempts && !this._isWsLimit) {
            this.reconnectAttempts = 0;
        }
        this.connect();
    },

    connect() {
        if (this._isOriginBlocked) {
            return;
        }
        if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
            return; // Socket is already open or connecting
        }

        // Clean up previous socket if any
        if (this.socket) {
            try {
                this.socket.onclose = null;
                this.socket.onerror = null;
                this.socket.onmessage = null;
                this.socket.onopen = null;
                this.socket.close();
            } catch (e) {}
            this.socket = null;
        }

        let host = window.location.host;
        if (!host || window.location.protocol === 'file:') {
            host = 'localhost:8000';
        }

        const protocol = (window.location.protocol === 'https:' || window.location.protocol === 'wss:') ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${host}/ws`;

        if (window.location.protocol === 'file:') {
            this._setConnectionState(ConnectionState.DISCONNECTED);
            this._setStatus("LOCAL FILE", "#3b82f6");
            return;
        }

        this._setConnectionState(ConnectionState.CONNECTING);

        try {
            this.socket = new WebSocket(wsUrl);
            this.lastPongReceived = Date.now();

            this.socket.onopen = () => {
                console.log("[WS] Connected to live broadcast hub:", wsUrl);
                this.isSocketConnected = true;
                this.reconnectAttempts = 0;
                this._isWsLimit = false;
                this._isOriginBlocked = false;
                if (this.reconnectTimeoutId) {
                    clearTimeout(this.reconnectTimeoutId);
                    this.reconnectTimeoutId = null;
                }
                this.lastPongReceived = Date.now();
                this._setConnectionState(ConnectionState.CONNECTED);
                this._startHeartbeat();
                this._stopHttpPolling();
            };

            this.socket.onmessage = (event) => {
                this.lastPongReceived = Date.now();
                if (event.data === "pong") {
                    return; // Heartbeat ACK
                }
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === "pong" || msg.event === "pong") {
                        return;
                    }
                    this.handleMessage(msg);
                } catch (err) {
                    console.warn("[WS] Error parsing message:", err);
                }
            };

            this.socket.onclose = (e) => {
                console.warn("[WS] Connection closed. Code:", e.code, "Reason:", e.reason);
                this.isSocketConnected = false;
                this._stopHeartbeat();
                const reasonStr = String(e.reason || "");
                if (e.code === 1008 && reasonStr.toLowerCase().includes("limit")) {
                    this._isWsLimit = true;
                    this._setStatus("WS LIMIT", "#ef4444");
                    this._setConnectionState(ConnectionState.ERROR, false);
                    this._startHttpPolling();
                    this._scheduleReconnect();
                    return;
                }
                if (e.code === 1008 && (reasonStr.includes("Origin") || reasonStr.includes("Forbidden Origin"))) {
                    this._isOriginBlocked = true;
                    this._setStatus("ORIGIN BLOCKED", "#ef4444");
                    this._setConnectionState(ConnectionState.ERROR, false);
                    this._startHttpPolling();
                    return; // Do not reconnect on forbidden origin
                }
                this._setConnectionState(ConnectionState.REAUTHENTICATING);
                this._startHttpPolling();
                this._scheduleReconnect();
            };

            this.socket.onerror = (err) => {
                console.warn("[WS] Socket error:", err);
                this._setConnectionState(ConnectionState.ERROR);
            };

        } catch (err) {
            console.error("[WS] Initialization exception:", err);
            this._stopHeartbeat();
            this._setConnectionState(ConnectionState.ERROR);
            this._startHttpPolling();
            this._scheduleReconnect();
        }
    },

    _startHeartbeat() {
        this._stopHeartbeat();
        // 10-second active ping + 45-second zombie connection watchdog
        this.heartbeatInterval = setInterval(() => {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                try {
                    this.socket.send("ping");
                } catch (e) {
                    console.warn("[WS] Heartbeat send failed:", e);
                }

                // Check if pong has been missed for more than 45 seconds
                if (Date.now() - this.lastPongReceived > 45000) {
                    console.warn("[WS Watchdog] Heartbeat missed >45s (zombie socket). Reconnecting...");
                    try {
                        this.socket.close();
                    } catch (e) {}
                }
            }
        }, 10000);
    },

    _stopHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
    },

    handleMessage(msg) {
        if (!msg) return;
        const eventType = msg.type || msg.event;
        if (!eventType) return;

        if (eventType === "live_feed_update" && msg.data) {
            const livePort = UI.preferLivePortfolio(msg.data.portfolio);
            if (livePort) msg.data.portfolio = livePort;
            UI.renderDashboard(msg.data);
        } else if (eventType === "POSITION_DELTA" && msg.data) {
            this._applyPositionDelta(msg.data);
        } else if (eventType === "SYSTEM_STATUS" && msg.data && msg.data.state) {
            const next = String(msg.data.state).toUpperCase();
            if (Object.values(ConnectionState).includes(next)) {
                this._setConnectionState(next);
            }
        } else if (eventType === "portfolio_update" && msg.data) {
            UI.renderPortfolio(msg.data);
            UI.renderKPIs(null, msg.data, null);
            if (msg.data.holdings && Array.isArray(msg.data.holdings) && ChartEngine.currentTicker) {
                const match = msg.data.holdings.find(h => h.ticker && h.ticker.toUpperCase() === ChartEngine.currentTicker);
                if (match && match.current_price) {
                    ChartEngine.updateLiveTick(match.ticker, match.current_price);
                }
            }
        } else if (eventType === "price_update" && msg.data) {
            if (msg.data.ticker && msg.data.price) {
                ChartEngine.updateLiveTick(msg.data.ticker, msg.data.price);
            }
        } else if (eventType === "connected" && msg.data && msg.data.portfolio) {
            UI.renderPortfolio(msg.data.portfolio);
            UI.renderKPIs(null, msg.data.portfolio, null);
        } else if (eventType === "autopilot_buy_alert" && msg.data) {
            const tr = { ...msg.data, side: msg.data.side || "BUY", filled: UI.isConfirmedFill({ ...msg.data, side: "BUY" }) };
            UI.toast(`[AUTOPILOT] ${tr.ticker} ${tr.shares} SH @ $${Number(tr.buy_price).toFixed(2)}`, "success");
            try { UI.addMediaWallTradeAlert(tr); } catch (e) {}
        } else if (eventType === "guardian_alert" && msg.data) {
            const act = msg.data;
            if (!UI.shouldShowTradeAlert(act)) return;
            const filled = UI.isConfirmedFill(act);
            const px = Number(act.sell_price || act.buy_price || act.price || 0);
            const side = String(act.side || act.action || "EXIT").toUpperCase();
            UI.toast(
                filled
                    ? `[GUARDIAN] ${act.ticker} ${side} FILLED @ $${px.toFixed(2)} — ${act.reason || "exit"}`
                    : `[GUARDIAN] ${act.ticker} ${side} 접수(미체결) @ $${px.toFixed(2)} — ${act.reason || "submitted"}`,
                filled ? "warn" : "info"
            );
            try { UI.addMediaWallTradeAlert(act); } catch (e) {}
        } else if (eventType === "scan_status") {
            const btnScan = document.getElementById("btnSyncAll");
            const dataStatus = (msg.data && msg.data.status) ? msg.data.status : msg.status;
            const scanMsg = (msg.data && msg.data.message) || msg.message || "";
            if (dataStatus === "completed") {
                if (this._scanTimeoutId) {
                    clearTimeout(this._scanTimeoutId);
                    this._scanTimeoutId = null;
                }
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "SYNC / REFRESH";
                }
                UI.toast(scanMsg || "스캔 완료", "success");
            } else if (dataStatus === "error") {
                if (this._scanTimeoutId) {
                    clearTimeout(this._scanTimeoutId);
                    this._scanTimeoutId = null;
                }
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "SYNC / REFRESH";
                }
                UI.toast(scanMsg || "스캔 실패", "error");
            } else if (dataStatus === "started") {
                if (btnScan) {
                    btnScan.disabled = true;
                    btnScan.textContent = "SYNCING...";
                }
            }
        }
    },

    _scheduleReconnect() {
        if (this.reconnectTimeoutId) {
            clearTimeout(this.reconnectTimeoutId);
            this.reconnectTimeoutId = null;
        }
        this.reconnectAttempts++;
        const exponent = Math.min(this.reconnectAttempts, 4);
        const baseDelay = Math.min(this.maxReconnectDelay, 1000 * Math.pow(2, exponent));
        const delay = baseDelay + Math.random() * 1000;

        this.reconnectTimeoutId = setTimeout(() => {
            this.reconnectTimeoutId = null;
            if (!this.isSocketConnected && (!this.socket || this.socket.readyState === WebSocket.CLOSED)) {
                this.connect();
            }
        }, delay);
    },

    _startHttpPolling() {
        const desiredMs = (this.connectionState === ConnectionState.CONNECTED) ? 15000 : 5000;
        if (this.currentPollingMs === desiredMs && this.pollingInterval) return;
        this.currentPollingMs = desiredMs;
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
        this.pollingInterval = setInterval(async () => {
            await this._pollHttpFeed();
        }, desiredMs);
    },

    _stopHttpPolling() {
        if (this.connectionState === ConnectionState.CONNECTED) {
            // Keep 15s slow polling even when connected to recover missed broadcasts
            this._startHttpPolling();
            return;
        }
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
            this.currentPollingMs = 0;
        }
    },

    async _pollHttpFeed() {
        try {
            const fresh = await ApiClient.getDashboardData();
            if (fresh) {
                if (fresh.last_updated !== UI.latestDashboardData?.last_updated) {
                    UI.renderDashboard(fresh);
                }
            }
            const freshPort = await ApiClient.getPortfolioData(false);
            if (freshPort) {
                UI.renderPortfolio(freshPort);
            }
            if (this._consecutivePollFailures >= 2) {
                this._restoreConnectionStatus();
            }
            this._consecutivePollFailures = 0;
        } catch (err) {
            this._consecutivePollFailures = (this._consecutivePollFailures || 0) + 1;
            console.warn(`[HTTP Poll] Feed poll failed (failures=${this._consecutivePollFailures}):`, err);
            if (this._consecutivePollFailures >= 2) {
                this._setStatus("FEED STALE", "#f59e0b");
            }
        }
    },

    _restoreConnectionStatus() {
        if (this._isOriginBlocked) {
            this._setStatus("ORIGIN BLOCKED", "#ef4444");
            return;
        }
        if (this._isWsLimit) {
            this._setStatus("WS LIMIT", "#ef4444");
            return;
        }
        const labels = {
            DISCONNECTED: ["OFFLINE", "#ef4444"],
            CONNECTING: ["CONNECTING", "#f59e0b"],
            CONNECTED: ["LIVE HUB", "#10b981"],
            REAUTHENTICATING: ["REAUTH", "#f59e0b"],
            ERROR: ["ERROR", "#ef4444"],
        };
        const pair = labels[this.connectionState];
        if (pair) this._setStatus(pair[0], pair[1]);
    },

    _startExecutionLogPolling() {
        if (this.executionLogInterval) return;
        const refresh = async () => {
            if (document.visibilityState !== "visible") return;
            try {
                const payload = await ApiClient.getExecutionLogs(300);
                if (payload && Array.isArray(payload.execution_logs)) {
                    UI.renderTradeLogs(payload.execution_logs);
                    const dash = UI.latestDashboardData || {};
                    const isBull = (dash.slot_allocation_summary && dash.slot_allocation_summary.is_bull_regime !== undefined)
                        ? dash.slot_allocation_summary.is_bull_regime : true;
                    try { UI.renderMediaWall(dash.macro, payload.execution_logs, isBull, dash.slot_allocation_summary); } catch (e) {}
                }
            } catch (e) {
                console.warn("[HTTP Poll] Execution logs poll error:", e);
            }
        };
        refresh();
        this.executionLogInterval = setInterval(refresh, 3000);
    },

    _hydrateFromHttp() {
        ApiClient.getDashboardData().then(fresh => {
            if (fresh) UI.renderDashboard(fresh);
            return ApiClient.getPortfolioData(false);
        }).then(freshPort => {
            if (freshPort) UI.renderPortfolio(freshPort);
            if (this._consecutivePollFailures >= 2) this._restoreConnectionStatus();
            this._consecutivePollFailures = 0;
        }).catch(err => {
            this._consecutivePollFailures = (this._consecutivePollFailures || 0) + 1;
            console.warn("[HTTP Hydrate] Hydration failed:", err);
            if (this._consecutivePollFailures >= 2) {
                this._setStatus("FEED STALE", "#f59e0b");
            }
        });
    },

    _applyPositionDelta(delta) {
        if (!delta) return;
        const dash = UI.latestDashboardData;
        if (!dash) return;
        if (!dash.portfolio) dash.portfolio = { holdings: [] };
        if (!Array.isArray(dash.portfolio.holdings)) dash.portfolio.holdings = [];
        const tk = String(delta.ticker || (delta.holding && delta.holding.ticker) || "").toUpperCase();
        if (!tk) return;
        const action = String(delta.action || "").toUpperCase();
        if (action === "EXIT" || delta.holding === null) {
            dash.portfolio.holdings = dash.portfolio.holdings.filter(
                (h) => String(h.ticker || "").toUpperCase() !== tk
            );
        } else if (delta.holding) {
            const idx = dash.portfolio.holdings.findIndex(
                (h) => String(h.ticker || "").toUpperCase() === tk
            );
            if (idx >= 0) {
                dash.portfolio.holdings[idx] = { ...dash.portfolio.holdings[idx], ...delta.holding };
            } else {
                dash.portfolio.holdings.push(delta.holding);
            }
        }
        UI.renderPortfolio(dash.portfolio);
        UI.renderEngineOverlay(dash.slot_allocation_summary, dash.macro, dash.portfolio, dash.risk_constitution);
    },

    _setConnectionState(state, updateLabel = true) {
        this.connectionState = state;
        const labels = {
            DISCONNECTED: ["OFFLINE", "#ef4444"],
            CONNECTING: ["CONNECTING", "#f59e0b"],
            CONNECTED: ["LIVE HUB", "#10b981"],
            REAUTHENTICATING: ["REAUTH", "#f59e0b"],
            ERROR: ["ERROR", "#ef4444"],
        };
        const pair = labels[state];
        if (pair && updateLabel) this._setStatus(pair[0], pair[1]);
        try { UI.updateSystemHealth({ ws: state }); } catch (e) {}
    },

    _setStatus(text, color) {
        const sText = document.getElementById("serverStatusText");
        const sDot = document.getElementById("serverDot");
        if (sText) sText.textContent = text;
        if (sDot) {
            sDot.style.background = color;
            sDot.style.boxShadow = `0 0 8px ${color}`;
        }
    }
};

export const TerminalApp = {
    async init() {
        console.log("[TerminalApp] Initializing R Quant Terminal v2 Trading Cockpit...");
        ChartEngine.init();
        this._setupEventListeners();
        
        // 1. Connect WebSocket Hub immediately in parallel (Zero waterfall delay)
        WebSocketClient.init();

        // 2. Initial Broker Status Check in parallel (non-blocking)
        ApiClient.getBrokerStatus().then(brokerSt => {
            if (!brokerSt || typeof brokerSt !== 'object') {
                throw new Error("Invalid broker status response");
            }
            const isLive = Boolean(brokerSt.is_configured);
            const kisText = document.getElementById("kisStatusText"), kisDot = document.getElementById("kisStatusDot"), kisBox = document.getElementById("kisBrokerStatusBox");
            if (kisText) kisText.textContent = isLive ? "KIS API: CONNECTED" : "KIS API: SIMULATION";
            if (kisDot) { kisDot.style.background = isLive ? "#34d399" : "#f59e0b"; kisDot.style.boxShadow = `0 0 6px ${isLive ? '#34d399' : '#f59e0b'}`; }
            if (kisBox) kisBox.style.background = isLive ? "#064e3b" : "#1e293b";
            try { UI.updateSystemHealth({ kis: isLive ? "live" : "simulation" }); } catch (e) {}
        }).catch(err => {
            console.warn("[TerminalApp] Broker status check failed:", err);
            const kisText = document.getElementById("kisStatusText"), kisDot = document.getElementById("kisStatusDot"), kisBox = document.getElementById("kisBrokerStatusBox");
            if (kisText) kisText.textContent = "KIS API: UNREACHABLE";
            if (kisDot) { kisDot.style.background = "#ef4444"; kisDot.style.boxShadow = "0 0 6px #ef4444"; }
            if (kisBox) kisBox.style.background = "#450a0a";
            try { UI.updateSystemHealth({ kis: "unreachable" }); } catch (e) {}
        });

        // 3. Fetch dashboard and broker-reconciled portfolio concurrently.
        try {
            const portfolioPromise = ApiClient.getPortfolioData(true);
            const data = await ApiClient.getDashboardData();
            if (data) {
                UI.renderDashboard(data);
                const pick = UI.defaultChartTarget(data);
                if (pick) UI.selectStock(pick.ticker, pick.price);
            }
            try {
                const freshPort = await portfolioPromise;
                if (freshPort) {
                    UI.renderPortfolio(freshPort);
                    if (freshPort.reconcile?.status === "error") {
                        UI.toast(freshPort.reconcile.message || "대조 실패", "error");
                    }
                }
            } catch (portErr) {
                console.warn("[TerminalApp] Initial portfolio fetch error:", portErr);
                if (portErr.data && portErr.data.portfolio) {
                    UI.renderPortfolio(portErr.data.portfolio);
                }
                UI.toast(portErr.message || "포트폴리오 대조 실패", "error");
            }
        } catch (err) {
            console.error("[TerminalApp] Initial dashboard fetch failed:", err);
            const lastUpEl = document.getElementById("lastUpdated");
            if (lastUpEl) lastUpEl.textContent = "대시보드 로드 실패";
            UI.toast(`대시보드를 불러오지 못했습니다: ${err.message || "오류"}`, "error");
        }
    },

    async refreshData() {
        try {
            const data = await ApiClient.getDashboardData();
            if (data) UI.renderDashboard(data);
        } catch (e) {
            console.warn("[TerminalApp] Refresh dashboard error:", e);
        }

        try {
            const freshPort = await ApiClient.getPortfolioData(true);
            if (freshPort) {
                UI.renderPortfolio(freshPort);
                if (freshPort.reconcile?.status === "error") {
                    UI.toast(freshPort.reconcile.message || "대조 실패", "error");
                }
            }
        } catch (e) {
            console.warn("[TerminalApp] Refresh portfolio error:", e);
            if (e.data && e.data.portfolio) {
                UI.renderPortfolio(e.data.portfolio);
            }
            UI.toast(e.message || "포트폴리오 대조 실패", "error");
        }
    },

    _setupEventListeners() {
        const btnSyncAll = document.getElementById("btnSyncAll");
        if (btnSyncAll) {
            btnSyncAll.onclick = async () => {
                if (WebSocketClient._scanTimeoutId) {
                    clearTimeout(WebSocketClient._scanTimeoutId);
                    WebSocketClient._scanTimeoutId = null;
                }
                btnSyncAll.disabled = true; 
                btnSyncAll.textContent = "SYNCING...";
                try {
                    const scanRes = await ApiClient.triggerScan();
                    UI.toast(scanRes.message || "스캔 요청 접수", "info");

                    if (scanRes.status === "already_scanning") {
                        btnSyncAll.disabled = false;
                        btnSyncAll.textContent = "SYNC / REFRESH";
                    } else if (scanRes.status === "scanning_started") {
                        WebSocketClient._scanTimeoutId = setTimeout(() => {
                            if (btnSyncAll.disabled) {
                                btnSyncAll.disabled = false;
                                btnSyncAll.textContent = "SYNC / REFRESH";
                                UI.toast("스캔 결과를 받지 못했습니다", "warn");
                            }
                        }, 120000);
                    }

                    // Run reconciliation
                    try {
                        const recRes = await ApiClient.syncBroker();
                        const rReport = recRes?.report || recRes;
                        if (rReport?.status === "success" || rReport?.status === "skipped") {
                            UI.toast("대조 완료", "success");
                        } else {
                            UI.toast(recRes?.detail || "대조 실패", "error");
                        }
                    } catch (recErr) {
                        UI.toast("대조 실패: " + recErr.message, "error");
                    }

                    await this.refreshData();
                } catch (e) {
                    UI.toast("Sync error: " + e.message, "error");
                    btnSyncAll.disabled = false; 
                    btnSyncAll.textContent = "SYNC / REFRESH";
                }
            };
        }

        // Clear Account
        const btnReset = document.getElementById("btnResetPortfolio");
        if (btnReset) {
            btnReset.onclick = async () => {
                if (!confirm("Reset entire portfolio?")) return;
                try { 
                    await ApiClient.resetPortfolio(); 
                    await this.refreshData(); 
                } catch (e) { 
                    UI.toast("Reset error: " + e.message, "error"); 
                }
            };
        }

        // Timeframe Switchers
        const tfDaily = document.getElementById("tfDaily");
        const tfWeekly = document.getElementById("tfWeekly");
        if (tfDaily && tfWeekly) {
            tfDaily.onclick = () => { tfDaily.classList.add("active"); tfWeekly.classList.remove("active"); ChartEngine.switchTimeframe("daily"); };
            tfWeekly.onclick = () => { tfWeekly.classList.add("active"); tfDaily.classList.remove("active"); ChartEngine.switchTimeframe("weekly"); };
        }

        // Toggle Helper for Badges
        const setupToggleBadge = (id, getFn, toggleFn, labelPrefix, activeClass, activeSuffix) => {
            const badge = document.getElementById(id);
            if (!badge) return;
            badge.style.cursor = "pointer";

            let isToggling = false;

            const setBadgeState = (state) => {
                // state: "active" | "standby" | "unknown"
                if (state === "active") {
                    badge.className = "popover-toggle-btn active";
                    badge.textContent = "ACTIVE";
                } else if (state === "standby") {
                    badge.className = "popover-toggle-btn disabled";
                    badge.textContent = "STANDBY";
                } else {
                    badge.className = "popover-toggle-btn disabled";
                    badge.textContent = "UNKNOWN";
                }
                if (labelPrefix === "GUARDIAN") {
                    try { UI.updateSystemHealth({ guardian: state }); } catch (e) {}
                }
                if (labelPrefix === "AUTOPILOT") {
                    try { UI.updateSystemHealth({ autopilot: state }); } catch (e) {}
                }
            };

            // Initial state: UNKNOWN
            badge.textContent = "UNKNOWN";
            badge.className = "popover-toggle-btn disabled";

            getFn().then((st) => {
                if (!st || typeof st !== 'object') {
                    setBadgeState("unreachable");
                    return;
                }
                const enabled = Boolean(st.is_enabled !== undefined ? st.is_enabled : st.enabled);
                setBadgeState(enabled ? "active" : "standby");
            }).catch((e) => {
                console.warn(`[${labelPrefix}] Init status failed:`, e);
                setBadgeState("unreachable");
            });

            badge.onclick = async () => {
                if (isToggling) return;
                isToggling = true;
                badge.style.opacity = "0.6";
                badge.style.pointerEvents = "none";
                try {
                    let st;
                    try {
                        st = await getFn();
                    } catch (e) {
                        UI.toast(`[${labelPrefix}] 상태 조회 실패로 변경할 수 없습니다.`, "error");
                        return;
                    }
                    if (!st || typeof st !== 'object') {
                        UI.toast(`[${labelPrefix}] 상태 조회 실패로 변경할 수 없습니다.`, "error");
                        return;
                    }
                    const currentEnabled = Boolean(st.is_enabled !== undefined ? st.is_enabled : st.enabled);
                    const next = !currentEnabled;
                    const res = await toggleFn(next);
                    const finalEnabled = res && (res.is_enabled !== undefined ? res.is_enabled : res.enabled) !== undefined
                        ? Boolean(res.is_enabled !== undefined ? res.is_enabled : res.enabled) : next;
                    setBadgeState(finalEnabled ? "active" : "standby");
                    UI.toast(`[${labelPrefix}] ${finalEnabled ? 'ACTIVE' : 'DISABLED'}`, finalEnabled ? "success" : "warn");
                } catch (e) {
                    console.error(`[${labelPrefix}] Toggle error:`, e);
                    UI.toast(`[${labelPrefix}] Toggle failed: ${e.message}`, "error");
                } finally {
                    isToggling = false;
                    badge.style.opacity = "";
                    badge.style.pointerEvents = "";
                }
            };
        };
        setupToggleBadge("guardianBadge", () => ApiClient.getGuardianStatus(), (n) => ApiClient.toggleGuardian(n), "GUARDIAN", "active-violet", "-7% / TP");
        setupToggleBadge("autopilotBadge", () => ApiClient.getAutoPilotStatus(), (n) => ApiClient.toggleAutoPilot(n), "AUTOPILOT", "active-green", "C-2");

        // Indicator Toggles
        const attachToggle = (id, fn) => {
            const btn = document.getElementById(id);
            if (btn) btn.onclick = () => fn(btn.classList.toggle("active"));
        };
        attachToggle("btnKijun", (v) => ChartEngine.toggleSeries("kijun", v));
        attachToggle("btnTenkan", (v) => ChartEngine.toggleSeries("tenkan", v));
        attachToggle("btnSpan", (v) => ChartEngine.toggleSeries("span", v));
        attachToggle("btnSma", (v) => ChartEngine.toggleSeries("sma", v));

        // Setup Unified System Status Popover
        const statusSign = document.getElementById("systemStatusSign");
        const statusPopover = document.getElementById("systemStatusPopover");
        const btnClosePopover = document.getElementById("btnCloseStatusPopover");
        if (statusSign && statusPopover) {
            statusSign.onclick = (e) => {
                e.stopPropagation();
                const isShown = statusPopover.style.display === "block";
                statusPopover.style.display = isShown ? "none" : "block";
                statusSign.setAttribute("aria-expanded", String(!isShown));
            };
            if (btnClosePopover) {
                btnClosePopover.onclick = (e) => {
                    e.stopPropagation();
                    statusPopover.style.display = "none";
                    statusSign.setAttribute("aria-expanded", "false");
                };
            }
            document.addEventListener("click", (e) => {
                if (!statusPopover.contains(e.target) && !statusSign.contains(e.target)) {
                    statusPopover.style.display = "none";
                    statusSign.setAttribute("aria-expanded", "false");
                }
            });
        }

        // Setup stock search input (safely no-op if removed from DOM)
        this._setupSearchInput();
    },

    _setupSearchInput() {
        const input = document.getElementById("stockSearchInput"), dropdown = document.getElementById("searchDropdown"), spinner = document.getElementById("searchSpinner");
        if (!input || !dropdown) return;
        window.addEventListener("keydown", (e) => {
            if (e.key === "/" && document.activeElement !== input) { e.preventDefault(); input.focus(); input.select(); }
        });
        let debounceTimer = null;
        input.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            const query = input.value.trim();
            if (!query) { dropdown.style.display = "none"; return; }
            if (spinner) spinner.style.display = "block";
            debounceTimer = setTimeout(async () => {
                try {
                    const results = await ApiClient.searchStocks(query);
                    if (spinner) spinner.style.display = "none";
                    if (!results || !results.length) {
                        dropdown.innerHTML = `<div style="padding:10px;color:#64748b;font-size:11px;text-align:center;">NO RESULTS FOUND</div>`;
                        dropdown.style.display = "block";
                        return;
                    }
                    dropdown.innerHTML = results.map((r) => {
                        const tk = UI.escapeHtml(r.ticker || ""), nm = UI.escapeHtml(r.name || r.name_kr || "");
                        const isKr = (r.ticker || "").endsWith(".KS") || (r.ticker || "").endsWith(".KQ");
                        const px = r.price > 0 ? (isKr ? "₩" + Number(r.price).toLocaleString() : "$" + Number(r.price).toFixed(2)) : "";
                        return `<div class="search-item" data-ticker="${tk}" data-price="${Number(r.price || 0)}" style="padding:8px 12px;border-bottom:1px solid #1e293b;cursor:pointer;display:flex;justify-content:space-between;align-items:center;"><div><strong style="color:#f8fafc;font-family:'JetBrains Mono';">${tk}</strong><span style="color:#94a3b8;font-size:11px;margin-left:6px;">${nm}</span></div><span style="color:#38bdf8;font-weight:700;font-family:'JetBrains Mono';">${px}</span></div>`;
                    }).join("");
                    dropdown.querySelectorAll(".search-item").forEach((item) => {
                        item.onclick = () => {
                            UI.selectStock(item.dataset.ticker, parseFloat(item.dataset.price || 0));
                            dropdown.style.display = "none";
                            input.value = "";
                        };
                    });
                    dropdown.style.display = "block";
                } catch (e) {
                    if (spinner) spinner.style.display = "none";
                    dropdown.innerHTML = `<div style="padding:10px;color:#ef4444;font-size:11px;text-align:center;">SEARCH FAILED</div>`;
                    dropdown.style.display = "block";
                }
            }, 250);
        });
        document.addEventListener("click", (e) => {
            if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.style.display = "none";
        });
    }
};
