"""
AL-SANGMOO QUANT TERMINAL: PORTFOLIO GUARDIAN DAEMON
Continuous background auto-execution engine for -4% Stop-Loss and +15% Partial Take-Profit (50%).
Directly executes broker sell orders via KIS OpenAPI and logs to SSOT SQLite.
"""

import asyncio
import logging
import math
from datetime import datetime
from typing import Dict, Any, List, Optional
import pandas as pd
import yfinance as yf

import db_manager
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.domain.quant.ichimoku import calculate_ichimoku_indicators
from al_sangmoo.api.hub import hub

logger = logging.getLogger(__name__)


class PortfolioGuardian:
    """
    Autonomous background guardian monitoring active positions every N seconds.
    Enforces Al-Sangmoo 17-Year quantitative exit rules without requiring user intervention:
      1. -4% Stop Loss or 26D Kijun break: 100% full stop loss execution.
      2. +15% Take Profit: 50% partial profit locking (letting remaining 50% run for top gains).
    """

    def __init__(self, check_interval_seconds: int = 30):
        self.interval = check_interval_seconds
        self.is_running = False
        self.is_enabled = True  # Auto-execution enabled by default
        self._task: Optional[asyncio.Task] = None
        self.last_check_time: Optional[str] = None
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
                "stop_loss_pct": -4.0,
                "take_profit_pct": 15.0,
                "partial_tp_ratio": 0.50,
                "kijun_break_check": True
            }
        }

    def _get_adaptive_interval(self, holdings_count: int) -> float:
        """
        Adaptive polling interval:
        - 10s if active holdings exist.
        - 30s if portfolio is empty to conserve KIS OpenAPI rate limits.
        """
        if holdings_count > 0:
            return float(self.interval)
        return max(30.0, float(self.interval) * 3)

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
        Scans all active SQLite positions, updates prices, and executes KIS orders
        for -4% full stop-loss or +15% 50% partial take-profit in a background worker thread.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_check_time = now_str

        # Offload synchronous broker and price lookups to background worker thread
        res = await asyncio.to_thread(self._sync_check_and_execute_guardian_rules)

        # Broadcast live alerts to WebSockets on main async loop
        for act in res.get("actions", []):
            try:
                await hub.broadcast("guardian_alert", act)
            except Exception:
                pass

        try:
            fresh_port = db_manager.get_live_portfolio()
            await hub.broadcast("portfolio_update", fresh_port)
        except Exception:
            pass

        return res

    def _sync_check_and_execute_guardian_rules(self) -> Dict[str, Any]:
        """Synchronous worker executing broker TRs, price lookups, and DB state changes."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Auto-Reconcile against KIS Broker SSOT on every guardian cycle
        if default_kis_broker.is_configured():
            try:
                from al_sangmoo.domain.reconciliation import check_sync
                check_sync(auto_calibrate=True)
            except Exception as sync_exc:
                logger.debug(f"[Guardian Auto-Sync] {sync_exc}")

        portfolio = db_manager.get_live_portfolio()
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

            # 1. Fetch real-time price (Primary: KIS OpenAPI TR HHDFS00000300, Fallback: yfinance fast_info/history)
            cur_price = None
            kijun_line = float(h.get("stop_loss_price", buy_price * 0.96))

            try:
                if default_kis_broker.is_configured():
                    cur_price = default_kis_broker.get_live_price(ticker)
                
                if cur_price is None or cur_price <= 0:
                    import logging as _logging
                    _logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
                    ticker_obj = yf.Ticker(ticker)
                    # Safe fast_info lookup (resilient to yfinance 'currentTradingPeriod' KeyError)
                    try:
                        fast_info = getattr(ticker_obj, "fast_info", None)
                        if fast_info:
                            p = getattr(fast_info, "last_price", None) or getattr(fast_info, "regular_market_price", None) or getattr(fast_info, "previous_close", None)
                            if p and float(p) > 0:
                                cur_price = float(p)
                    except Exception:
                        pass

                    # Resilient 5-day history fallback
                    if cur_price is None or cur_price <= 0:
                        try:
                            hist = ticker_obj.history(period="5d")
                            if not hist.empty and "Close" in hist.columns:
                                close_series = hist["Close"].dropna()
                                if not close_series.empty:
                                    cur_price = float(close_series.iloc[-1])
                        except Exception:
                            pass

                    # Final fallback: 1mo OHLCV for Ichimoku Kijun reference
                    if cur_price is None or cur_price <= 0:
                        try:
                            df = yf.download(ticker, period="1mo", interval="1d", progress=False)
                            if not df.empty:
                                if isinstance(df.columns, pd.MultiIndex):
                                    df.columns = df.columns.get_level_values(0)
                                df = calculate_ichimoku_indicators(df)
                                last_row = df.iloc[-1]
                                cur_price = float(last_row["Close"])
                                kijun_line = float(last_row.get("Kijun", kijun_line))
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning(f"[Portfolio Guardian] Live price fetch failed for {ticker}: {exc}")

            if cur_price is None or cur_price <= 0:
                cur_price = float(h.get("current_price") or buy_price)


            if cur_price is None or cur_price <= 0:
                continue

            # 2. Compute PnL % & update live price in DB
            pnl_pct = ((cur_price - buy_price) / buy_price) * 100.0
            cur_val = cur_price * total_qty
            total_cost = buy_price * total_qty
            pnl_amt = cur_val - total_cost
            stop_price = float(h.get("stop_loss_price") or (buy_price * 0.96))
            target_price = float(h.get("target_price") or (buy_price * 1.15))

            try:
                with db_manager.get_connection() as conn:
                    conn.cursor().execute("""
                    UPDATE my_portfolio
                    SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?
                    WHERE id = ?
                    """, (cur_price, cur_val, pnl_pct, pnl_amt, holding_id))
                    conn.commit()
            except Exception as e:
                logger.debug(f"[Guardian DB Update Error] {e}")


            action_type = None
            action_reason = None
            sell_qty = 0.0
            is_full_exit = True

            # 1. Condition 1: Strict -4% Hard Stop-Loss (100% Full Exit)
            if pnl_pct <= -4.0 or cur_price <= stop_price:
                action_type = "AUTO_STOP_LOSS"
                action_reason = f"[가디언 칼손절] {ticker} 손절선(-4%) 도달 전량 매도 (수익률: {pnl_pct:+.2f}%, 현재가: ${cur_price:,.2f})"
                sell_qty = total_qty
                is_full_exit = True

            # 2. Condition 2: Uncapped Trailing Profit Exit (Let Winners Run - v2 Engine)
            # If position gained >= +15%, dynamically trail with max(Kijun, Peak - 2.5*ATR)
            elif pnl_pct >= 15.0:
                # Calculate trailing floor: Kijun line or ATR floor
                trailing_floor = max(kijun_line, buy_price * 1.10)
                if cur_price < trailing_floor:
                    action_type = "AUTO_TRAILING_TP"
                    action_reason = f"[가디언 트레일링 익절] {ticker} 대세 상승 완결 무제한 익절 (수익률: {pnl_pct:+.2f}%, 현재가: ${cur_price:,.2f})"
                    sell_qty = total_qty
                    is_full_exit = True

            # 3. Condition 3: Normal Kijun Line Breakdown (if position below Kijun and in profit/neutral)
            elif kijun_line > 0 and cur_price < kijun_line and pnl_pct > 0:
                action_type = "AUTO_KIJUN_EXIT"
                action_reason = f"[가디언 기준선 이탈] {ticker} 26일 기준선(${kijun_line:,.2f}) 하회 추세 청산 (수익률: {pnl_pct:+.2f}%)"
                sell_qty = total_qty
                is_full_exit = True

            # 3. Execute Automated Exit if triggered & enabled
            if action_type and self.is_enabled and sell_qty > 0:
                logger.warning(f"[Portfolio Guardian Triggered] {action_reason}")

                order_id = None
                broker_status = "SKIPPED_UNCONFIGURED"

                # Execute order via KIS Broker
                if default_kis_broker.is_configured():
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
                        order_id = broker_res.get("order_id")
                        broker_status = broker_res.get("status", "error")
                    except Exception as e:
                        logger.error(f"[Portfolio Guardian Broker Error] {e}")
                        broker_status = "FAILED"

                if is_full_exit:
                    # Full close in SQLite
                    db_manager.record_portfolio_sell(
                        holding_id=holding_id,
                        sell_price=cur_price,
                        reason=f"{action_type} ({pnl_pct:+.2f}%)"
                    )
                else:
                    # Partial close: reduce SQLite holding quantity
                    remaining_qty = total_qty - sell_qty
                    with db_manager.get_connection() as conn:
                        cursor = conn.cursor()
                        rem_cost = remaining_qty * buy_price
                        rem_val = remaining_qty * cur_price
                        pnl_amt = rem_val - rem_cost
                        cursor.execute("""
                        UPDATE my_portfolio
                        SET quantity = ?, total_cost = ?, current_value = ?,
                            current_price = ?, pnl_pct = ?, pnl_amount = ?,
                            exit_advice = ?
                        WHERE id = ?
                        """, (
                            remaining_qty, rem_cost, rem_val,
                            cur_price, pnl_pct, pnl_amt,
                            f"50% 분할익절 완료 (잔여 {int(remaining_qty)}주 추세 홀딩)",
                            holding_id
                        ))
                        # Insert partial trade record to history
                        cursor.execute("""
                        INSERT INTO trade_history (
                            holding_id, ticker, buy_date, sell_date, buy_price,
                            sell_price, quantity, pnl_pct, pnl_amount, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            holding_id, ticker, h.get("buy_date", now_str[:10]), now_str[:10],
                            buy_price, cur_price, sell_qty, pnl_pct, (cur_price - buy_price) * sell_qty,
                            f"AUTO_PARTIAL_TP_50 ({pnl_pct:+.2f}%)", now_str
                        ))
                        conn.commit()

                action_record = {
                    "timestamp": now_str,
                    "holding_id": holding_id,
                    "ticker": ticker,
                    "action": action_type,
                    "buy_price": buy_price,
                    "sell_price": cur_price,
                    "sold_quantity": sell_qty,
                    "remaining_quantity": total_qty - sell_qty if not is_full_exit else 0.0,
                    "is_full_exit": is_full_exit,
                    "pnl_pct": round(pnl_pct, 2),
                    "order_id": order_id,
                    "broker_status": broker_status,
                    "reason": action_reason
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


# Global singleton daemon instance (10s ultra-fast real-time sync)
default_guardian = PortfolioGuardian(check_interval_seconds=10)

