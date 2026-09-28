const { chromium } = require('d:/코딩/R/.agents/skills/playwright-skill/node_modules/playwright');

(async () => {
    console.log('[EDGE TEST] Launching headless browser for Slot Allocator Edge Cases...');
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();

    await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    // Test Scenario 1: 4 Holdings (3 sats + 1 proxy) in Bull regime
    console.log('[EDGE TEST] Evaluating 4 holdings scenario (DELL, CVX, NVDA, QLD)...');
    const fourHoldings = {
        total_equity_usd: 10000,
        holdings: [
            { ticker: 'DELL', current_price: 540, buy_price: 530, quantity: 5, pnl_pct: 1.89, id: 1 },
            { ticker: 'CVX', current_price: 215, buy_price: 200, quantity: 10, pnl_pct: 7.5, id: 2 },
            { ticker: 'NVDA', current_price: 130, buy_price: 125, quantity: 15, pnl_pct: 4.0, id: 3 },
            { ticker: 'QLD', current_price: 90, buy_price: 90, quantity: 20, pnl_pct: 0.0, id: 4 }
        ]
    };
    await page.evaluate((port) => {
        window.TerminalUI.renderPortfolio(port);
        window.TerminalUI.renderSlotVisualizer(port, true, null, null);
    }, fourHoldings);
    await page.waitForTimeout(200);

    const tableTickers4 = await page.$$eval('#portfolioTableBody tr td strong.ticker-pill', els => els.map(e => e.textContent.trim()));
    const slotCards4 = await page.$$eval('#slotVisualizerGrid .slot-card', els => els.length);
    console.log('[EDGE TEST] 4 Holdings table:', tableTickers4, 'slot cards:', slotCards4);
    if (tableTickers4.length !== 4 || !tableTickers4.includes('QLD') || !tableTickers4.includes('NVDA')) {
        console.error('[FAIL] 4th holding was dropped!', tableTickers4);
        process.exit(1);
    }
    if (slotCards4 !== 0) {
        console.error('[FAIL] Slot cards must not duplicate the holdings table, got', slotCards4);
        process.exit(1);
    }
    console.log('[PASS] All 4 holdings live in the table only (QLD included, no slot-card clone)!');

    const summary4 = await page.$eval('#slotSummaryText', el => el.textContent.trim());
    console.log('[EDGE TEST] 4 Holdings slot summary:', summary4);
    if (!summary4.startsWith('3 / 3 SLOTS')) {
        console.error('[FAIL] QQQ/QLD must not inflate satellite slot count. Expected 3 / 3 SLOTS, got:', summary4);
        process.exit(1);
    }
    console.log('[PASS] Slot counter uses satellite count only (3 / 3), cash proxy excluded!');

    // Test Scenario 1b: 2 sats + QQQ must read 2/3, not 3/3, and KPI must show 1 SLOT READY
    console.log('[EDGE TEST] Evaluating 2 satellites + QQQ (must not fill 3rd slot)...');
    const twoSatsPlusQqq = {
        total_equity_usd: 10000,
        free_cash_usd: 2000,
        cash_ratio_pct: 20,
        holdings: [
            { ticker: 'NVDA', current_price: 130, buy_price: 125, quantity: 15, pnl_pct: 4.0, id: 11 },
            { ticker: 'AMZN', current_price: 180, buy_price: 170, quantity: 10, pnl_pct: 5.9, id: 12 },
            { ticker: 'QQQ', current_price: 480, buy_price: 480, quantity: 4, pnl_pct: 0.0, id: 13 }
        ]
    };
    await page.evaluate((port) => {
        window.TerminalUI.latestPortfolioData = port;
        window.TerminalUI.renderPortfolio(port);
        window.TerminalUI.renderSlotVisualizer(port, true, null, null);
        window.TerminalUI.renderKPIs(null, port, true);
    }, twoSatsPlusQqq);
    await page.waitForTimeout(200);

    const summary2p = await page.$eval('#slotSummaryText', el => el.textContent.trim());
    const kpiSlots = await page.$eval('#kpiCashRatioText', el => el.textContent.trim());
    console.log('[EDGE TEST] 2 sats + QQQ summary:', summary2p, '| KPI:', kpiSlots);
    if (!summary2p.startsWith('2 / 3 SLOTS')) {
        console.error('[FAIL] Expected 2 / 3 SLOTS with QQQ excluded from count, got:', summary2p);
        process.exit(1);
    }
    if (!kpiSlots.includes('1 SLOT READY') && !kpiSlots.includes('1 SLOTS READY')) {
        console.error('[FAIL] KPI available slots should ignore QQQ. Expected 1 SLOT READY, got:', kpiSlots);
        process.exit(1);
    }
    const table2p = await page.$$eval('#portfolioTableBody tr', els => els.map(e => ({
        ticker: (e.querySelector('strong.ticker-pill') || {}).textContent || '',
        proxy: e.textContent.includes('PROXY'),
    })));
    if (!table2p.some(c => c.ticker.trim() === 'QQQ' && c.proxy)) {
        console.error('[FAIL] QQQ cash proxy missing from holdings table!', table2p);
        process.exit(1);
    }
    console.log('[PASS] 2 sats + QQQ → 2 / 3 SLOTS and 1 SLOT READY; QQQ marked PROXY in the table!');

    // Test Scenario 2: Bear Regime with 3 holdings (2 sats + 1 proxy)
    console.log('[EDGE TEST] Evaluating Bear Regime scenario (25/25 slots + QQQ)...');
    const bearHoldings = {
        total_equity_usd: 8000,
        holdings: [
            { ticker: 'DELL', current_price: 540, buy_price: 530, quantity: 3, pnl_pct: 1.89, id: 21 },
            { ticker: 'CVX', current_price: 215, buy_price: 200, quantity: 5, pnl_pct: 7.5, id: 22 },
            { ticker: 'QQQ', current_price: 480, buy_price: 480, quantity: 8, pnl_pct: 0.0, id: 23 }
        ]
    };
    await page.evaluate((port) => {
        window.TerminalUI.renderPortfolio(port);
        window.TerminalUI.renderSlotVisualizer(port, false, null, null);
    }, bearHoldings);
    await page.waitForTimeout(200);

    const tableBear = await page.$$eval('#portfolioTableBody tr td strong.ticker-pill', els => els.map(e => e.textContent.trim()));
    console.log('[EDGE TEST] Bear Regime table:', tableBear);
    if (!tableBear.includes('QQQ')) {
        console.error('[FAIL] QQQ Cash Proxy was dropped in Bear Regime!', tableBear);
        process.exit(1);
    }
    console.log('[PASS] QQQ Cash Proxy correctly rendered in Bear Regime table!');

    const summaryBear = await page.$eval('#slotSummaryText', el => el.textContent.trim());
    console.log('[EDGE TEST] Bear Regime slot summary:', summaryBear);
    if (!summaryBear.startsWith('2 / 2 SLOTS')) {
        console.error('[FAIL] Bear satellite count should be 2 / 2 (QQQ excluded). Got:', summaryBear);
        process.exit(1);
    }
    console.log('[PASS] Bear slot counter is 2 / 2 (QQQ excluded from satellite count)!');

    // Test Scenario 3: Empty portfolio (0 holdings)
    console.log('[EDGE TEST] Evaluating Empty Portfolio (0 holdings)...');
    const emptyPort = { total_equity_usd: 7500, holdings: [] };
    await page.evaluate((port) => {
        window.TerminalUI.renderPortfolio(port);
        window.TerminalUI.renderSlotVisualizer(port, true, null, null);
    }, emptyPort);
    await page.waitForTimeout(200);

    const emptySlots = await page.$$eval('#slotVisualizerGrid .slot-card', els => els.length);
    const emptyTable = await page.$eval('#portfolioTableBody', el => el.textContent);
    console.log('[EDGE TEST] Empty slot cards:', emptySlots);
    if (emptySlots !== 0) {
        console.error('[FAIL] Empty portfolio must not spawn slot-card clones, got ' + emptySlots);
        process.exit(1);
    }
    if (!/No active holdings/i.test(emptyTable)) {
        console.error('[FAIL] Empty portfolio table should say no holdings, got:', emptyTable);
        process.exit(1);
    }
    console.log('[PASS] Empty portfolio is one table empty-state, no slot-card clones!');

    const summaryEmpty = await page.$eval('#slotSummaryText', el => el.textContent.trim());
    if (!summaryEmpty.startsWith('0 / 3 SLOTS')) {
        console.error('[FAIL] Empty portfolio should read 0 / 3 SLOTS, got:', summaryEmpty);
        process.exit(1);
    }

    // Test Scenario 4: Quick Buy QTY auto-fill (Top Pick sizing.shares vs next-slot NAV)
    console.log('[EDGE TEST] Evaluating Quick Buy QTY auto-fill...');
    await page.evaluate(() => {
        window.TerminalUI.latestDashboardData = {
            top_conviction_pick: { ticker: 'NVDA', sizing: { shares: 42 } },
            top_conviction_runner_up: { ticker: 'AMD', sizing: { shares: 17 } },
            slot_allocation_summary: { is_bull_regime: true },
            portfolio: { total_equity_usd: 10000, holdings: [], free_cash_usd: 10000 }
        };
        window.TerminalUI.latestPortfolioData = { total_equity_usd: 10000, holdings: [], free_cash_usd: 10000 };
        window.TerminalUI.updateQuickBuyConsole('NVDA', 100);
    });
    const qtyPick = await page.$eval('#qbQty', el => el.value);
    console.log('[EDGE TEST] Top Pick NVDA qbQty:', qtyPick);
    if (qtyPick !== '42') {
        console.error('[FAIL] Top Pick should auto-fill sizing.shares=42, got:', qtyPick);
        process.exit(1);
    }

    await page.evaluate(() => {
        window.TerminalUI.updateQuickBuyConsole('AAPL', 200);
    });
    const qtyGen = await page.$eval('#qbQty', el => el.value);
    console.log('[EDGE TEST] General AAPL qbQty (slot1 50% of $10k / $200):', qtyGen);
    if (qtyGen !== '25') {
        console.error('[FAIL] General ticker should fill floor(slotCap/price)=25, got:', qtyGen);
        process.exit(1);
    }
    console.log('[PASS] Quick Buy QTY auto-fills Top Pick shares and next-slot capacity!');

    await browser.close();
    console.log('[ALL EDGE CASE TESTS PASSED SUCCESSFULLY]');
})();
