/**
 * verify_slot_allocator_edge_cases.js
 *
 * Drives the live frontend's 3-slot allocator (window.TerminalUI.renderSlotVisualizer)
 * through the regime x holdings edge-case matrix and asserts the rendered
 * occupied / empty / locked slot counts and summary text. Also fails on any
 * console/page error emitted while rendering.
 *
 * Usage:  node tools_and_tests/verify_slot_allocator_edge_cases.js
 * Env:    TARGET_URL (default http://127.0.0.1:8000)
 */
"use strict";

const path = require("node:path");

function loadChromium() {
  const candidates = [
    path.join(__dirname, "..", "node_modules", "playwright"),
    path.join(__dirname, "..", ".agents", "skills", "playwright-skill", "node_modules", "playwright"),
    "playwright",
  ];
  for (const c of candidates) {
    try {
      return require(c).chromium;
    } catch (_) { /* try next */ }
  }
  throw new Error("Playwright is not installed. Run: (cd .agents/skills/playwright-skill && npm run setup)");
}

const TARGET_URL = process.env.TARGET_URL || "http://127.0.0.1:8000";

function mkHoldings(tickers) {
  return tickers.map((tk, i) => ({
    ticker: tk,
    pnl_pct: 1.5,
    current_price: 100 + i,
    buy_price: 95 + i,
    quantity: 2,
    current_value: (100 + i) * 2,
    stop_loss_price: (95 + i) * 0.96,
  }));
}

// [name, isBullRegime, holdingTickers, expectOccupied, expectEmpty, expectLocked, expectSummary]
const CASES = [
  ["bull / 0 held", true, [], 0, 3, 0, "0 / 3 SLOTS OCCUPIED"],
  ["bull / 2 held", true, ["AAPL", "MSFT"], 2, 1, 0, "2 / 3 SLOTS OCCUPIED"],
  ["bull / 3 held (full)", true, ["AAPL", "MSFT", "NVDA"], 3, 0, 0, "3 / 3 SLOTS OCCUPIED"],
  ["bear / 0 held", false, [], 0, 2, 1, "0 / 2 SLOTS OCCUPIED"],
  ["bear / 1 held", false, ["AAPL"], 1, 1, 1, "1 / 2 SLOTS OCCUPIED"],
  ["bear / 2 held (full)", false, ["AAPL", "MSFT"], 2, 0, 1, "2 / 2 SLOTS OCCUPIED"],
];

(async () => {
  const chromium = loadChromium();
  const browser = await chromium.launch({ headless: true });
  const jsErrors = [];
  let failures = 0;

  try {
    const page = await browser.newPage();
    page.on("console", (msg) => { if (msg.type() === "error") jsErrors.push(msg.text()); });
    page.on("pageerror", (err) => jsErrors.push(String(err)));

    const resp = await page.goto(TARGET_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (!resp || !resp.ok()) throw new Error(`Root document HTTP ${resp ? resp.status() : "none"}`);

    await page.waitForFunction(
      () => window.TerminalUI && typeof window.TerminalUI.renderSlotVisualizer === "function",
      { timeout: 15000 }
    );
    await page.locator("#slotVisualizerGrid").waitFor({ state: "attached", timeout: 15000 });

    for (const [name, isBull, tickers, expOcc, expEmpty, expLocked, expSummary] of CASES) {
      const holdings = mkHoldings(tickers);
      const result = await page.evaluate(({ holdings, isBull }) => {
        const portfolio = { holdings, total_equity_usd: 7500.0 };
        window.TerminalUI.renderSlotVisualizer(portfolio, isBull, null);
        const grid = document.getElementById("slotVisualizerGrid");
        const summary = document.getElementById("slotSummaryText");
        return {
          occupied: grid.querySelectorAll(".slot-card.occupied").length,
          empty: grid.querySelectorAll(".slot-card.empty").length,
          locked: grid.querySelectorAll(".slot-card.locked").length,
          total: grid.querySelectorAll(".slot-card").length,
          summary: summary ? summary.textContent.trim() : "",
        };
      }, { holdings, isBull });

      const ok =
        result.occupied === expOcc &&
        result.empty === expEmpty &&
        result.locked === expLocked &&
        result.total === 3 &&
        result.summary === expSummary;

      if (ok) {
        console.log(`[slot-edge] PASS  ${name}  -> occ=${result.occupied} empty=${result.empty} locked=${result.locked} | "${result.summary}"`);
      } else {
        failures++;
        console.error(`[slot-edge] FAIL  ${name}`);
        console.error(`   expected occ=${expOcc} empty=${expEmpty} locked=${expLocked} summary="${expSummary}"`);
        console.error(`   actual   occ=${result.occupied} empty=${result.empty} locked=${result.locked} total=${result.total} summary="${result.summary}"`);
      }
    }

    if (jsErrors.length) {
      failures++;
      console.error(`[slot-edge] FAIL: ${jsErrors.length} console/page error(s):`);
      jsErrors.forEach((e) => console.error("   - " + e));
    }

    if (failures === 0) {
      console.log(`[slot-edge] ALL ${CASES.length} EDGE CASES PASSED (0 console errors).`);
    } else {
      process.exitCode = 1;
    }
  } catch (err) {
    console.error("[slot-edge] FAIL:", err && err.message ? err.message : err);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
