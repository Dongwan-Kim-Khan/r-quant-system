/**
 * R QUANT TERMINAL: INTERACTIVE CHART ENGINE MODULE
 * Encapsulates TradingView Lightweight Charts, Series, Timeframe Switcher, and +26D Cloud Renderer.
 */

export const ChartEngine = {
    mainChart: null,
    volumeChart: null,
    candleSeries: null,
    kijunSeries: null,
    tenkanSeries: null,
    spanASeries: null,
    spanBSeries: null,
    sma20Series: null,
    sma60Series: null,
    volumeSeries: null,
    
    currentTimeframe: 'daily',
    currentLoadedChartObj: null,

    /**
     * Initialize Lightweight Charts and series
     */
    init() {
        if (typeof LightweightCharts === "undefined") {
            console.warn("[Chart] LightweightCharts library not ready, retrying in 200ms...");
            setTimeout(() => this.init(), 200);
            return;
        }
        if (this.mainChart) return;

        const chartDiv = document.getElementById("chartContainer");
        const volDiv = document.getElementById("volumeContainer");
        if (!chartDiv || !volDiv) return;

        this.mainChart = LightweightCharts.createChart(chartDiv, {
            layout: { background: { color: '#0f172a' }, textColor: '#94a3b8' },
            grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
            timeScale: {
                timeVisible: true,
                borderColor: '#334155',
                rightOffset: 32, // Space for 26-day forward cloud
                barSpacing: 9,
                minBarSpacing: 0.5,
                fixLeftEdge: false,
                fixRightEdge: false
            },
            rightPriceScale: { borderColor: '#334155', autoScale: true },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
        });

        this.candleSeries = this.mainChart.addCandlestickSeries({
            upColor: '#10b981', downColor: '#ef4444',
            borderUpColor: '#10b981', borderDownColor: '#ef4444',
            wickUpColor: '#10b981', wickDownColor: '#ef4444'
        });

        this.kijunSeries = this.mainChart.addLineSeries({ color: '#fbbf24', lineWidth: 2, title: '26D Kijun (기준선)' });
        this.tenkanSeries = this.mainChart.addLineSeries({ color: '#38bdf8', lineWidth: 2, title: '9D Tenkan (전환선)' });
        this.spanASeries = this.mainChart.addLineSeries({ color: '#10b981', lineWidth: 2, title: '선행스팬1 (Span A)' });
        this.spanBSeries = this.mainChart.addLineSeries({ color: '#ef4444', lineWidth: 2, title: '선행스팬2 (Span B)' });
        this.sma20Series = this.mainChart.addLineSeries({ color: '#f43f5e', lineWidth: 1, title: 'SMA 20' });
        this.sma60Series = this.mainChart.addLineSeries({ color: '#a855f7', lineWidth: 1, title: 'SMA 60' });

        this.volumeChart = LightweightCharts.createChart(volDiv, {
            layout: { background: { color: '#0f172a' }, textColor: '#94a3b8' },
            grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
            timeScale: {
                timeVisible: true,
                borderColor: '#334155',
                rightOffset: 32,
                barSpacing: 9,
                minBarSpacing: 0.5,
                fixLeftEdge: false,
                fixRightEdge: false
            },
            rightPriceScale: { borderColor: '#334155', autoScale: true }
        });
        this.volumeSeries = this.volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' } });

        // Synchronize timescale between price chart and volume chart
        this.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            this.volumeChart.timeScale().setVisibleLogicalRange(range);
        });

        // Setup UI Toggles
        this._setupToggle("btnKijun", this.kijunSeries);
        this._setupToggle("btnTenkan", this.tenkanSeries);
        this._setupToggle("btnSpan", [this.spanASeries, this.spanBSeries]);

        // Window resize observer
        window.addEventListener('resize', () => {
            if (chartDiv && this.mainChart) this.mainChart.applyOptions({ width: chartDiv.clientWidth });
            if (volDiv && this.volumeChart) this.volumeChart.applyOptions({ width: volDiv.clientWidth });
        });
    },

    _setupToggle(btnId, targetSeries) {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        btn.addEventListener("click", () => {
            btn.classList.toggle("active");
            const isVisible = btn.classList.contains("active");
            if (Array.isArray(targetSeries)) {
                targetSeries.forEach(s => s.applyOptions({ visible: isVisible }));
            } else if (targetSeries) {
                targetSeries.applyOptions({ visible: isVisible });
            }
        });
    },

    setTimeframe(tf) {
        this.currentTimeframe = tf;
        const dailyBtn = document.getElementById("tfDaily");
        const weeklyBtn = document.getElementById("tfWeekly");

        if (dailyBtn && weeklyBtn) {
            if (tf === 'weekly') {
                dailyBtn.classList.remove("active");
                weeklyBtn.classList.add("active");
            } else {
                weeklyBtn.classList.remove("active");
                dailyBtn.classList.add("active");
            }
        }

        if (this.currentLoadedChartObj) {
            this.renderData(this.currentLoadedChartObj, tf);
        }
    },

    renderData(chartObj, tf = null) {
        if (!chartObj) return;
        this.currentLoadedChartObj = chartObj;
        const targetTf = tf || this.currentTimeframe;

        if (!this.candleSeries) {
            this.init();
            if (!this.candleSeries) return;
        }

        let tfData = null;
        if (chartObj.timeframes && chartObj.timeframes[targetTf] && chartObj.timeframes[targetTf].candles && chartObj.timeframes[targetTf].candles.length > 0) {
            tfData = chartObj.timeframes[targetTf];
        } else {
            tfData = chartObj;
        }

        const candles = tfData.candles || [];
        this.candleSeries.setData(candles);
        this.kijunSeries.setData(tfData.kijun_line || []);
        this.tenkanSeries.setData(tfData.tenkan_line || []);
        this.spanASeries.setData(tfData.span_a_line || []);
        this.spanBSeries.setData(tfData.span_b_line || []);
        this.sma20Series.setData(tfData.sma20 || []);
        this.sma60Series.setData(tfData.sma60 || []);
        this.volumeSeries.setData(tfData.volume || []);

        // Dynamic timescale framing
        requestAnimationFrame(() => {
            try {
                const totalCandles = candles.length;
                this.mainChart.timeScale().resetTimeScale();
                this.volumeChart.timeScale().resetTimeScale();

                if (totalCandles > 0) {
                    const barsToShow = targetTf === 'weekly' ? Math.min(80, totalCandles) : Math.min(130, totalCandles);
                    const targetRange = {
                        from: totalCandles - barsToShow,
                        to: totalCandles + 28
                    };
                    this.mainChart.timeScale().setVisibleLogicalRange(targetRange);
                    this.volumeChart.timeScale().setVisibleLogicalRange(targetRange);
                } else {
                    this.mainChart.timeScale().fitContent();
                    this.volumeChart.timeScale().fitContent();
                }
            } catch (e) {
                this.mainChart.timeScale().fitContent();
                this.volumeChart.timeScale().fitContent();
            }
        });
    },

    renderNotFound(ticker) {
        if (this.candleSeries) {
            this.candleSeries.setData([]);
            this.kijunSeries.setData([]);
            this.tenkanSeries.setData([]);
            this.spanASeries.setData([]);
            this.spanBSeries.setData([]);
            this.sma20Series.setData([]);
            this.sma60Series.setData([]);
            this.volumeSeries.setData([]);
        }

        const curPrice = document.getElementById("curPrice");
        const qbPrice = document.getElementById("qbPrice");
        const qbBuyPrice = document.getElementById("qbBuyPrice");
        const tgtEl = document.getElementById("qbTargetVal");
        const stopEl = document.getElementById("qbStopVal");

        if (curPrice) curPrice.textContent = "N/A (조회 불가)";
        if (qbPrice) qbPrice.textContent = "N/A";
        if (qbBuyPrice) qbBuyPrice.value = "";
        if (tgtEl) tgtEl.textContent = "-";
        if (stopEl) stopEl.textContent = "-";
    }
};
