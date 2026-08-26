/**
 * R QUANT TERMINAL v2: QUANT DECODER MODULE
 * Professional Bloomberg Dark Terminal Decoder for Al-Sangmoo v2 Quant Framework.
 * Pure 3-Gate + Dual Momentum + 20D Breakout Institutional Scoring (Zero Emojis).
 */

export const QuantDecoder = {
    update(ticker, liveChartData, dashboardData) {
        const banner = document.getElementById("verdictBanner");
        const score = document.getElementById("verdictScore");
        const title = document.getElementById("verdictTitle");

        let info = null;
        if (liveChartData && liveChartData.intelligence) {
            info = liveChartData.intelligence;
        } else if (dashboardData && dashboardData.chart_intelligence && dashboardData.chart_intelligence[ticker]) {
            info = dashboardData.chart_intelligence[ticker].intelligence || {};
        }

        const topPickTicker = (dashboardData && dashboardData.top_conviction_pick && dashboardData.top_conviction_pick.ticker) || '';
        const runnerUpTicker = (dashboardData && dashboardData.top_conviction_runner_up && dashboardData.top_conviction_runner_up.ticker) || '';
        const rankedList = (dashboardData && dashboardData.ranked_conviction_list) || [];
        const currentItem = rankedList.find(x => x.ticker === ticker) || (dashboardData && dashboardData.top_conviction_pick && dashboardData.top_conviction_pick.ticker === ticker ? dashboardData.top_conviction_pick : null);

        const isTopPick = (ticker === topPickTicker);
        const isRunnerUp = (ticker === runnerUpTicker);
        const isBreakout = Boolean(currentItem && currentItem.is_breakout);
        const isKijunSupport = Boolean(currentItem && currentItem.is_kijun_pullback);
        const convictionScore = currentItem ? Number(currentItem.conviction_score || 0) : (info && info.score ? Number(info.score) : 90);
        const rs3m = currentItem ? Number(currentItem.rs_3m || currentItem.momentum_3m || 0) : 0;

        // 1. Update Institutional Verdict Banner (Revolut Clean Style)
        if (title && score && banner) {
            if (isTopPick) {
                title.textContent = `QUANT VERDICT: [1위 TOP PICK]`;
                score.textContent = `${convictionScore.toFixed(1)} PT (최우선 진입)`;
                score.style.color = "var(--primary-bright)";
                banner.style.borderLeft = "4px solid var(--primary-bright)";
            } else if (isRunnerUp) {
                title.textContent = `QUANT VERDICT: [2위 RUNNER UP]`;
                score.textContent = `${convictionScore.toFixed(1)} PT (강력 추천)`;
                score.style.color = "var(--accent-green)";
                banner.style.borderLeft = "4px solid var(--accent-green)";
            } else if (isBreakout) {
                title.textContent = `QUANT VERDICT: [20일 신고가 돌파]`;
                score.textContent = `${convictionScore.toFixed(1)} PT (돌파 매수)`;
                score.style.color = "var(--accent-yellow)";
                banner.style.borderLeft = "4px solid var(--accent-yellow)";
            } else if (isKijunSupport) {
                title.textContent = `QUANT VERDICT: [26일 기준선 지지]`;
                score.textContent = `${convictionScore.toFixed(1)} PT (눌림목 매수)`;
                score.style.color = "var(--accent-green)";
                banner.style.borderLeft = "4px solid var(--accent-green)";
            } else {
                title.textContent = `QUANT VERDICT: [관망 / 중립 국면]`;
                score.textContent = `${convictionScore.toFixed(1)} PT`;
                score.style.color = "var(--text-muted)";
                banner.style.borderLeft = "4px solid var(--hairline-strong)";
            }
        }

        if (!liveChartData) return;

        // 2. Card 1: 26D Kijun Support (생명선 지지)
        if (liveChartData.kijun) {
            const kGap = liveChartData.kijun_gap_pct !== undefined 
                ? Number(liveChartData.kijun_gap_pct) 
                : Number(((liveChartData.latest_close - liveChartData.kijun) / liveChartData.kijun) * 100);
            const isGood = kGap >= -1.0;
            const bEl = document.getElementById("sigKijunBadge");
            const vEl = document.getElementById("sigKijunVal");

            if (bEl) {
                bEl.className = `status-badge ${isGood ? 'status-good' : 'status-bad'}`;
                bEl.textContent = isGood ? '[SUPPORT INTACT]' : '[BREAKDOWN]';
            }
            if (vEl) {
                vEl.textContent = `$${Number(liveChartData.kijun).toFixed(2)} (${kGap >= 0 ? '+' : ''}${kGap.toFixed(1)}% GAP)`;
            }
        }

        // 3. Card 2: 9D Tenkan Line (단기 전환선)
        if (liveChartData.tenkan) {
            const isBullCross = Number(liveChartData.tenkan) >= Number(liveChartData.kijun || 0);
            const bEl = document.getElementById("sigTenkanBadge");
            const vEl = document.getElementById("sigTenkanVal");

            if (bEl) {
                bEl.className = `status-badge ${isBullCross ? 'status-good' : 'status-bad'}`;
                bEl.textContent = isBullCross ? '[BULLISH CROSS]' : '[BEARISH CROSS]';
            }
            if (vEl) {
                vEl.textContent = `$${Number(liveChartData.tenkan).toFixed(2)} (${isBullCross ? '9D >= 26D' : '9D < 26D'})`;
            }
        }

        // 4. Card 3: Ichimoku Cloud (일목 구름대 지지)
        if (liveChartData.future_cloud_type || liveChartData.status_text) {
            const isCloudAbove = !String(liveChartData.status_text || '').includes('하회');
            const bEl = document.getElementById("sigCloudBadge");
            const vEl = document.getElementById("sigCloudVal");

            if (bEl) {
                bEl.className = `status-badge ${isCloudAbove ? 'status-good' : 'status-bad'}`;
                bEl.textContent = isCloudAbove ? '[CLOUD SUPPORT]' : '[CLOUD RESISTANCE]';
            }
            if (vEl) {
                vEl.textContent = isCloudAbove ? 'ABOVE CLOUD (BULLISH)' : 'BELOW CLOUD (BEARISH)';
            }
        }

        // 5. Card 4: 3M RS Momentum & 20D Breakout (v2 핵심)
        const rsBadge = document.getElementById("sigRsBadge") || document.getElementById("sigFutureCloudBadge");
        const rsVal = document.getElementById("sigRsVal") || document.getElementById("sigFutureCloudVal");

        if (rsBadge) {
            const isRsStrong = rs3m >= 0;
            rsBadge.className = `status-badge ${isRsStrong ? 'status-good' : 'status-bad'}`;
            rsBadge.textContent = isBreakout ? '[20D BREAKOUT]' : (isRsStrong ? '[RS OUTPERFORM]' : '[RS UNDERPERFORM]');
        }
        if (rsVal) {
            rsVal.textContent = `3M RS: ${rs3m >= 0 ? '+' : ''}${rs3m.toFixed(1)}% ${isBreakout ? '| 20D HIGH' : '| IN RANGE'}`;
        }
    }
};
