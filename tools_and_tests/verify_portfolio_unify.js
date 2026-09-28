const { chromium } = require('d:/코딩/R/.agents/skills/playwright-skill/node_modules/playwright');

(async () => {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('http://127.0.0.1:8000/?boot=portfolio-unify', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);

    const titles = await page.$$eval('.panel-header span, .panel-header > div > span, .card-title', els =>
        els.map((e) => e.textContent.trim()).filter(Boolean)
    );
    console.log('[TITLES]', titles);

    const hasActive = titles.some((t) => t.includes('ACTIVE PORTFOLIO'));
    const hasSlotTitle = titles.some((t) => t.includes('C-2 SLOT ALLOCATOR'));
    const hasPortfolio = await page.$('#portfolioPanel');
    const portfolioLabel = await page.$eval('#portfolioPanel .panel-header span', (el) => el.textContent.trim());

    if (hasActive) {
        console.error('[FAIL] ACTIVE PORTFOLIO still present');
        process.exit(1);
    }
    if (hasSlotTitle) {
        console.error('[FAIL] C-2 SLOT ALLOCATOR still present');
        process.exit(1);
    }
    if (!hasPortfolio || portfolioLabel !== 'PORTFOLIO') {
        console.error('[FAIL] Unified PORTFOLIO panel missing, got:', portfolioLabel);
        process.exit(1);
    }

    const inPanel = await page.evaluate(() => {
        const panel = document.getElementById('portfolioPanel');
        const ids = ['kpiTotalEquityUsd', 'kpiFreeCashUsd', 'kpiHoldingsEvalUsd', 'portfolioTableBody', 'btnSyncBroker'];
        return Object.fromEntries(ids.map((id) => {
            const el = document.getElementById(id);
            return [id, Boolean(el && panel.contains(el))];
        }));
    });
    console.log('[IN PANEL]', inPanel);
    for (const [id, ok] of Object.entries(inPanel)) {
        if (!ok) {
            console.error(`[FAIL] ${id} is not inside #portfolioPanel`);
            process.exit(1);
        }
    }

    const slotHidden = await page.evaluate(() => {
        const grid = document.getElementById('slotVisualizerGrid');
        const overlay = document.getElementById('c2OverlayBar');
        const slotCards = grid ? grid.querySelectorAll('.slot-card').length : -1;
        return {
            gridHidden: Boolean(grid && grid.hasAttribute('hidden')),
            overlayHidden: Boolean(overlay && overlay.hasAttribute('hidden')),
            slotCards,
        };
    });
    console.log('[HIDDEN DUPES]', slotHidden);
    if (!slotHidden.gridHidden || slotHidden.slotCards !== 0) {
        console.error('[FAIL] Slot occupancy cards still rendered as a second holdings view', slotHidden);
        process.exit(1);
    }

    const topKpiTitles = await page.$$eval('.kpi-container .kpi-title span', els => els.map((e) => e.textContent.trim()));
    console.log('[TOP KPI]', topKpiTitles);
    if (topKpiTitles.includes('TOTAL EQUITY') || topKpiTitles.includes('AVAILABLE CASH') || topKpiTitles.includes('HOLDINGS EVAL')) {
        console.error('[FAIL] Duplicate equity KPIs remain in top kpi-container');
        process.exit(1);
    }

    const equityText = await page.$eval('#kpiTotalEquityUsd', (el) => el.textContent.trim());
    const cashText = await page.$eval('#kpiFreeCashUsd', (el) => el.textContent.trim());
    const evalText = await page.$eval('#kpiHoldingsEvalUsd', (el) => el.textContent.trim());
    console.log('[VALUES]', { equityText, cashText, evalText });
    if (equityText === '---' || cashText === '---' || evalText === '---') {
        console.error('[FAIL] Portfolio summary still showing placeholders');
        process.exit(1);
    }
    const equityUsd = Number(String(equityText).replace(/[^0-9.]/g, ''));
    if (!(equityUsd > 0) || equityUsd > 50000) {
        console.error('[FAIL] TOTAL EQUITY looks like poisoned KIS output2, got:', equityText);
        process.exit(1);
    }

    const headers = await page.$$eval('#portfolioPanel thead th', els => els.map((e) => e.textContent.trim()));
    console.log('[TABLE HEADERS]', headers);
    if (!headers.includes('VALUE') || !headers.includes('WEIGHT')) {
        console.error('[FAIL] holdings table must include VALUE and WEIGHT columns');
        process.exit(1);
    }

    const tableTickers = await page.$$eval('#portfolioTableBody tr td strong.ticker-pill', els => els.map((e) => e.textContent.trim()));
    const weights = await page.$$eval('#portfolioTableBody td.holding-weight', els => els.map((e) => e.textContent.trim()));
    console.log('[TABLE]', tableTickers, weights);
    for (const w of weights) {
        const pct = Number(String(w).replace('%', ''));
        if (!(pct >= 1)) {
            console.error('[FAIL] holding weight collapsed to sub-1% of inflated NAV:', weights);
            process.exit(1);
        }
    }

    const staleFeed = {
        holdings: [{ ticker: 'FAKE', current_price: 1, buy_price: 1, quantity: 1, pnl_pct: 0, id: 999 }],
        total_equity_usd: 7500,
        free_cash_usd: 7500,
        total_eval: 0,
        equity_source: 'LOCAL',
    };
    const preserved = await page.evaluate((feed) => {
        const before = JSON.parse(JSON.stringify(window.TerminalUI.latestPortfolioData || {}));
        window.TerminalUI.renderDashboard({
            last_updated: 'STALE',
            portfolio: feed,
            slot_allocation_summary: { is_bull_regime: true },
            macro: {},
            universe_sectors: [],
            execution_logs: [],
        });
        const afterTickers = (window.TerminalUI.latestPortfolioData.holdings || [])
            .map((h) => String(h.ticker || '').toUpperCase());
        return {
            beforeTickers: (before.holdings || []).map((h) => String(h.ticker || '').toUpperCase()),
            afterTickers,
            keptLive: !afterTickers.includes('FAKE'),
        };
    }, staleFeed);
    console.log('[SSOT GUARD]', preserved);
    if (!preserved.keptLive) {
        console.error('[FAIL] live_feed-style dashboard render overwrote live portfolio with FAKE');
        process.exit(1);
    }

    await page.screenshot({ path: 'd:/코딩/R/tools_and_tests/portfolio_unify_verification.png', fullPage: true });
    if (errors.length) console.warn('[PAGE ERRORS]', errors);
    await browser.close();
    console.log('[PASS] Unified PORTFOLIO verified');
})();
