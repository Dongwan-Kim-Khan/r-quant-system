/** Al-Sangmoo v2 risk constitution. Keep in lockstep with al_sangmoo/core/constants.py */
export const STOP_LOSS_PCT = -0.04;
export const TAKE_PROFIT_PCT = 0.15;
export const STOP_LOSS_MULT = 1 + STOP_LOSS_PCT;
export const TAKE_PROFIT_MULT = 1 + TAKE_PROFIT_PCT;

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
