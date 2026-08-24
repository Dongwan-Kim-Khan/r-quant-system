/**
 * R QUANT TERMINAL: QUANT DECODER MODULE
 * Updates the 5-Factor Institutional Matrix Decoder Cards, Verdict Banner, and Trading Directives.
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

        // Determine Tier type from live dashboard signals
        let tierType = 'TIER_2';
        if (dashboardData) {
            const isT1 = (dashboardData.dual_consensus || []).some(x => x.ticker === ticker);
            const isT3 = (dashboardData.strat2_exclusive || []).some(x => x.ticker === ticker);
            if (isT1) tierType = 'TIER_1';
            else if (isT3) tierType = 'TIER_3';
            else tierType = 'TIER_2';
        }

        if (title && score && banner) {
            if (tierType === 'TIER_1') {
                title.textContent = `Quant Rating: [Tier 1: 거시 순풍 + 스마트머니 매집 (최우선 주도주)]`;
                score.textContent = (info && info.score) ? info.score : "100 / 100 pt (TIER_1_LEADER)";
                score.style.color = "#fbbf24";
                banner.style.borderLeftColor = "#fbbf24";
            } else if (tierType === 'TIER_3') {
                title.textContent = `Quant Rating: [Tier 3: 구름대 도약 스나이퍼 (바닥 변곡 반등)]`;
                score.textContent = (info && info.score) ? info.score : "100 / 100 pt (TIER_3_SNIPER)";
                score.style.color = "#f87171";
                banner.style.borderLeftColor = "#f87171";
            } else {
                title.textContent = `Quant Rating: [Tier 2: 구조적 26일선 눌림목 (추세 순응 분할 매수)]`;
                score.textContent = (info && info.score) ? info.score : "95 / 100 pt (TIER_2_PULLBACK)";
                score.style.color = "#10b981";
                banner.style.borderLeftColor = "#10b981";
            }
        }

        if (!liveChartData) return;

        // 1. Future Cloud Decoder (+26D Forward Projection)
        if (liveChartData.future_cloud_type) {
            const isBull = liveChartData.future_cloud_type.includes("양운");
            const fcBadge = document.getElementById("sigFutureCloudBadge");
            const fcVal = document.getElementById("sigFutureCloudVal");
            const fcDesc = document.getElementById("sigFutureCloudDesc");

            if (fcBadge) {
                fcBadge.textContent = liveChartData.future_cloud_type;
                fcBadge.className = `status-badge ${isBull ? 'status-good' : 'status-bad'}`;
            }
            if (fcVal) {
                fcVal.textContent = `+26일 선행스팬1 $${liveChartData.future_span_a_latest} | 선행스팬2 $${liveChartData.future_span_b_latest} (구름 두께 $${liveChartData.future_cloud_gap})`;
            }
            if (fcDesc) {
                fcDesc.textContent = isBull 
                    ? `향후 26거래일 미래 선행스팬1이 선행스팬2 위에 위치(양운)하여 하방 지지 매물대를 형성 중입니다.`
                    : `향후 26거래일 미래 선행스팬2가 선행스팬1 위에 위치(음운)하여 상방 저항 매물대를 형성 중입니다.`;
            }
        }

        // 2. Kijun-sen (26-Day Support)
        if (liveChartData.kijun) {
            const kGap = liveChartData.kijun_gap_pct !== undefined ? liveChartData.kijun_gap_pct : (((liveChartData.latest_close - liveChartData.kijun)/liveChartData.kijun)*100).toFixed(1);
            const kGood = kGap >= -0.5;
            const bEl = document.getElementById("sigKijunBadge");
            const vEl = document.getElementById("sigKijunVal");
            const dEl = document.getElementById("sigKijunDesc");

            if (bEl) {
                bEl.className = `status-badge ${kGood ? 'status-good' : 'status-bad'}`;
                bEl.textContent = kGood ? 'SUPPORT INTACT' : 'BREAKDOWN';
            }
            if (vEl) vEl.textContent = `$${liveChartData.kijun} (${kGap >= 0 ? '+' : ''}${kGap}% 이격)`;
            if (dEl) dEl.textContent = kGood ? '주가가 26일 기준선(생명선) 위에 위치하여 중기 지지선이 유효합니다.' : '주가가 26일 기준선(생명선)을 이탈하여 추세가 약화되었습니다.';
        }

        // 3. Tenkan-sen (9-Day Golden Cross)
        if (liveChartData.tenkan) {
            const tGood = liveChartData.tenkan >= liveChartData.kijun;
            const bEl = document.getElementById("sigTenkanBadge");
            const vEl = document.getElementById("sigTenkanVal");
            const dEl = document.getElementById("sigTenkanDesc");

            if (bEl) {
                bEl.className = `status-badge ${tGood ? 'status-good' : 'status-bad'}`;
                bEl.textContent = tGood ? 'BULLISH CROSS' : 'BEARISH';
            }
            if (vEl) vEl.textContent = `$${liveChartData.tenkan} (${tGood ? '9일선 > 26일선 골든' : '9일선 < 26일선 데드'})`;
            if (dEl) dEl.textContent = tGood ? '단기 9일 전환선이 26일 기준선 위에 위치하여 단기 모멘텀이 유지됩니다.' : '단기 9일 전환선이 하락 압력을 나타내고 있습니다.';
        }

        // 4. Volume Dry-up Ratio
        if (liveChartData.vol_ratio !== undefined) {
            const vPct = (liveChartData.vol_ratio * 100).toFixed(0);
            const vDry = liveChartData.vol_ratio <= 0.75;
            const bEl = document.getElementById("sigVolBadge");
            const vEl = document.getElementById("sigVolVal");
            const dEl = document.getElementById("sigVolDesc");

            if (bEl) {
                bEl.className = `status-badge ${vDry ? 'status-good' : 'status-neutral'}`;
                bEl.textContent = vDry ? 'OPTIMAL DRY-UP' : 'NORMAL VOLUME';
            }
            if (vEl) vEl.textContent = `20일 평균 대비 ${vPct}% (거래량 비율)`;
            if (dEl) dEl.textContent = vDry ? '눌림목에서 거래량이 75% 이하로 건조되어 매도 압력 소진 및 반등 타점 형성.' : '거래량이 평균 수준을 유지하고 있습니다.';
        }

        // 5. Cloud Status and Directive
        if (liveChartData.status_text) {
            const cVal = document.getElementById("sigCloudVal");
            const actEl = document.getElementById("actionDirectiveText");
            if (cVal) cVal.textContent = liveChartData.status_text;
            if (actEl && liveChartData.latest_close) {
                actEl.textContent = liveChartData.status_text + ` (목표가: $${(liveChartData.latest_close * 1.15).toFixed(2)} / 손절선: $${(liveChartData.latest_close * 0.97).toFixed(2)})`;
            }
        }
    }
};
