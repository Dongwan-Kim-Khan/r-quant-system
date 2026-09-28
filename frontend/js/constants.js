/** Al-Sangmoo C-2 risk constitution. Keep in lockstep with al_sangmoo/core/constants.py */
export const STOP_LOSS_PCT = -0.07;
export const EOD_STOP_LOSS_PCT = -0.07;
export const EMERGENCY_STOP_LOSS_PCT = -0.10;
export const TAKE_PROFIT_PCT = 0.18;
export const STOP_LOSS_MULT = 1 + STOP_LOSS_PCT;
export const TAKE_PROFIT_MULT = 1 + TAKE_PROFIT_PCT;
export const ATR_MULTIPLIER = 3.0;
export const SLOT_WEIGHTS_BULL = [0.34, 0.33, 0.33];
export const SLOT_WEIGHTS_BEAR = [0.25, 0.25];
export const CASH_PROXY_TICKERS = new Set(["QQQ", "QLD"]);
export const HARD_STOP_DISPLAY = "−7.0% (종가) / −10.0% (비상)";
export const TRAILING_ACTIVATE_DISPLAY = "+18.0%";

export function isCashProxyTicker(ticker) {
    return CASH_PROXY_TICKERS.has(String(ticker || "").trim().toUpperCase());
}

export function deriveStopPrice(entry) {
    return Number((Number(entry) * STOP_LOSS_MULT).toFixed(2));
}

export function deriveTargetPrice(entry) {
    return Number((Number(entry) * TAKE_PROFIT_MULT).toFixed(2));
}

export function isMarketTicker(ticker) {
    const t = String(ticker || "").trim().toUpperCase();
    if (!t || t.includes("_")) return false;
    return /^([A-Z]{1,5}|[0-9]{6})(\.(KS|KQ))?$/.test(t);
}

/** MSI 2.0 is a risk index: higher = more danger. Lockstep with domain/quant/macro.py */
export function classifyMsiStance(score) {
    const s = Number(score);
    if (s >= 75) return "CASH_EXIT";
    if (s >= 50) return "DEFENSE_HOLD";
    if (s >= 30) return "SELECTIVE_BUY";
    return "ACTIVE_BUY";
}

export function extractMsiScore(macro) {
    if (!macro || typeof macro !== "object") return 50.0;
    const climate = (macro.macro_climate && typeof macro.macro_climate === "object") ? macro.macro_climate : {};
    const candidates = [macro.msi_score, climate.msi_score, macro.msi, climate.msi];
    for (const raw of candidates) {
        if (raw === null || raw === undefined || raw === "") continue;
        const n = Number(raw);
        if (Number.isFinite(n)) return n;
    }
    return 50.0;
}

/** Top Pick / Runner-up use engine sizing.shares; otherwise next satellite slot NAV / price. */
export function resolveQuickBuyQty(ticker, price, dashboard, portfolio) {
    const p = Number(price || 0);
    if (!(p > 0)) return 1;
    const data = dashboard || {};
    const tk = String(ticker || "").toUpperCase();
    const pick = [data.top_conviction_pick, data.top_conviction_runner_up]
        .find((x) => x && String(x.ticker || "").toUpperCase() === tk);
    const sized = pick && pick.sizing ? Number(pick.sizing.shares) : 0;
    if (sized > 0) return Math.max(1, Math.floor(sized));
    const port = portfolio || data.portfolio || {};
    const isBull = (data.slot_allocation_summary && data.slot_allocation_summary.is_bull_regime !== undefined)
        ? Boolean(data.slot_allocation_summary.is_bull_regime) : true;
    const sats = (Array.isArray(port.holdings) ? port.holdings : [])
        .filter((h) => isMarketTicker(h && h.ticker) && !isCashProxyTicker(h.ticker));
    const equity = Number(port.total_equity_usd !== undefined ? port.total_equity_usd : (port.total_equity || 0));
    const weights = isBull ? SLOT_WEIGHTS_BULL : SLOT_WEIGHTS_BEAR;
    const cap = (weights[sats.length] !== undefined)
        ? equity * weights[sats.length]
        : Number(port.free_cash_usd || 0);
    return Math.max(1, Math.floor(cap / p));
}
