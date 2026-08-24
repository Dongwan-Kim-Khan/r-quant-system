/**
 * R QUANT TERMINAL: REAL-TIME WEBSOCKET & SMART POLLING MODULE
 * Manages WebSocket live hub stream with exponential backoff & smart polling pause.
 */

import { ApiClient } from './api.js';
import { UI } from './ui.js';

export const WebSocketClient = {
    socket: null,
    reconnectAttempts: 0,
    maxReconnectDelay: 30000,
    pollingInterval: null,
    isSocketConnected: false,

    init() {
        this.connect();
    },

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this._setStatus("CONNECTING...", "#f59e0b");

        try {
            this.socket = new WebSocket(wsUrl);

            this.socket.onopen = () => {
                console.log("[WS] Connected to live quant broadcast hub.");
                this.isSocketConnected = true;
                this.reconnectAttempts = 0;
                this._setStatus("API ACTIVE", "#10b981");

                // Smart Polling Pause: Stop HTTP polling when WebSocket is live
                this._stopHttpPolling();
            };

            this.socket.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    this.handleMessage(msg);
                } catch (err) {
                    console.warn("[WS] Error parsing message:", err);
                }
            };

            this.socket.onclose = () => {
                console.warn("[WS] Disconnected from hub. Initiating backoff reconnect...");
                this.isSocketConnected = false;
                this._setStatus("LOCAL CACHE (RECONNECTING)", "#3b82f6");

                // Start Fallback HTTP polling while disconnected
                this._startHttpPolling();
                this._scheduleReconnect();
            };

            this.socket.onerror = (err) => {
                console.warn("[WS] Socket error:", err);
            };

        } catch (err) {
            console.error("[WS] Initialization exception:", err);
            this._startHttpPolling();
            this._scheduleReconnect();
        }
    },

    handleMessage(msg) {
        if (!msg || !msg.type) return;

        if (msg.type === "live_feed_update" && msg.data) {
            UI.renderDashboard(msg.data);
        } else if (msg.type === "scan_status") {
            const btnScan = document.getElementById("btnScanNow");
            if (msg.status === "completed") {
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "Run Live Scan";
                }
            } else if (msg.status === "error") {
                if (btnScan) {
                    btnScan.disabled = false;
                    btnScan.textContent = "Run Live Scan";
                }
                alert(msg.message || "스캔 중 오류가 발생했습니다.");
            }
        }
    },

    _scheduleReconnect() {
        this.reconnectAttempts++;
        const baseDelay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), this.maxReconnectDelay);
        const jitter = Math.random() * 1000;
        const delay = baseDelay + jitter;

        setTimeout(() => {
            if (!this.isSocketConnected) {
                this.connect();
            }
        }, delay);
    },

    _startHttpPolling() {
        if (this.pollingInterval) return;
        this.pollingInterval = setInterval(async () => {
            if (!this.isSocketConnected) {
                const fresh = await ApiClient.getDashboardData();
                if (fresh) UI.renderDashboard(fresh);
            }
        }, 30000);
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
        if (sDot) sDot.style.background = color;
    }
};
