/**
 * verify_dashboard_browser.js
 *
 * Headless browser smoke check for the Al-Sangmoo Quant terminal.
 * Loads the dashboard, records every console error / uncaught page error, and
 * fails (exit 1) if any are found. Confirms the core terminal shell rendered.
 *
 * Usage:  node tools_and_tests/verify_dashboard_browser.js
 * Env:    TARGET_URL (default http://127.0.0.1:8000)
 */
"use strict";

const path = require("node:path");
const fs = require("node:fs");

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
const ARTIFACT_DIR = process.env.PW_ARTIFACT_DIR || "/tmp";

(async () => {
  const chromium = loadChromium();
  const browser = await chromium.launch({ headless: true });
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];

  try {
    const page = await browser.newPage();

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => pageErrors.push(String(err)));
    page.on("requestfailed", (req) => {
      const url = req.url();
      // favicon is intentionally 204/no-content; ignore.
      if (!url.endsWith("/favicon.ico")) {
        failedRequests.push(`${url} :: ${req.failure() && req.failure().errorText}`);
      }
    });

    console.log(`[verify-dashboard] Navigating to ${TARGET_URL}`);
    const resp = await page.goto(TARGET_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (!resp || !resp.ok()) {
      throw new Error(`Root document returned HTTP ${resp ? resp.status() : "no response"}`);
    }

    // Core terminal shell must render.
    await page.getByText("R QUANT", { exact: false }).first().waitFor({ timeout: 15000 });
    await page.locator("#slotVisualizerGrid").waitFor({ state: "attached", timeout: 15000 });

    // Give the async dashboard/portfolio/chart fetches time to run so their
    // handlers get a chance to throw any console errors.
    await page.waitForTimeout(4000);

    const title = await page.title();
    const shotPath = path.join(ARTIFACT_DIR, "verify_dashboard_browser.png");
    await page.screenshot({ path: shotPath, fullPage: true });

    console.log(`[verify-dashboard] Title: ${title}`);
    console.log(`[verify-dashboard] Screenshot: ${shotPath}`);
    if (failedRequests.length) {
      console.log(`[verify-dashboard] WARN failed requests (${failedRequests.length}):`);
      failedRequests.forEach((f) => console.log("   - " + f));
    }

    const totalJsErrors = consoleErrors.length + pageErrors.length;
    if (totalJsErrors > 0) {
      console.error(`[verify-dashboard] FAIL: ${totalJsErrors} browser console/page error(s):`);
      consoleErrors.forEach((e) => console.error("   [console.error] " + e));
      pageErrors.forEach((e) => console.error("   [pageerror] " + e));
      process.exitCode = 1;
    } else {
      console.log("[verify-dashboard] PASS: 0 browser console errors.");
    }
  } catch (err) {
    console.error("[verify-dashboard] FAIL:", err && err.message ? err.message : err);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
