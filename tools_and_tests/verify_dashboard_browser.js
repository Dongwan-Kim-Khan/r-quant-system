const { chromium } = require('d:/코딩/R/.agents/skills/playwright-skill/node_modules/playwright');

(async () => {
    console.log("[TEST] Launching headless browser...");
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();

    const consoleErrors = [];
    page.on('console', msg => {
        if (msg.type() === 'error') {
            consoleErrors.push(msg.text());
        }
    });

    page.on('pageerror', err => {
        consoleErrors.push(err.message);
    });

    console.log("[TEST] Navigating to http://127.0.0.1:8000/ ...");
    await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });

    // Wait for hydration and data population
    await page.waitForTimeout(2000);

    // 1. Verify unified PORTFOLIO table (no cloned slot-card holdings)
    const portTickers = await page.$$eval('#portfolioTableBody tr td strong.ticker-pill', els => els.map(e => e.textContent.trim()));
    console.log("[TEST] Portfolio table tickers:", portTickers);

    const slotCards = await page.$$eval('#slotVisualizerGrid .slot-card', els => els.length);
    const slotHidden = await page.$eval('#slotVisualizerGrid', el => el.hasAttribute('hidden'));
    console.log("[TEST] Slot card clones:", slotCards, "hidden:", slotHidden);
    if (!slotHidden || slotCards !== 0) {
        console.error("[FAIL] Slot occupancy cards are still a second holdings view");
        process.exit(1);
    }

    const slotSummaryText = await page.$eval('#slotSummaryText', el => el.textContent.trim());
    console.log("[TEST] Slot summary text:", slotSummaryText);
    if (!/^\d+ \/ [23] SLOTS/.test(slotSummaryText)) {
        console.error("[FAIL] Slot summary missing satellite slot counter:", slotSummaryText);
        process.exit(1);
    }

    const msiText = await page.$eval('#kpiRegimeBadge', el => el.textContent.trim());
    console.log("[TEST] MSI KPI badge:", msiText);
    if (!/MSI:\s*\d+(\.\d+)?\s*PT/.test(msiText)) {
        console.error("[FAIL] Regime KPI must show MSI score, got:", msiText);
        process.exit(1);
    }

    const rsTitles = await page.$$eval('.indicator-name', els => els.map(e => e.textContent.trim()));
    console.log("[TEST] Decoder indicator titles:", rsTitles);
    if (!rsTitles.includes('4. COMPOSITE RS')) {
        console.error("[FAIL] Decoder card 4 title must be 4. COMPOSITE RS, got:", rsTitles);
        process.exit(1);
    }

    const weights = await page.$$eval('#portfolioTableBody td.holding-weight', els => els.map(e => e.textContent.trim()));
    console.log("[TEST] Holding weights:", weights);
    for (const w of weights) {
        const pct = Number(String(w).replace('%', ''));
        if (!(pct >= 1)) {
            console.error(`[FAIL] holding weight collapsed under inflated NAV: ${weights}`);
            process.exit(1);
        }
    }
    const equityText = await page.$eval('#kpiTotalEquityUsd', el => el.textContent.trim());
    const equityUsd = Number(String(equityText).replace(/[^0-9.]/g, ''));
    console.log("[TEST] Equity:", equityText);
    if (!(equityUsd > 0) || equityUsd > 50000) {
        console.error("[FAIL] TOTAL EQUITY looks like poisoned KIS output2, got:", equityText);
        process.exit(1);
    }
    console.log("[PASS] Live holdings occupy a single PORTFOLIO table with sane weights!");

    // 2. Verify Guardian badge styling and click behavior
    const gBadge = await page.$('#guardianBadge');
    let gText = await gBadge.textContent();
    let gClass = await gBadge.getAttribute('class');
    console.log(`[TEST] Initial Guardian Badge: class="${gClass}", text="${gText}"`);

    if (!gClass.includes('status-pill-chip')) {
        console.error("[FAIL] Guardian badge missing status-pill-chip class!");
        process.exit(1);
    }

    console.log("[TEST] Clicking Guardian badge...");
    await gBadge.click();
    await page.waitForTimeout(600);

    let gClassAfter1 = await gBadge.getAttribute('class');
    let gTextAfter1 = await gBadge.textContent();
    console.log(`[TEST] After 1st click: class="${gClassAfter1}", text="${gTextAfter1}"`);

    if (!gClassAfter1.includes('status-pill-chip')) {
        console.error("[FAIL] Guardian badge lost status-pill-chip class after click!");
        process.exit(1);
    }

    console.log("[TEST] Clicking Guardian badge again to restore...");
    await gBadge.click();
    await page.waitForTimeout(600);

    let gClassAfter2 = await gBadge.getAttribute('class');
    let gTextAfter2 = await gBadge.textContent();
    console.log(`[TEST] After 2nd click: class="${gClassAfter2}", text="${gTextAfter2}"`);

    // 3. Verify Autopilot badge styling and click behavior
    const aBadge = await page.$('#autopilotBadge');
    let aText = await aBadge.textContent();
    let aClass = await aBadge.getAttribute('class');
    console.log(`[TEST] Initial Autopilot Badge: class="${aClass}", text="${aText}"`);

    if (!aClass.includes('status-pill-chip')) {
        console.error("[FAIL] Autopilot badge missing status-pill-chip class!");
        process.exit(1);
    }

    console.log("[TEST] Clicking Autopilot badge...");
    await aBadge.click();
    await page.waitForTimeout(600);

    let aClassAfter1 = await aBadge.getAttribute('class');
    let aTextAfter1 = await aBadge.textContent();
    console.log(`[TEST] After 1st click: class="${aClassAfter1}", text="${aTextAfter1}"`);

    if (!aClassAfter1.includes('status-pill-chip')) {
        console.error("[FAIL] Autopilot badge lost status-pill-chip class after click!");
        process.exit(1);
    }

    console.log("[TEST] Clicking Autopilot badge again to restore...");
    await aBadge.click();
    await page.waitForTimeout(600);

    let aClassAfter2 = await aBadge.getAttribute('class');
    let aTextAfter2 = await aBadge.textContent();
    console.log(`[TEST] After 2nd click: class="${aClassAfter2}", text="${aTextAfter2}"`);

    // 4. Attack Untested Edge Case: Rapid double-click (< 50ms interval) to verify debounce/locking
    console.log("[TEST] Testing rapid double-click on Guardian badge (< 50ms)...");
    await Promise.all([gBadge.click(), gBadge.click()]);
    await page.waitForTimeout(800);
    let gRapidClass = await gBadge.getAttribute('class');
    let gRapidText = await gBadge.textContent();
    console.log(`[TEST] After rapid double-click: class="${gRapidClass}", text="${gRapidText}"`);
    if (!gRapidClass.includes('status-pill-chip')) {
        console.error("[FAIL] Guardian badge corrupted after rapid double-click!");
        process.exit(1);
    }
    // Restore to active if toggled off
    if (gRapidClass.includes('disabled')) {
        await gBadge.click();
        await page.waitForTimeout(600);
    }

    console.log("[TEST] Testing rapid double-click on Autopilot badge (< 50ms)...");
    await Promise.all([aBadge.click(), aBadge.click()]);
    await page.waitForTimeout(800);
    let aRapidClass = await aBadge.getAttribute('class');
    let aRapidText = await aBadge.textContent();
    console.log(`[TEST] After rapid double-click: class="${aRapidClass}", text="${aRapidText}"`);
    if (!aRapidClass.includes('status-pill-chip')) {
        console.error("[FAIL] Autopilot badge corrupted after rapid double-click!");
        process.exit(1);
    }
    if (aRapidClass.includes('disabled')) {
        await aBadge.click();
        await page.waitForTimeout(600);
    }

    // 5. Check console errors
    console.log("[TEST] Console errors detected:", consoleErrors);
    if (consoleErrors.some(e => !e.includes('favicon') && !e.includes('WebSocket') && !e.includes('font'))) {
        console.warn("[WARN] JavaScript errors on page:", consoleErrors);
    }

    // 5. Take screenshot
    await page.screenshot({ path: 'd:/코딩/R/tools_and_tests/dashboard_verification.png', fullPage: true });
    console.log("[PASS] Screenshot saved to tools_and_tests/dashboard_verification.png");

    await browser.close();
    console.log("[ALL TESTS PASSED SUCCESSFULLY]");
})();
