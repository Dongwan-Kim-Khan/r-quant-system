"""
AL-SANGMOO QUANT TERMINAL: PORTFOLIO GUARDIAN DAEMON
Background auto-execution of C1-M2 exits:
  1. -5.0% hard stop (full exit)
  2. 26-day kijun close breakdown (full exit)
  3. Uncapped trailing after +15% peak gain: max(Kijun-26, peak - 2.5*ATR(14))
  4. On satellite exit: redeploy proceeds into QQQ cash-proxy (+ QLD if 1.5x regime)
  5. QQQ/QLD cash-proxy sleeve is NOT subject to satellite exits (−5%/kijun/trailing);
     sleeve alignment (park / delever QLD→QQQ) runs via Cash Proxy overlay, not Guardian exits.
No 50% partial take-profit.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from al_sangmoo.core.market_time import is_regular_hours_for_ticker
from al_sangmoo.infrastructure.persistence import (
    claim_satellite_order_intent,
    claim_proxy_order_intent,
    clear_satellite_order_intent,
    clear_proxy_order_intent,
    get_connection,
    get_live_portfolio,
    mark_proxy_order_submitted,
    mark_satellite_order_submitted,
    record_portfolio_sell,
    record_portfolio_ticker_sell_quantity,
    add_portfolio_buy,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.api.hub import hub, EventType
from al_sangmoo.infrastructure.idempotent_order import (
    is_broker_order_ack,
    make_client_order_id,
)
from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    derive_stop_price,
    derive_target_price,
    derive_partial_tp_price,
)
from al_sangmoo.domain.risk import cash_proxy, exit_cooldown
from al_sangmoo.domain.risk.trailing_stop import (
    ATR_TRAIL_MULT,
    HARD_STOP_PCT,
    TRAILING_ACTIVATE_PCT,
    evaluate_guardian_exit,
    fetch_trailing_snapshot,
    format_holding_advice,
)
from al_sangmoo.domain.risk.cash_proxy import (
    build_cash_proxy_plan,
    is_proxy_ticker,
    park_proceeds_after_exit,
)
from al_sangmoo.domain.quant.macro import fetch_spy_trend_regime
from al_sangmoo.domain.risk.macro_guardrail import evaluate_dynamic_leverage

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
    Enforces C1-M2 exits without user intervention:
      1. -5% hard stop or 26D kijun close breakdown: 100% exit.
      2. After +15% peak gain: uncapped trailing floor max(kijun, peak - 2.5*ATR).
      3. Satellite exit proceeds immediately repark into QQQ cash proxy.
      4. Cash-proxy ETFs (QQQ/QLD) skip satellite exit rules; sleeve rebalance is separate.
    """

    def __init__(self, check_interval_seconds: int = 10):
        self.interval = check_interval_seconds
        self.is_running = False
        self.is_enabled = True
        self._task: Optional[asyncio.Task] = None
        self.last_check_time: Optional[str] = None
        self.last_sync_time: float = 0.0
        self.last_sleeve_align_time: float = 0.0
        self.last_actions: List[Dict[str, Any]] = []
        self._sleeve_align_min_interval_sec: float = 60.0
        self._pending_broker_orders: Dict[str, float] = {}
        self._pending_order_ttl_sec: float = 300.0

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
                "cash_proxy": CASH_PROXY_TICKER,
                "engine": "C1-M2",
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

    @exit_cooldown.serialized_satellite_transition
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
                from al_sangmoo.domain.reconciliation import check_sync
                sync_res = check_sync(auto_calibrate=True)
                if sync_res.get("status") == "success":
                    self.last_sync_time = now_ts
            except Exception as sync_err:
                logger.debug(f"[Portfolio Guardian] Background check_sync skipped: {sync_err}")

        portfolio = get_live_portfolio()
        holdings = portfolio.get("holdings", [])
        if not holdings:
            # A just-reconciled final satellite exit leaves an empty local book.
            # Sleeve alignment must still park the resulting cash in QQQ/QLD.
            sleeve_actions: List[Dict[str, Any]] = []
            try:
                sleeve_actions = self._align_cash_proxy_sleeve()
                if sleeve_actions:
                    self.last_actions.extend(sleeve_actions)
            except Exception as sleeve_err:
                logger.warning(
                    f"[Portfolio Guardian] Empty-book cash-proxy align failed: {sleeve_err}"
                )
            return {
                "status": "success",
                "timestamp": now_str,
                "checked_count": 0,
                "actions_count": len(sleeve_actions),
                "actions": sleeve_actions,
                "cash_proxy_sleeve_actions": sleeve_actions,
            }

        triggered_actions = []

        for h in holdings:
            holding_id = int(h["id"])
            ticker = h["ticker"].upper()
            buy_price = float(h["buy_price"])
            total_qty = float(h.get("quantity", 1.0))
            if buy_price <= 0 or total_qty <= 0:
                continue


            pending_key = f"SELL:{ticker}"
            pending_at = self._pending_broker_orders.get(pending_key, 0.0)
            if pending_at and (now_ts - pending_at) < self._pending_order_ttl_sec:
                continue
            self._pending_broker_orders.pop(pending_key, None)

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
            # Always mark-to-market so the dashboard/DB show the latest price & PnL.
            self._persist_mark(holding_id, cur_price, total_qty, buy_price, decision)

            # Cash proxy (QLD/QQQ): keep it marked, but the Guardian must NEVER
            # force-exit it. Dumping the parked proxy on a stop/kijun signal is the
            # whipsaw source; the sleeve rebalancer owns proxy sizing instead.
            if cash_proxy.is_proxy_ticker(ticker):
                continue

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

                    marketable_sell_price = round(cur_price * 0.995, 2)
                    ex_cd = "NYSE" if ticker in ["CVX", "DELL", "JNJ", "XOM", "UNH", "PG", "JPM", "V", "MA", "LLY"] else "NASD"
                    client_order_id = make_client_order_id()
                    if not claim_satellite_order_intent(
                        ticker=ticker,
                        side="SELL",
                        quantity=sell_qty,
                        price=marketable_sell_price,
                        owner="GUARDIAN",
                        exchange=ex_cd,
                        client_order_id=client_order_id,
                        baseline_quantity=total_qty,
                    ):
                        logger.info(
                            f"[Portfolio Guardian] Durable SELL intent still active "
                            f"for {ticker}; duplicate order blocked."
                        )
                        continue
                    try:
                        broker_res = default_kis_broker.place_order(
                            ticker=ticker,
                            side="SELL",
                            qty=int(sell_qty),
                            price=marketable_sell_price,
                            order_type="00",
                            exchange=ex_cd,
                            client_order_id=client_order_id,
                        )
                        broker_status = broker_res.get("status", "error")
                        order_id = broker_res.get("order_id") or broker_res.get("odno")
                        if not is_broker_order_ack(broker_status):
                            if "TIMEOUT" in str(
                                broker_res.get("reason") or ""
                            ).upper():
                                mark_satellite_order_submitted(
                                    ticker,
                                    "SELL",
                                    str(order_id or ""),
                                    str(broker_res.get("client_order_id") or ""),
                                )
                            else:
                                clear_satellite_order_intent(ticker, "SELL")
                            logger.error(
                                f"[Portfolio Guardian] Broker sell order rejected for {ticker}: "
                                f"{broker_res.get('message')}. Local DB holding retained."
                            )
                            continue
                        if broker_status != "filled":
                            # KIS "submitted" is only order acceptance, not a fill.
                            # Keep the local position until broker reconciliation confirms it left the account.
                            self._pending_broker_orders[pending_key] = now_ts
                            mark_satellite_order_submitted(
                                ticker,
                                "SELL",
                                str(order_id or ""),
                                str(broker_res.get("client_order_id") or ""),
                            )
                            triggered_actions.append({
                                "timestamp": now_str,
                                "holding_id": holding_id,
                                "ticker": ticker,
                                "action": f"{action_type}_SUBMITTED",
                                "sold_quantity": 0.0,
                                "remaining_quantity": total_qty,
                                "order_id": order_id,
                                "broker_status": broker_status,
                                "reason": action_reason,
                            })
                            self.last_actions.append(triggered_actions[-1])
                            continue
                    except Exception as e:
                        # Unknown POST outcome stays durably claimed. An operator
                        # or broker-state reconciliation must resolve it.
                        logger.error(f"[Portfolio Guardian Broker Error] {e}")
                        continue

                logger.warning(f"[Portfolio Guardian Triggered] {action_reason}")
                # Fail closed across a crash between broker/local-ledger steps:
                # a false-positive 24h lock is safer than a same-day re-buy.
                exit_cooldown.record_exit(
                    ticker,
                    action_type,
                    source="GUARDIAN",
                    order_id=str(order_id or ""),
                )
                sold_ok = record_portfolio_sell(
                    holding_id=holding_id,
                    sell_price=cur_price,
                    reason=f"{action_type} ({pnl_pct:+.2f}%)",
                    execution_log={
                        "order_type": "GUARDIAN_FORCED_EXIT",
                        "status": (
                            "SIMULATED"
                            if broker_status == "LOCAL"
                            else "FILLED"
                        ),
                        "message": (
                            f"Guardian {action_type} ({pnl_pct:+.2f}%): "
                            f"{action_reason}"
                        ),
                        "order_id": str(
                            order_id
                            or f"SIM-GUARDIAN-{ticker}-{int(now_ts * 1000)}"
                        ),
                        "timestamp": now_str,
                    },
                )
                if not sold_ok:
                    logger.error(
                        f"[Portfolio Guardian] Local sell ledger update failed for {ticker}"
                    )
                    continue
                if default_kis_broker.is_configured():
                    clear_satellite_order_intent(ticker, "SELL")
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

            # C1-M2: satellite exits repark into QQQ cash proxy
            try:
                proxy_actions = self._repark_exit_proceeds(sell_qty * cur_price)
                if proxy_actions:
                    action_record["cash_proxy_actions"] = proxy_actions
            except Exception as proxy_err:
                logger.warning(f"[Portfolio Guardian] Cash-proxy repark failed: {proxy_err}")

        # Sleeve alignment: delever QLD→QQQ / park idle when leverage regime changes
        sleeve_actions: List[Dict[str, Any]] = []
        try:
            sleeve_actions = self._align_cash_proxy_sleeve()
            if sleeve_actions:
                triggered_actions.extend(sleeve_actions)
                self.last_actions.extend(sleeve_actions)
        except Exception as sleeve_err:
            logger.warning(f"[Portfolio Guardian] Cash-proxy sleeve align failed: {sleeve_err}")

        sat_checked = len([h for h in holdings if not is_proxy_ticker(str(h.get("ticker") or ""))])
        return {
            "status": "success",
            "timestamp": now_str,
            "checked_count": sat_checked,
            "actions_count": len(triggered_actions),
            "actions": triggered_actions,
            "cash_proxy_sleeve_actions": sleeve_actions,
        }

    def _current_leverage_mode(self) -> bool:
        try:
            spy = fetch_spy_trend_regime()
            vix = None
            try:
                import yfinance as yf
                hist = yf.Ticker("^VIX").history(period="5d")
                if hist is not None and not hist.empty:
                    vix = float(hist["Close"].iloc[-1])
            except Exception:
                vix = None
            lev = evaluate_dynamic_leverage(
                spy_close=spy.get("spy_close"),
                spy_sma200=spy.get("spy_sma200"),
                vix=vix,
            )
            return bool(lev.get("leverage_mode"))
        except Exception:
            return False

    def _fetch_etf_price(self, ticker: str) -> float:
        try:
            if default_kis_broker.is_configured():
                px = default_kis_broker.get_live_price(ticker)
                if px and float(px) > 0:
                    return float(px)
        except Exception:
            pass
        return self._fetch_live_price(ticker, 0.0)

    @cash_proxy.serialized_proxy_orders
    def _align_cash_proxy_sleeve(self) -> List[Dict[str, Any]]:
        """
        Cash Proxy overlay automation (not satellite exits):
          - 1.0x: sell residual QLD (DELEVERAGE_TO_1X_QQQ_CORE), park idle in QQQ
          - 1.5x: maintain QQQ+QLD idle mix
        Delever sells run immediately; other sleeve actions throttle to ~60s.
        """
        if not self.is_enabled:
            return []

        port = get_live_portfolio()
        holdings = list(port.get("holdings", []))
        equity = float(port.get("total_equity_usd") or port.get("total_value") or 0.0)
        cash = float(port.get("cash_usd") or port.get("free_cash_usd") or port.get("cash") or 0.0)
        if equity <= 0:
            return []

        leverage_on = self._current_leverage_mode()
        qqq_px = self._fetch_etf_price(CASH_PROXY_TICKER)
        qld_px = self._fetch_etf_price("QLD")  # always needed for possible QLD sells
        plan = build_cash_proxy_plan(
            total_equity_usd=equity,
            holdings=holdings,
            cash_usd=cash,
            leverage_mode=leverage_on,
            qqq_price=qqq_px,
            qld_price=qld_px,
        )
        actions = list(plan.get("actions") or [])
        if not actions:
            return []

        has_delever = any(a.get("reason") == "DELEVERAGE_TO_1X_QQQ_CORE" for a in actions)
        now_ts = time.time()
        if not has_delever and (now_ts - self.last_sleeve_align_time) < self._sleeve_align_min_interval_sec:
            return []
        self.last_sleeve_align_time = now_ts

        # SELL first (free cash / delever), then BUY park
        ordered = [a for a in actions if a.get("side") == "SELL"] + [
            a for a in actions if a.get("side") == "BUY"
        ]
        executed: List[Dict[str, Any]] = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for act in ordered:
            ticker = str(act.get("ticker") or "").upper()
            side = str(act.get("side") or "").upper()
            qty = int(act.get("qty") or 0)
            px = qqq_px if ticker == CASH_PROXY_TICKER else qld_px
            if not ticker or side not in {"BUY", "SELL"} or qty <= 0 or px <= 0:
                continue

            pending_key = f"{side}:{ticker}"
            pending_at = self._pending_broker_orders.get(pending_key, 0.0)
            if pending_at and (now_ts - pending_at) < self._pending_order_ttl_sec:
                # Do not buy the replacement sleeve until the pending sell settles.
                if side == "SELL":
                    break
                continue
            self._pending_broker_orders.pop(pending_key, None)

            broker_status = "SIMULATED"
            order_id = None
            if default_kis_broker.is_configured():
                if not is_market_open_for_orders(ticker):
                    continue
            client_order_id = make_client_order_id()
            if not claim_proxy_order_intent(
                ticker=ticker,
                side=side,
                quantity=float(qty),
                price=px,
                owner="GUARDIAN_ALIGN",
                exchange="AMEX" if ticker == "QLD" else "NASD",
                client_order_id=client_order_id,
                baseline_quantity=sum(
                    float(h.get("quantity") or 0.0)
                    for h in holdings
                    if str(h.get("ticker") or "").upper() == ticker
                ),
            ):
                executed.append({
                    "timestamp": now_str,
                    "ticker": ticker,
                    "action": f"CASH_PROXY_{side}_PENDING",
                    "side": side,
                    "qty": qty,
                    "price": px,
                    "broker_status": "PERSISTENT_ORDER_PENDING",
                    "reason": act.get("reason"),
                })
                if side == "SELL":
                    break
                continue
            if default_kis_broker.is_configured():
                try:
                    limit = round(px * (1.005 if side == "BUY" else 0.995), 2)
                    broker_res = default_kis_broker.place_order(
                        ticker=ticker,
                        side=side,
                        qty=qty,
                        price=limit,
                        order_type="00",
                        exchange="AMEX" if ticker == "QLD" else "NASD",
                        client_order_id=client_order_id,
                    )
                    broker_status = broker_res.get("status", "error")
                    order_id = broker_res.get("order_id") or broker_res.get("odno")
                    if not is_broker_order_ack(broker_status):
                        if "TIMEOUT" in str(
                            broker_res.get("reason") or ""
                        ).upper():
                            mark_proxy_order_submitted(
                                ticker,
                                str(order_id or ""),
                                str(
                                    broker_res.get("client_order_id")
                                    or client_order_id
                                ),
                            )
                        else:
                            clear_proxy_order_intent(ticker)
                        logger.error(
                            f"[Guardian CashProxy] {side} {ticker} x{qty} rejected: "
                            f"{broker_res.get('message')}"
                        )
                        continue
                    if broker_status != "filled":
                        # Accepted is not filled. Broker reconciliation owns the local ledger update.
                        self._pending_broker_orders[pending_key] = now_ts
                        mark_proxy_order_submitted(
                            ticker,
                            str(order_id or ""),
                            str(
                                broker_res.get("client_order_id")
                                or client_order_id
                            ),
                        )
                        executed.append({
                            "timestamp": now_str,
                            "ticker": ticker,
                            "action": f"CASH_PROXY_{side}_SUBMITTED",
                            "side": side,
                            "qty": qty,
                            "price": px,
                            "order_id": order_id,
                            "broker_status": broker_status,
                            "reason": act.get("reason"),
                        })
                        if side == "SELL":
                            break
                        continue
                except Exception as e:
                    logger.error(f"[Guardian CashProxy {side} Error] {e}")
                    continue

            audit_log = {
                "order_type": "GUARDIAN_CASH_PROXY_REBALANCE",
                "status": (
                    "SIMULATED" if broker_status == "SIMULATED" else "FILLED"
                ),
                "message": f"Guardian cash-proxy sleeve: {act.get('reason')}",
                "order_id": str(
                    order_id
                    or f"SIM-GUARDIAN-PROXY-{side}-{ticker}-{int(now_ts * 1000)}"
                ),
                "timestamp": now_str,
            }
            if side == "BUY":
                add_portfolio_buy(
                    ticker=ticker,
                    buy_price=px,
                    quantity=float(qty),
                    buy_date=datetime.now().strftime("%Y-%m-%d"),
                    target_price=derive_target_price(px),
                    stop_loss_price=derive_stop_price(px),
                    partial_tp_price=derive_partial_tp_price(px),
                    execution_log=audit_log,
                )
            else:
                try:
                    ledger_sold = record_portfolio_ticker_sell_quantity(
                        ticker=ticker,
                        sell_price=px,
                        quantity=float(qty),
                        reason=f"CASH_PROXY_SLEEVE ({act.get('reason')})",
                        execution_log=audit_log,
                    )
                except Exception as e:
                    logger.error(f"[Guardian CashProxy Sell Ledger Error] {e}")
                    ledger_sold = 0.0
                if broker_status == "SIMULATED" and ledger_sold + 1e-9 < qty:
                    clear_proxy_order_intent(ticker)
                    continue
            clear_proxy_order_intent(ticker)

            rec = {
                "timestamp": now_str,
                "ticker": ticker,
                "action": f"CASH_PROXY_{side}",
                "side": side,
                "qty": qty,
                "price": px,
                "order_id": order_id,
                "broker_status": broker_status,
                "reason": act.get("reason"),
            }
            executed.append(rec)
            logger.info(
                f"[Portfolio Guardian] Cash-proxy sleeve: {side} {ticker} x{qty} @ ${px:.2f} "
                f"({act.get('reason')})"
            )

        return executed

    @cash_proxy.serialized_proxy_orders
    def _repark_exit_proceeds(self, proceeds_usd: float) -> List[Dict[str, Any]]:
        """Buy QQQ (+ QLD if 1.5x) with satellite exit proceeds."""
        if proceeds_usd <= 0 or not self.is_enabled:
            return []
        leverage_on = self._current_leverage_mode()
        qqq_px = self._fetch_etf_price(CASH_PROXY_TICKER)
        qld_px = self._fetch_etf_price("QLD") if leverage_on else 0.0
        plan = park_proceeds_after_exit(
            proceeds_usd=proceeds_usd,
            leverage_mode=leverage_on,
            qqq_price=qqq_px,
            qld_price=qld_px,
        )
        executed: List[Dict[str, Any]] = []
        for act in plan:
            ticker = act["ticker"]
            qty = int(act["qty"])
            if qty <= 0:
                continue
            px = qqq_px if ticker == CASH_PROXY_TICKER else qld_px
            if px <= 0:
                continue
            pending_key = f"BUY:{ticker}"
            pending_at = self._pending_broker_orders.get(pending_key, 0.0)
            if pending_at and (time.time() - pending_at) < self._pending_order_ttl_sec:
                executed.append({
                    **act,
                    "status": "SUBMITTED_AWAITING_RECONCILIATION",
                })
                continue
            self._pending_broker_orders.pop(pending_key, None)
            broker_status = "SIMULATED"
            order_id = None
            if default_kis_broker.is_configured():
                if not is_market_open_for_orders(ticker):
                    continue
            client_order_id = make_client_order_id()
            if not claim_proxy_order_intent(
                ticker=ticker,
                side="BUY",
                quantity=float(qty),
                price=px,
                owner="GUARDIAN_REPARK",
                exchange="AMEX" if ticker == "QLD" else "NASD",
                client_order_id=client_order_id,
                baseline_quantity=sum(
                    float(h.get("quantity") or 0.0)
                    for h in (
                        get_live_portfolio().get("holdings") or []
                    )
                    if str(h.get("ticker") or "").upper() == ticker
                ),
            ):
                executed.append({
                    **act,
                    "status": "PERSISTENT_ORDER_PENDING",
                })
                continue
            if default_kis_broker.is_configured():
                try:
                    broker_res = default_kis_broker.place_order(
                        ticker=ticker,
                        side="BUY",
                        qty=qty,
                        price=round(px * 1.005, 2),
                        order_type="00",
                        exchange="AMEX" if ticker == "QLD" else "NASD",
                        client_order_id=client_order_id,
                    )
                    broker_status = broker_res.get("status", "error")
                    order_id = broker_res.get("order_id") or broker_res.get("odno")
                    if not is_broker_order_ack(broker_status):
                        if "TIMEOUT" in str(
                            broker_res.get("reason") or ""
                        ).upper():
                            mark_proxy_order_submitted(
                                ticker,
                                str(order_id or ""),
                                str(
                                    broker_res.get("client_order_id")
                                    or client_order_id
                                ),
                            )
                        else:
                            clear_proxy_order_intent(ticker)
                        continue
                    if broker_status != "filled":
                        self._pending_broker_orders[pending_key] = time.time()
                        mark_proxy_order_submitted(
                            ticker,
                            str(order_id or ""),
                            str(
                                broker_res.get("client_order_id")
                                or client_order_id
                            ),
                        )
                        executed.append({
                            **act,
                            "status": "SUBMITTED_AWAITING_RECONCILIATION",
                            "order_id": order_id,
                            "fill_price": None,
                        })
                        continue
                except Exception as e:
                    logger.error(f"[Guardian CashProxy Buy Error] {e}")
                    continue
            add_portfolio_buy(
                ticker=ticker,
                buy_price=px,
                quantity=float(qty),
                buy_date=datetime.now().strftime("%Y-%m-%d"),
                target_price=derive_target_price(px),
                stop_loss_price=derive_stop_price(px),
                partial_tp_price=derive_partial_tp_price(px),
                execution_log={
                    "order_type": "GUARDIAN_EXIT_PROCEEDS_PARK",
                    "status": (
                        "SIMULATED"
                        if broker_status == "SIMULATED"
                        else "FILLED"
                    ),
                    "message": (
                        f"Guardian exit proceeds repark: {act.get('reason')}"
                    ),
                    "order_id": str(
                        order_id
                        or f"SIM-GUARDIAN-REPARK-{ticker}-{int(time.time() * 1000)}"
                    ),
                },
            )
            clear_proxy_order_intent(ticker)
            executed.append({
                **act,
                "broker_status": broker_status,
                "order_id": order_id,
                "fill_price": px,
            })
            logger.info(
                f"[Portfolio Guardian] Cash-proxy repark: BUY {ticker} x{qty} @ ${px:.2f} ({act.get('reason')})"
            )
        return executed


# Global singleton daemon instance (10s real-time sync)
default_guardian = PortfolioGuardian(check_interval_seconds=10)
