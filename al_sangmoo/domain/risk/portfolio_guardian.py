"""
AL-SANGMOO QUANT TERMINAL: PORTFOLIO GUARDIAN DAEMON
Background auto-execution of v2 exits:
  1. -4.0% hard stop (full exit)
  2. 26-day kijun close breakdown (full exit)
  3. Uncapped trailing after +15% peak gain: max(Kijun-26, peak - 2.5*ATR(14))
No 50% partial take-profit.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from al_sangmoo.core.market_time import is_regular_hours_for_ticker
from al_sangmoo.infrastructure.persistence import (
    get_connection,
    get_live_portfolio,
    record_portfolio_sell,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.api.hub import hub, EventType
from al_sangmoo.infrastructure.idempotent_order import is_broker_order_ack
from al_sangmoo.core.constants import derive_stop_price
from al_sangmoo.domain.risk.trailing_stop import (
    ATR_TRAIL_MULT,
    HARD_STOP_PCT,
    TRAILING_ACTIVATE_PCT,
    evaluate_guardian_exit,
    fetch_trailing_snapshot,
    format_holding_advice,
)

logger = logging.getLogger(__name__)


def is_market_open_for_orders(ticker_sym: str) -> bool:
    """
    Checks if regular financial market is currently OPEN to accept automated orders.
    - US Market: 09:30 - 16:00 America/New_York (Monday to Friday, DST-aware)
    - KR Market: 09:00 - 15:30 KST (Monday to Friday)
    """
    return is_regular_hours_for_ticker(ticker_sym)


class PortfolioGuardian:
    """
    Autonomous background guardian monitoring active positions.
    Enforces Al-Sangmoo v2 exits without user intervention:
      1. -4% hard stop or 26D kijun close breakdown: 100% exit.
      2. After +15% peak gain: uncapped trailing floor max(kijun, peak - 2.5*ATR).
    """

    def __init__(self, check_interval_seconds: int = 10):
        self.interval = check_interval_seconds
        self.is_running = False
        self.is_enabled = True
        self._task: Optional[asyncio.Task] = None
        self.last_check_time: Optional[str] = None
        self.last_sync_time: float = 0.0
        self.last_actions: List[Dict[str, Any]] = []

    def start(self):
        """Starts the guardian background loop."""
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info(f"[Portfolio Guardian] Started background daemon (Interval: {self.interval}s, Auto-Execution: {self.is_enabled})")

    def stop(self):
        """Stops the guardian background loop."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("[Portfolio Guardian] Stopped background daemon")

    def set_enabled(self, enabled: bool):
        """Toggles automated broker order execution."""
        self.is_enabled = enabled
        logger.info(f"[Portfolio Guardian] Auto-execution set to {enabled}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the guardian daemon's operational status."""
        return {
            "is_running": self.is_running,
            "is_enabled": self.is_enabled,
            "interval_seconds": self.interval,
            "last_check_time": self.last_check_time,
            "recent_actions": self.last_actions[-10:],
            "rules": {
                "stop_loss_pct": -HARD_STOP_PCT,
                "trailing_activate_pct": TRAILING_ACTIVATE_PCT,
                "atr_multiplier": ATR_TRAIL_MULT,
                "partial_tp_ratio": 0.0,
                "uncapped_trailing": True,
                "kijun_break_check": True,
            }
        }

    def _get_adaptive_interval(self, holdings_count: int) -> float:
        """
        Adaptive polling interval:
        - configured interval if holdings exist
        - 15s if portfolio is empty to conserve KIS OpenAPI rate limits
        """
        if holdings_count > 0:
            return float(self.interval)
        return 15.0

    async def _monitor_loop(self):
        """Main async daemon polling loop with adaptive interval."""
        while self.is_running:
            holdings_count = 0
            try:
                res = await self.check_and_execute_guardian_rules()
                holdings_count = res.get("checked_count", 0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Portfolio Guardian Error] {e}", exc_info=True)

            sleep_duration = self._get_adaptive_interval(holdings_count)
            await asyncio.sleep(sleep_duration)

    async def check_and_execute_guardian_rules(self) -> Dict[str, Any]:
        """
        Scans active SQLite positions, updates prices, and executes KIS full-exit
        orders for hard stop, kijun breakdown, or uncapped trailing TP.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_check_time = now_str

        res = await asyncio.to_thread(self._sync_check_and_execute_guardian_rules)

        for act in res.get("actions", []):
            try:
                await hub.broadcast(EventType.GUARDIAN_ALERT, act)
                await hub.broadcast_delta(EventType.POSITION_DELTA, {
                    "ticker": act.get("ticker"),
                    "action": act.get("action") or "EXIT",
                    "holding": None,
                })
            except Exception:
                pass

        try:
            fresh_port = get_live_portfolio()
            await hub.broadcast(EventType.PORTFOLIO_UPDATE, fresh_port)
        except Exception:
            pass

        return res

    def _fetch_live_price(self, ticker: str, fallback: float) -> float:
        cur_price = None
        try:
            if default_kis_broker.is_configured():
                cur_price = default_kis_broker.get_live_price(ticker)

            if cur_price is None or cur_price <= 0:
                import yfinance as yf
                import logging as _logging
                _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
                ticker_obj = yf.Ticker(ticker)
                try:
                    fast_info = getattr(ticker_obj, "fast_info", None)
                    if fast_info:
                        p = (
                            getattr(fast_info, "last_price", None)
                            or getattr(fast_info, "regular_market_price", None)
                            or getattr(fast_info, "previous_close", None)
                        )
                        if p and float(p) > 0:
                            cur_price = float(p)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(f"[Portfolio Guardian] Live price fetch failed for {ticker}: {exc}")

        if cur_price is None or cur_price <= 0:
            cur_price = float(fallback or 0.0)
        return float(cur_price or 0.0)

    def _persist_mark(self, holding_id: int, cur_price: float, total_qty: float, buy_price: float, decision: Dict[str, Any]) -> None:
        pnl_pct = float(decision.get("pnl_pct") or 0.0)
        pnl_amt = (cur_price - buy_price) * total_qty
        cur_val = cur_price * total_qty
        hard_stop_price = float(decision.get("hard_stop_price") or derive_stop_price(buy_price))
        advice = format_holding_advice(decision, buy_price)
        try:
            with get_connection() as conn:
                conn.cursor().execute("""
                UPDATE my_portfolio
                SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?,
                    stop_loss_price = ?, max_gain_pct = ?, peak_high = ?,
                    kijun_26 = ?, atr_14 = ?, trailing_floor = ?, exit_advice = ?
                WHERE id = ?
                """, (
                    cur_price, cur_val, pnl_pct, pnl_amt,
                    hard_stop_price,
                    float(decision.get("max_gain_pct") or 0.0),
                    float(decision.get("peak_high") or 0.0),
                    float(decision.get("kijun_26") or 0.0),
                    float(decision.get("atr_14") or 0.0),
                    float(decision.get("trailing_floor") or 0.0),
                    advice,
                    holding_id,
                ))
                conn.commit()
        except Exception as e:
            logger.debug(f"[Guardian DB Update Error] {e}")

    def _sync_check_and_execute_guardian_rules(self) -> Dict[str, Any]:
        """
        1. Query DB for active holdings.
        2. Sync live quote via KIS / yfinance.
        3. Load 26D kijun + ATR(14) and enforce v2 full-exit rules.
        """
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_check_time = now_str

        if default_kis_broker.is_configured() and (now_ts - self.last_sync_time > 300.0):
            try:
                default_kis_broker.check_sync()
                self.last_sync_time = now_ts
            except Exception as sync_err:
                logger.debug(f"[Portfolio Guardian] Background check_sync skipped: {sync_err}")

        portfolio = get_live_portfolio()
        holdings = portfolio.get("holdings", [])
        if not holdings:
            return {"status": "success", "checked_count": 0, "actions": []}

        triggered_actions = []

        for h in holdings:
            holding_id = int(h["id"])
            ticker = h["ticker"].upper()
            buy_price = float(h["buy_price"])
            total_qty = float(h.get("quantity", 1.0))
            if buy_price <= 0 or total_qty <= 0:
                continue

            fallback_px = float(h.get("current_price") or buy_price)
            cur_price = self._fetch_live_price(ticker, fallback_px)
            if cur_price <= 0:
                continue

            snap = fetch_trailing_snapshot(ticker, buy_date=str(h.get("buy_date") or ""))
            peak_seed = max(
                float(h.get("peak_high") or 0.0),
                float(snap.get("peak_from_hist") or 0.0),
                float(snap.get("last_high") or 0.0),
            )
            decision = evaluate_guardian_exit(
                buy_price=buy_price,
                current_price=cur_price,
                kijun_26=float(snap.get("kijun_26") or h.get("kijun_26") or 0.0),
                atr_14=float(snap.get("atr_14") or h.get("atr_14") or 0.0),
                peak_high=peak_seed,
                max_gain_pct=float(h.get("max_gain_pct") or 0.0),
            )
            pnl_pct = float(decision["pnl_pct"])
            self._persist_mark(holding_id, cur_price, total_qty, buy_price, decision)

            action_type = decision.get("action")
            action_reason = decision.get("reason")
            if not action_type:
                continue

            sell_qty = total_qty
            order_id = None
            broker_status = "LOCAL"

            if self.is_enabled:
                if default_kis_broker.is_configured():
                    if not is_market_open_for_orders(ticker):
                        logger.info(
                            f"[Portfolio Guardian] {ticker} {action_type} signal detected, "
                            "but regular market is currently closed. Auto-order skipped until market open."
                        )
                        continue

                    try:
                        marketable_sell_price = round(cur_price * 0.995, 2)
                        ex_cd = "NYSE" if ticker in ["CVX", "JNJ", "XOM", "UNH", "PG", "JPM", "V", "MA", "LLY"] else "NASD"
                        broker_res = default_kis_broker.place_order(
                            ticker=ticker,
                            side="SELL",
                            qty=int(sell_qty),
                            price=marketable_sell_price,
                            order_type="00",
                            exchange=ex_cd
                        )
                        broker_status = broker_res.get("status", "error")
                        order_id = broker_res.get("order_id") or broker_res.get("odno")
                        if not is_broker_order_ack(broker_status):
                            logger.error(
                                f"[Portfolio Guardian] Broker sell order rejected for {ticker}: "
                                f"{broker_res.get('message')}. Local DB holding retained."
                            )
                            continue
                    except Exception as e:
                        logger.error(f"[Portfolio Guardian Broker Error] {e}")
                        continue

                logger.warning(f"[Portfolio Guardian Triggered] {action_reason}")
                record_portfolio_sell(
                    holding_id=holding_id,
                    sell_price=cur_price,
                    reason=f"{action_type} ({pnl_pct:+.2f}%)"
                )
            else:
                logger.info(f"[Portfolio Guardian] Signal {action_type} for {ticker} suppressed (auto-exec disabled).")
                continue

            action_record = {
                "timestamp": now_str,
                "holding_id": holding_id,
                "ticker": ticker,
                "action": action_type,
                "buy_price": buy_price,
                "sell_price": cur_price,
                "sold_quantity": sell_qty,
                "remaining_quantity": 0.0,
                "is_full_exit": True,
                "pnl_pct": round(pnl_pct, 2),
                "trailing_floor": decision.get("trailing_floor"),
                "kijun_26": decision.get("kijun_26"),
                "atr_14": decision.get("atr_14"),
                "peak_high": decision.get("peak_high"),
                "order_id": order_id,
                "broker_status": broker_status,
                "reason": action_reason,
            }
            triggered_actions.append(action_record)
            self.last_actions.append(action_record)

        return {
            "status": "success",
            "timestamp": now_str,
            "checked_count": len(holdings),
            "actions_count": len(triggered_actions),
            "actions": triggered_actions
        }


# Global singleton daemon instance (10s real-time sync)
default_guardian = PortfolioGuardian(check_interval_seconds=10)
