/**
 * R QUANT TERMINAL: WebSocket hub, heartbeat, and TerminalApp controller.
 */
import { ApiClient } from './api.js?v=4.3.3';
import { UI } from './ui.js?v=4.3.3';
import { ChartEngine } from './chart.js?v=4.3.3';

export const ConnectionState = {
    DISCONNECTED: "DISCONNECTED",
    CONNECTING: "CONNECTING",
    CONNECTED: "CONNECTED",
    REAUTHENTICATING: "REAUTHENTICATING",
    ERROR: "ERROR",
};

export const WebSocketClient = {
    socket: null,
    reconnectAttempts: 0,
    maxReconnectDelay: 10000,
    reconnectTimeoutId: null,
    pollingInterval: null,
    heartbeatInterval: null,
    lastPongReceived: Date.now(),
    isSocketConnected: false,
    connectionState: "DISCONNECTED",

    init() {
        this._setupLifecycleListeners();
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

    checkAndReconnect() {
        const wsOpen = this.socket && this.socket.readyState === WebSocket.OPEN;
        const isStale = (Date.now() - this.lastPongReceived) > 30000;
        if (wsOpen && !isStale && this.connectionState === ConnectionState.CONNECTED) {
            this._stopHttpPolling();
            return;
        }
        if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
            return;
        }
        this._setConnectionState(ConnectionState.REAUTHENTICATING);
        this._hydrateFromHttp();
        this._startHttpPolling();
        this.reconnectAttempts = 0;
        this.connect();
    },

    connect() {
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
            const tr = msg.data;
            UI.toast(`[AUTOPILOT] ${tr.ticker} ${tr.shares} SH @ $${Number(tr.buy_price).toFixed(2)}`, "success");
        } else if (eventType === "guardian_alert" && msg.data) {
            const act = msg.data;
            UI.toast(`[GUARDIAN] ${act.ticker} ${act.pnl_pct}% @ $${Number(act.sell_price || 0).toFixed(2)} — ${act.reason || "exit"}`, "warn");
        } else if (eventType === "scan_status") {
            const btnScan = document.getElementById("btnScanNow");
            const dataStatus = (msg.data && msg.data.status) ? msg.data.status : msg.status;
            if (dataStatus === "completed") {
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "RUN SCAN";
                }
            } else if (dataStatus === "started") {
                if (btnScan) {
                    btnScan.disabled = true;
                    btnScan.textContent = "SCANNING...";
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
        if (this.connectionState === ConnectionState.CONNECTED) {
            this._stopHttpPolling();
            return;
        }
        if (this.pollingInterval) return;
        this.pollingInterval = setInterval(async () => {
            if (this.connectionState === ConnectionState.CONNECTED) {
                this._stopHttpPolling();
                return;
            }
            try {
                const fresh = await ApiClient.getDashboardData();
                if (fresh) UI.renderDashboard(fresh);
                const freshPort = await ApiClient.getPortfolioData();
                if (freshPort) UI.renderPortfolio(freshPort);
            } catch (e) {}
        }, 5000);
    },

    _stopHttpPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    },

    _hydrateFromHttp() {
        ApiClient.getDashboardData().then(fresh => {
            if (fresh) UI.renderDashboard(fresh);
            return ApiClient.getPortfolioData();
        }).then(freshPort => {
            if (freshPort) UI.renderPortfolio(freshPort);
        }).catch(() => {});
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
        UI.renderSlotVisualizer(
            dash.portfolio,
            dash.slot_allocation_summary && dash.slot_allocation_summary.is_bull_regime,
            dash.top_conviction_pick,
            dash.slot_allocation_summary
        );
        UI.renderEngineOverlay(dash.slot_allocation_summary, dash.macro, dash.portfolio, dash.risk_constitution);
    },

    _setConnectionState(state) {
        this.connectionState = state;
        const labels = {
            DISCONNECTED: ["OFFLINE", "#ef4444"],
            CONNECTING: ["CONNECTING", "#f59e0b"],
            CONNECTED: ["LIVE HUB", "#10b981"],
            REAUTHENTICATING: ["REAUTH", "#f59e0b"],
            ERROR: ["ERROR", "#ef4444"],
        };
        const pair = labels[state];
        if (pair) this._setStatus(pair[0], pair[1]);
        if (state === ConnectionState.CONNECTED) this._stopHttpPolling();
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
            const isLive = Boolean(brokerSt && brokerSt.is_configured);
            const kisText = document.getElementById("kisStatusText"), kisDot = document.getElementById("kisStatusDot"), kisBox = document.getElementById("kisBrokerStatusBox");
            if (kisText) kisText.textContent = isLive ? "KIS API: CONNECTED" : "KIS API: SIMULATION";
            if (kisDot) { kisDot.style.background = isLive ? "#34d399" : "#f59e0b"; kisDot.style.boxShadow = `0 0 6px ${isLive ? '#34d399' : '#f59e0b'}`; }
            if (kisBox) kisBox.style.background = isLive ? "#064e3b" : "#1e293b";
        }).catch(() => {});

        // 3. Fetch dashboard and broker-reconciled portfolio concurrently.
        // Render the feed immediately; then replace its portfolio with broker SSOT.
        try {
            const portfolioPromise = ApiClient.getPortfolioData(true);
            const data = await ApiClient.getDashboardData();
            if (data) {
                UI.renderDashboard(data);
                const pick = UI.defaultChartTarget(data);
                if (pick) UI.selectStock(pick.ticker, pick.price);
            }
            const freshPort = await portfolioPromise;
            if (freshPort) UI.renderPortfolio(freshPort);
        } catch (err) {
            console.error("[TerminalApp] Initial dashboard fetch failed:", err);
        }
    },

    async refreshData() {
        try {
            const data = await ApiClient.getDashboardData();
            if (data) UI.renderDashboard(data);
            const freshPort = await ApiClient.getPortfolioData(true);
            if (freshPort) UI.renderPortfolio(freshPort);
        } catch (e) {
            console.warn("[TerminalApp] Refresh error:", e);
        }
    },

    _setupEventListeners() {
        // Run Live Scan
        const btnScan = document.getElementById("btnScanNow");
        if (btnScan) {
            btnScan.onclick = async () => {
                btnScan.disabled = true; btnScan.textContent = "SCANNING...";
                try {
                    await ApiClient.triggerScan();
                    const fresh = await ApiClient.getDashboardData();
                    if (fresh) UI.renderDashboard(fresh);
                } catch (e) { UI.toast("Scan error: " + e.message, "error"); }
                finally { btnScan.disabled = false; btnScan.textContent = "RUN SCAN"; }
            };
        }

        // Refresh Button
        const btnRefresh = document.getElementById("btnRefresh");
        if (btnRefresh) btnRefresh.onclick = () => this.refreshData();

        // Daily Sync
        const btnSync = document.getElementById("btnSyncBroker");
        if (btnSync) {
            btnSync.onclick = async () => {
                btnSync.disabled = true; btnSync.textContent = "SYNCING...";
                try { await ApiClient.syncBroker(); this.refreshData(); }
                catch (e) { UI.toast("Sync error: " + e.message, "error"); }
                finally { btnSync.disabled = false; btnSync.textContent = "SYNC"; }
            };
        }

        // Clear Account
        const btnReset = document.getElementById("btnResetPortfolio");
        if (btnReset) {
            btnReset.onclick = async () => {
                if (!confirm("Reset entire portfolio?")) return;
                try { await ApiClient.resetPortfolio(); this.refreshData(); }
                catch (e) { UI.toast("Reset error: " + e.message, "error"); }
            };
        }

        // Quick Buy Button
        const btnExecBuy = document.getElementById("btnExecuteBuy");
        if (btnExecBuy) {
            btnExecBuy.onclick = async () => {
                if (btnExecBuy.disabled) return;
                const ticker = (UI.currentSelectedTicker || ChartEngine.currentTicker || document.getElementById("qbTicker")?.textContent || "").trim();
                if (!ticker || ticker === "---") {
                    UI.toast("Please select a stock first.", "warn");
                    return;
                }
                const price = parseFloat(document.getElementById("qbBuyPrice")?.value || 0);
                const qty = parseFloat(document.getElementById("qbQty")?.value || 1);
                if (price <= 0 || qty <= 0) {
                    UI.toast("Please enter valid price and quantity.", "warn");
                    return;
                }
                btnExecBuy.disabled = true;
                btnExecBuy.textContent = "BUYING...";
                try {
                    await ApiClient.buyStock(ticker, price, qty);
                    console.log(`[ORDER COMPLETED] ${ticker} ${qty} SH`);
                    this.refreshData();
                } catch (e) {
                    UI.toast("Buy Failed: " + e.message, "error");
                } finally {
                    setTimeout(() => {
                        btnExecBuy.disabled = false;
                        btnExecBuy.textContent = "BUY SLOT";
                    }, 2000);
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

            const setBadgeState = (enabled) => {
                badge.className = enabled ? `status-pill-chip ${activeClass}` : "status-pill-chip disabled";
                const text = enabled ? `${labelPrefix}: ${activeSuffix}` : `${labelPrefix}: OFF`;
                badge.innerHTML = `<span class="badge-dot"></span><span>${text}</span>`;
            };

            // Fetch initial status on load to ensure UI matches backend daemon state
            getFn().then((st) => {
                const isEnabled = Boolean(st && (st.is_enabled !== undefined ? st.is_enabled : st.enabled));
                setBadgeState(isEnabled);
            }).catch((e) => {
                console.warn(`[${labelPrefix}] Init status check failed:`, e);
            });

            badge.onclick = async () => {
                if (isToggling) return;
                isToggling = true;
                badge.style.opacity = "0.6";
                badge.style.pointerEvents = "none";
                try {
                    const st = await getFn();
                    const current = Boolean(st && (st.is_enabled !== undefined ? st.is_enabled : st.enabled));
                    const next = !current;
                    const res = await toggleFn(next);
                    const finalState = res && (res.is_enabled !== undefined ? res.is_enabled : res.enabled) !== undefined
                        ? Boolean(res.is_enabled !== undefined ? res.is_enabled : res.enabled)
                        : next;
                    setBadgeState(finalState);
                    UI.toast(`[${labelPrefix}] ${finalState ? 'ACTIVE' : 'DISABLED'}`, finalState ? "success" : "warn");
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
        setupToggleBadge("guardianBadge", () => ApiClient.getGuardianStatus(), (n) => ApiClient.toggleGuardian(n), "GUARDIAN", "active-violet", "-5% / TP");
        setupToggleBadge("autopilotBadge", () => ApiClient.getAutoPilotStatus(), (n) => ApiClient.toggleAutoPilot(n), "AUTOPILOT", "active-green", "C1-M2");

        // Indicator Toggles
        const attachToggle = (id, fn) => {
            const btn = document.getElementById(id);
            if (btn) btn.onclick = () => fn(btn.classList.toggle("active"));
        };
        attachToggle("btnKijun", (v) => ChartEngine.toggleSeries("kijun", v));
        attachToggle("btnTenkan", (v) => ChartEngine.toggleSeries("tenkan", v));
        attachToggle("btnSpan", (v) => ChartEngine.toggleSeries("span", v));
        attachToggle("btnSma", (v) => ChartEngine.toggleSeries("sma", v));

        // Setup stock search input
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
                }
            }, 250);
        });
        document.addEventListener("click", (e) => {
            if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.style.display = "none";
        });
    }
};
