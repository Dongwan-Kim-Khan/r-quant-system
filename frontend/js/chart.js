/**
 * R QUANT TERMINAL v2: INTERACTIVE CHART ENGINE MODULE
 * Encapsulates TradingView Lightweight Charts, Series, Timeframe Switcher, and +26D Cloud Renderer.
 */
import { ApiClient } from './api.js?v=4.0.6';
import { QuantDecoder } from './decoder.js?v=4.0.6';

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
    currentTicker: "NVDA",

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

        chartDiv.innerHTML = "";
        volDiv.innerHTML = "";

        const initialWidth = chartDiv.clientWidth || 800;

        this.mainChart = LightweightCharts.createChart(chartDiv, {
            width: initialWidth,
            height: 360,
            layout: { background: { color: '#16181a' }, textColor: '#8d969e' },
            grid: { vertLines: { color: 'rgba(255, 255, 255, 0.04)' }, horzLines: { color: 'rgba(255, 255, 255, 0.04)' } },
            timeScale: {
                timeVisible: true,
                borderColor: 'rgba(255, 255, 255, 0.10)',
                rightOffset: 32, // Space for 26-day forward cloud
                barSpacing: 9,
                minBarSpacing: 0.5,
                fixLeftEdge: false,
                fixRightEdge: false
            },
            rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.10)', autoScale: true },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
        });

        this.candleSeries = this.mainChart.addCandlestickSeries({
            upColor: '#34d399', downColor: '#e23b4a',
            borderUpColor: '#34d399', borderDownColor: '#e23b4a',
            wickUpColor: '#34d399', wickDownColor: '#e23b4a'
        });

        this.kijunSeries = this.mainChart.addLineSeries({ color: '#fbbf24', lineWidth: 2, title: '26D Kijun (기준선)' });
        this.tenkanSeries = this.mainChart.addLineSeries({ color: '#4f55f1', lineWidth: 2, title: '9D Tenkan (전환선)' });
        this.spanASeries = this.mainChart.addLineSeries({ color: '#34d399', lineWidth: 2, title: '선행스팬1 (Span A)' });
        this.spanBSeries = this.mainChart.addLineSeries({ color: '#e23b4a', lineWidth: 2, title: '선행스팬2 (Span B)' });
        this.sma20Series = this.mainChart.addLineSeries({ color: '#f43f5e', lineWidth: 1, title: 'SMA 20' });
        this.sma60Series = this.mainChart.addLineSeries({ color: '#a855f7', lineWidth: 1, title: 'SMA 60' });

        this.volumeChart = LightweightCharts.createChart(volDiv, {
            width: initialWidth,
            height: 90,
            layout: { background: { color: '#16181a' }, textColor: '#8d969e' },
            grid: { vertLines: { color: 'rgba(255, 255, 255, 0.04)' }, horzLines: { color: 'rgba(255, 255, 255, 0.04)' } },
            timeScale: {
                timeVisible: true,
                borderColor: 'rgba(255, 255, 255, 0.10)',
                rightOffset: 32,
                barSpacing: 9,
                minBarSpacing: 0.5,
                fixLeftEdge: false,
                fixRightEdge: false
            },
            rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.10)', autoScale: true }
        });
        this.volumeSeries = this.volumeChart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: ''
        });

        // Synchronize timescale between price chart and volume chart
        this.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (this.volumeChart && range) this.volumeChart.timeScale().setVisibleLogicalRange(range);
        });

        // Synchronize crosshair & floating OHLCV legend
        this.mainChart.subscribeCrosshairMove(param => {
            this.updateOhlcvLegend(param);
        });

        // Window resize observer
        window.addEventListener('resize', () => {
            const w = chartDiv.clientWidth || 800;
            if (this.mainChart) this.mainChart.applyOptions({ width: w });
            if (this.volumeChart) this.volumeChart.applyOptions({ width: w, height: 90 });
        });

        // If data was loaded before chart initialization finished, render now
        if (this.currentLoadedChartObj) {
            this.renderData(this.currentLoadedChartObj);
        }
    },

    updateOhlcvLegend(param) {
        const ohlcvEl = document.getElementById("ohlcvLegend");
        if (!ohlcvEl || !this.candleSeries) return;
        if (!param || !param.time || !param.seriesData) return;
        
        const data = param.seriesData.get(this.candleSeries);
        if (data && data.open !== undefined) {
            const chg = ((data.close - data.open) / data.open) * 100;
            const isPos = chg >= 0;
            const chgColor = isPos ? '#34d399' : '#f87171';
            ohlcvEl.innerHTML = `O: <strong>$${data.open.toFixed(2)}</strong> H: <strong>$${data.high.toFixed(2)}</strong> L: <strong>$${data.low.toFixed(2)}</strong> C: <strong style="color:${chgColor};">$${data.close.toFixed(2)}</strong> (<strong style="color:${chgColor};">${isPos ? '+' : ''}${chg.toFixed(2)}%</strong>)`;
        }
    },

    async loadChart(ticker, livePriceHint = 0) {
        if (!ticker) return;
        this.currentTicker = ticker.toUpperCase();
        
        const curTickerEl = document.getElementById("curTicker");
        const curPriceEl = document.getElementById("curPrice");
        if (curTickerEl) curTickerEl.textContent = this.currentTicker;
        if (curPriceEl && livePriceHint > 0) {
            curPriceEl.textContent = `$${Number(livePriceHint).toFixed(2)}`;
        }

        if (!this.mainChart) {
            this.init();
        }

        try {
            const reqTicker = this.currentTicker;
            const chartData = await ApiClient.getChartData(reqTicker);
            if (this.currentTicker !== reqTicker) return;
            if (chartData && !chartData.aborted) {
                const latestPrice = (livePriceHint > 0) 
                    ? livePriceHint 
                    : (chartData.latest_close || (chartData.candles && chartData.candles.length > 0 ? chartData.candles[chartData.candles.length - 1].close : 0));
                
                this.renderData(chartData, null, latestPrice);
                
                if (curPriceEl && latestPrice > 0) {
                    curPriceEl.textContent = `$${Number(latestPrice).toFixed(2)}`;
                }

                // Update Decoder with live price
                const dashData = window.TerminalUI ? window.TerminalUI.latestDashboardData : null;
                QuantDecoder.update(this.currentTicker, chartData, dashData);
            } else if (!chartData || !chartData.aborted) {
                this.renderNotFound(this.currentTicker);
            }
        } catch (err) {
            if (this.currentTicker !== ticker.toUpperCase()) return;
            console.error(`[ChartEngine] Error loading chart for ${this.currentTicker}:`, err);
            this.renderNotFound(this.currentTicker);
        }
    },

    switchTimeframe(tf) {
        this.setTimeframe(tf);
    },

    setTimeframe(tf) {
        this.currentTimeframe = tf;
        const dailyBtn = document.getElementById("tfDaily");
        const weeklyBtn = document.getElementById("tfWeekly");
        const tfBadge = document.getElementById("curTfBadge");

        if (dailyBtn && weeklyBtn) {
            if (tf === 'weekly') {
                dailyBtn.classList.remove("active");
                weeklyBtn.classList.add("active");
                if (tfBadge) tfBadge.textContent = "1W WEEKLY";
            } else {
                weeklyBtn.classList.remove("active");
                dailyBtn.classList.add("active");
                if (tfBadge) tfBadge.textContent = "1D DAILY";
            }
        }

        if (this.currentLoadedChartObj) {
            this.renderData(this.currentLoadedChartObj, tf);
        }
    },

    toggleSeries(type, isVisible) {
        if (!this.mainChart) return;
        if (type === "kijun" && this.kijunSeries) this.kijunSeries.applyOptions({ visible: isVisible });
        else if (type === "tenkan" && this.tenkanSeries) this.tenkanSeries.applyOptions({ visible: isVisible });
        else if (type === "span") {
            if (this.spanASeries) this.spanASeries.applyOptions({ visible: isVisible });
            if (this.spanBSeries) this.spanBSeries.applyOptions({ visible: isVisible });
        } else if (type === "sma") {
            if (this.sma20Series) this.sma20Series.applyOptions({ visible: isVisible });
            if (this.sma60Series) this.sma60Series.applyOptions({ visible: isVisible });
        }
    },

    renderData(chartObj, tf = null, livePrice = 0) {
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

        const candles = tfData.candles ? [...tfData.candles] : [];
        const effectivePrice = livePrice > 0 ? livePrice : (chartObj.latest_close || 0);
        if (candles.length > 0 && effectivePrice > 0) {
            const lastIdx = candles.length - 1;
            const lastC = { ...candles[lastIdx] };
            lastC.close = Number(effectivePrice);
            lastC.high = Math.max(lastC.high, Number(effectivePrice));
            lastC.low = Math.min(lastC.low, Number(effectivePrice));
            candles[lastIdx] = lastC;
        }

        this.candleSeries.setData(candles);
        this.kijunSeries.setData(tfData.kijun_line || []);
        this.tenkanSeries.setData(tfData.tenkan_line || []);
        this.spanASeries.setData(tfData.span_a_line || []);
        this.spanBSeries.setData(tfData.span_b_line || []);
        this.sma20Series.setData(tfData.sma20 || []);
        this.sma60Series.setData(tfData.sma60 || []);
        const volData = (tfData && tfData.volume && tfData.volume.length > 0)
            ? tfData.volume
            : (chartObj.volume || []);
        this.volumeSeries.setData(volData);

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

    updateLiveTick(ticker, price) {
        if (!ticker || !price || this.currentTicker !== ticker.toUpperCase()) return;
        const p = Number(price);
        const curPriceEl = document.getElementById("curPrice");
        if (curPriceEl) curPriceEl.textContent = `$${p.toFixed(2)}`;

        if (this.currentLoadedChartObj) {
            this.currentLoadedChartObj.latest_close = p;
        }

        if (this.currentLoadedChartObj) {
            const tfData = (this.currentLoadedChartObj.timeframes && this.currentLoadedChartObj.timeframes[this.currentTimeframe]) || this.currentLoadedChartObj;
            const candles = tfData.candles || [];
            if (candles.length > 0 && this.candleSeries) {
                const last = candles[candles.length - 1];
                const updated = {
                    time: last.time,
                    open: last.open,
                    high: Math.max(last.high, p),
                    low: Math.min(last.low, p),
                    close: p
                };
                this.candleSeries.update(updated);

                if (this.volumeSeries && tfData.volume && tfData.volume.length > 0) {
                    const lastV = tfData.volume[tfData.volume.length - 1];
                    if (lastV) {
                        this.volumeSeries.update({
                            time: lastV.time,
                            value: lastV.value,
                            color: p >= (last.open || p) ? '#059669' : '#dc2626'
                        });
                    }
                }
            }
        }

        const dashData = window.TerminalUI ? window.TerminalUI.latestDashboardData : null;
        if (this.currentLoadedChartObj) {
            QuantDecoder.update(this.currentTicker, this.currentLoadedChartObj, dashData);
        }
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

        if (curPrice) curPrice.textContent = "N/A";
        if (qbPrice) qbPrice.textContent = "N/A";
        if (qbBuyPrice) qbBuyPrice.value = "";
        if (tgtEl) tgtEl.textContent = "-";
        if (stopEl) stopEl.textContent = "-";
    }
};
