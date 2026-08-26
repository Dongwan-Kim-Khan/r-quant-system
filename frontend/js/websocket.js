/**
 * R QUANT TERMINAL v2: REAL-TIME WEBSOCKET & APP CONTROLLER MODULE
 * Professional 24/7 Persistent WebSocket Hub with Active Heartbeat (Ping/Pong)
 * and Instant Auto-Resume on Tab Visibility/Focus.
 */
import { ApiClient } from './api.js?v=4.0.6';
import { UI } from './ui.js?v=4.0.6';
import { ChartEngine } from './chart.js?v=4.0.6';

export const WebSocketClient = {
    socket: null,
    reconnectAttempts: 0,
    maxReconnectDelay: 10000,
    reconnectTimeoutId: null,
    pollingInterval: null,
    heartbeatInterval: null,
    lastPongReceived: Date.now(),
    isSocketConnected: false,

    init() {
        this._setupLifecycleListeners();
        this.connect();
    },

    _setupLifecycleListeners() {
        // Auto-Resume when switching tabs or waking computer from sleep
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") {
                this.checkAndReconnect();
            }
        });

        window.addEventListener("online", () => {
            this.checkAndReconnect();
        });

        // Periodic 30-second sanity check for idle tabs
        setInterval(() => {
            if (document.visibilityState === "visible") {
                this.checkAndReconnect();
            }
        }, 30000);
    },

    checkAndReconnect() {
        if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
            return; // Still establishing connection, do not interrupt
        }
        const isStale = (Date.now() - this.lastPongReceived) > 60000;
        if (!this.socket || this.socket.readyState !== WebSocket.OPEN || isStale) {
            console.log("[WS] Resuming connection (stale or disconnected)...");
            this.reconnectAttempts = 0;
            this.connect();
        }
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
            this._setStatus("LOCAL FILE", "#3b82f6");
            return;
        }

        this._setStatus("CONNECTING...", "#f59e0b");

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
                this._setStatus("LIVE HUB", "#10b981");
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
                this._setStatus("RECONNECTING", "#f59e0b");
                this._startHttpPolling();
                this._scheduleReconnect();
            };

            this.socket.onerror = (err) => {
                console.warn("[WS] Socket error:", err);
                this._setStatus("OFFLINE", "#ef4444");
            };

        } catch (err) {
            console.error("[WS] Initialization exception:", err);
            this._stopHeartbeat();
            this._setStatus("OFFLINE", "#ef4444");
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
        } else if (eventType === "portfolio_update" && msg.data) {
            UI.renderPortfolio(msg.data);
            UI.renderKPIs(null, msg.data, false);
        } else if (eventType === "connected" && msg.data && msg.data.portfolio) {
            UI.renderPortfolio(msg.data.portfolio);
            UI.renderKPIs(null, msg.data.portfolio, false);
        } else if (eventType === "autopilot_buy_alert" && msg.data) {
            const tr = msg.data;
            alert(`[AUTOPILOT: ORDER COMPLETED]\n\n• TICKER: ${tr.ticker} (${tr.name})\n• SHARES: ${tr.shares} SH\n• PRICE: $${Number(tr.buy_price).toFixed(2)}\n• STRATEGY: ${tr.strategy}`);
            ApiClient.getDashboardData().then(fresh => {
                if (fresh) UI.renderDashboard(fresh);
            });
        } else if (eventType === "guardian_alert" && msg.data) {
            const act = msg.data;
            alert(`[PORTFOLIO GUARDIAN TRIGGERED]\n\n• TICKER: ${act.ticker}\n• PNL: ${act.pnl_pct}%\n• EXIT PRICE: $${Number(act.sell_price).toFixed(2)}\n• REASON: ${act.reason}`);
            ApiClient.getDashboardData().then(fresh => {
                if (fresh) UI.renderDashboard(fresh);
            });
        } else if (eventType === "scan_status") {
            const btnScan = document.getElementById("btnScanNow");
            const dataStatus = (msg.data && msg.data.status) ? msg.data.status : msg.status;
            if (dataStatus === "completed") {
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "RUN SCAN";
                }
                ApiClient.getDashboardData().then(fresh => {
                    if (fresh) UI.renderDashboard(fresh);
                });
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
        if (this.pollingInterval) return;
        this.pollingInterval = setInterval(async () => {
            try {
                const fresh = await ApiClient.getDashboardData();
                if (fresh) UI.renderDashboard(fresh);
                const freshPort = await ApiClient.getPortfolioData();
                if (freshPort) UI.renderPortfolio(freshPort);
            } catch (e) {}
        }, 10000);
    },

    _stopHttpPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
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
            const kisText = document.getElementById("kisStatusText");
            const kisDot = document.getElementById("kisStatusDot");
            const kisBox = document.getElementById("kisBrokerStatusBox");
            if (brokerSt && brokerSt.is_configured) {
                if (kisText) kisText.textContent = "KIS API: CONNECTED";
                if (kisDot) { kisDot.style.background = "#34d399"; kisDot.style.boxShadow = "0 0 6px #34d399"; }
                if (kisBox) kisBox.style.background = "#064e3b";
            } else {
                if (kisText) kisText.textContent = "KIS API: SIMULATION";
                if (kisDot) { kisDot.style.background = "#f59e0b"; kisDot.style.boxShadow = "0 0 6px #f59e0b"; }
                if (kisBox) kisBox.style.background = "#1e293b";
            }
        }).catch(() => {});

        // 3. Initial Dashboard Data Fetch & Immediate Chart Selection
        try {
            const data = await ApiClient.getDashboardData();
            if (data) {
                UI.renderDashboard(data);
                // Default to active holding if exists, otherwise top pick / NVDA
                const holdings = (data.portfolio && data.portfolio.holdings) || [];
                let defaultTicker = "NVDA";
                let defaultPrice = 0;
                if (holdings.length > 0) {
                    defaultTicker = holdings[0].ticker;
                    defaultPrice = Number(holdings[0].current_price || holdings[0].buy_price || 0);
                } else if (data.top_conviction_pick) {
                    defaultTicker = data.top_conviction_pick.ticker;
                    defaultPrice = Number(data.top_conviction_pick.price || 0);
                } else if (data.dual_consensus && data.dual_consensus.length > 0) {
                    defaultTicker = data.dual_consensus[0].ticker;
                    defaultPrice = Number(data.dual_consensus[0].price || 0);
                } else if (data.strat1_exclusive && data.strat1_exclusive.length > 0) {
                    defaultTicker = data.strat1_exclusive[0].ticker;
                    defaultPrice = Number(data.strat1_exclusive[0].price || 0);
                }
                UI.selectStock(defaultTicker, defaultPrice);
            }
        } catch (err) {
            console.error("[TerminalApp] Initial dashboard fetch failed:", err);
        }
    },

    async refreshData() {
        try {
            const data = await ApiClient.getDashboardData();
            if (data) UI.renderDashboard(data);
        } catch (e) {
            console.warn("[TerminalApp] Refresh error:", e);
        }
    },

    _setupEventListeners() {
        // Run Live Scan
        const btnScan = document.getElementById("btnScanNow");
        if (btnScan) {
            btnScan.onclick = async () => {
                btnScan.disabled = true;
                btnScan.textContent = "SCANNING...";
                try {
                    await ApiClient.triggerScan();
                    const fresh = await ApiClient.getDashboardData();
                    if (fresh) UI.renderDashboard(fresh);
                } catch (e) {
                    alert("Scan error: " + e.message);
                } finally {
                    btnScan.disabled = false;
                    btnScan.textContent = "RUN SCAN";
                }
            };
        }

        // Refresh Button
        const btnRefresh = document.getElementById("btnRefresh");
        if (btnRefresh) {
            btnRefresh.onclick = () => this.refreshData();
        }

        // Daily Sync
        const btnSync = document.getElementById("btnSyncBroker");
        if (btnSync) {
            btnSync.onclick = async () => {
                btnSync.disabled = true;
                btnSync.textContent = "SYNCING...";
                try {
                    await ApiClient.syncBroker();
                    this.refreshData();
                } catch (e) {
                    alert("Sync error: " + e.message);
                } finally {
                    btnSync.disabled = false;
                    btnSync.textContent = "DAILY SYNC";
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
                    this.refreshData();
                } catch (e) {
                    alert("Reset error: " + e.message);
                }
            };
        }

        // Quick Buy Button
        const btnExecBuy = document.getElementById("btnExecuteBuy");
        if (btnExecBuy) {
            btnExecBuy.onclick = async () => {
                if (btnExecBuy.disabled) return;
                const ticker = (document.getElementById("qbTicker")?.textContent || "NVDA").trim();
                const price = parseFloat(document.getElementById("qbBuyPrice")?.value || 0);
                const qty = parseFloat(document.getElementById("qbQty")?.value || 1);
                if (price <= 0 || qty <= 0) {
                    alert("Please enter valid price and quantity.");
                    return;
                }
                btnExecBuy.disabled = true;
                btnExecBuy.textContent = "BUYING...";
                try {
                    await ApiClient.buyStock(ticker, price, qty);
                    console.log(`[ORDER COMPLETED] ${ticker} ${qty} SH`);
                    this.refreshData();
                } catch (e) {
                    alert("Buy Failed: " + e.message);
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
            tfDaily.onclick = () => {
                tfDaily.classList.add("active");
                tfWeekly.classList.remove("active");
                ChartEngine.switchTimeframe("daily");
            };
            tfWeekly.onclick = () => {
                tfWeekly.classList.add("active");
                tfDaily.classList.remove("active");
                ChartEngine.switchTimeframe("weekly");
            };
        }

        // Guardian Daemon Toggle Badge
        const guardianBadge = document.getElementById("guardianBadge");
        if (guardianBadge) {
            guardianBadge.style.cursor = "pointer";
            guardianBadge.onclick = async () => {
                try {
                    const status = await ApiClient.getGuardianStatus();
                    const nextState = !(status && status.enabled);
                    await ApiClient.toggleGuardian(nextState);
                    guardianBadge.className = nextState ? "chip chip-green" : "chip chip-gray";
                    guardianBadge.textContent = nextState ? "GUARDIAN: ACTIVE" : "GUARDIAN: DISABLED";
                    console.log(`[GUARDIAN] Toggled to ${nextState ? 'ACTIVE' : 'DISABLED'}`);
                } catch (e) {
                    console.error("[GUARDIAN] Toggle error:", e);
                }
            };
        }

        // AutoPilot Daemon Toggle Badge
        const autopilotBadge = document.getElementById("autopilotBadge");
        if (autopilotBadge) {
            autopilotBadge.style.cursor = "pointer";
            autopilotBadge.onclick = async () => {
                try {
                    const status = await ApiClient.getAutoPilotStatus();
                    const nextState = !(status && status.enabled);
                    await ApiClient.toggleAutoPilot(nextState);
                    autopilotBadge.className = nextState ? "chip chip-green" : "chip chip-gray";
                    autopilotBadge.textContent = nextState ? "AUTOPILOT: ACTIVE" : "AUTOPILOT: DISABLED";
                    console.log(`[AUTOPILOT] Toggled to ${nextState ? 'ACTIVE' : 'DISABLED'}`);
                } catch (e) {
                    console.error("[AUTOPILOT] Toggle error:", e);
                }
            };
        }

        // Indicator Toggles
        const attachToggle = (id, fn) => {
            const btn = document.getElementById(id);
            if (btn) {
                btn.onclick = () => {
                    const isActive = btn.classList.toggle("active");
                    fn(isActive);
                };
            }
        };
        attachToggle("btnKijun", (v) => ChartEngine.toggleSeries("kijun", v));
        attachToggle("btnTenkan", (v) => ChartEngine.toggleSeries("tenkan", v));
        attachToggle("btnSpan", (v) => ChartEngine.toggleSeries("span", v));
        attachToggle("btnSma", (v) => ChartEngine.toggleSeries("sma", v));

        // Setup stock search input
        this._setupSearchInput();
    },

    _setupSearchInput() {
        const input = document.getElementById("stockSearchInput");
        const dropdown = document.getElementById("searchDropdown");
        const spinner = document.getElementById("searchSpinner");
        if (!input || !dropdown) return;

        // Shortcut key "/"
        window.addEventListener("keydown", (e) => {
            if (e.key === "/" && document.activeElement !== input) {
                e.preventDefault();
                input.focus();
                input.select();
            }
        });

        let debounceTimer = null;
        input.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            const query = input.value.trim();
            if (!query) {
                dropdown.style.display = "none";
                return;
            }
            if (spinner) spinner.style.display = "block";
            debounceTimer = setTimeout(async () => {
                try {
                    const results = await ApiClient.searchStocks(query);
                    if (spinner) spinner.style.display = "none";
                    if (results && results.length > 0) {
                        dropdown.innerHTML = '';
                        results.forEach(r => {
                            const isKr = (r.ticker || '').endsWith('.KS') || (r.ticker || '').endsWith('.KQ');
                            const priceDisplay = r.price && r.price > 0
                                ? (isKr ? '₩' + Number(r.price).toLocaleString() : '$' + Number(r.price).toFixed(2))
                                : '';
                            const nameDisplay = r.name || r.name_kr || '';

                            // SEC-04: Build DOM nodes via textContent to prevent XSS
                            const item = document.createElement('div');
                            item.className = 'search-item';
                            item.style.cssText = 'padding:8px 12px; border-bottom:1px solid #1e293b; cursor:pointer; display:flex; justify-content:space-between; align-items:center;';
                            item.dataset.ticker = r.ticker || '';
                            item.dataset.price = String(r.price || 0);

                            const leftDiv = document.createElement('div');
                            const strongEl = document.createElement('strong');
                            strongEl.style.cssText = 'color:#f8fafc; font-family:\'JetBrains Mono\';';
                            strongEl.textContent = r.ticker || '';

                            const spanEl = document.createElement('span');
                            spanEl.style.cssText = 'color:#94a3b8; font-size:11px; margin-left:6px;';
                            spanEl.textContent = nameDisplay;

                            leftDiv.appendChild(strongEl);
                            leftDiv.appendChild(spanEl);

                            const priceSpan = document.createElement('span');
                            priceSpan.style.cssText = 'color:#38bdf8; font-weight:700; font-family:\'JetBrains Mono\';';
                            priceSpan.textContent = priceDisplay;

                            item.appendChild(leftDiv);
                            item.appendChild(priceSpan);
                            dropdown.appendChild(item);
                        });
                        dropdown.style.display = "block";

                        dropdown.querySelectorAll('.search-item').forEach(item => {
                            item.onclick = () => {
                                const t = item.getAttribute('data-ticker');
                                const p = parseFloat(item.getAttribute('data-price'));
                                UI.selectStock(t, p);
                                dropdown.style.display = "none";
                                input.value = '';
                            };
                        });
                    } else {
                        dropdown.innerHTML = `<div style="padding:10px; color:#64748b; font-size:11px; text-align:center;">NO RESULTS FOUND</div>`;
                        dropdown.style.display = "block";
                    }
                } catch (e) {
                    if (spinner) spinner.style.display = "none";
                }
            }, 250);
        });

        document.addEventListener("click", (e) => {
            if (!input.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = "none";
            }
        });
    }
};
