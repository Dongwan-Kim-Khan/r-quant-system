"""
AL-SANGMOO QUANT TERMINAL: AUTOPILOT TRADER ENGINE
C1-M2 autonomous scanner + Rank#1 conviction entry with 50/30/20 NAV sizing,
QQQ cash-proxy funding, and KIS OpenAPI execution.
"""

import asyncio
import logging
import math
import os
import json
from datetime import datetime, time as dtime
from typing import Dict, Any, List, Optional

from al_sangmoo.infrastructure.persistence import (
    add_portfolio_buy,
    get_live_portfolio,
    record_portfolio_sell,
    sync_portfolio_prices,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.infrastructure.idempotent_order import is_broker_order_ack
from al_sangmoo.core.constants import (
    CASH_PROXY_TICKER,
    HARD_STOP_PCT,
    MAX_SLOTS_BEAR,
    MAX_SLOTS_BULL,
    derive_partial_tp_price,
    derive_stop_price,
    derive_target_price,
)
from al_sangmoo.api.hub import hub, EventType
from al_sangmoo.domain.risk.cash_proxy import (
    build_cash_proxy_plan,
    is_proxy_ticker,
    raise_cash_from_proxy,
    satellite_holdings,
)
from al_sangmoo.domain.risk.macro_guardrail import evaluate_dynamic_leverage
from al_sangmoo.domain.quant.macro import fetch_spy_trend_regime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")


class AutoPilotTrader:
    """
    100% Unattended Full-Auto Trading Engine (C1-M2).
    Executes market-open quant scan, selects #1 conviction pick (50% NAV),
    frees cash from QQQ proxy when needed, and places KIS orders into empty slots.
    """

    def __init__(self, check_interval_seconds: int = 60):
        self.interval = check_interval_seconds
        self.is_running = False
        self.is_enabled = False  # Auto-buying safety disabled by default (Must be explicitly activated)
        self._task: Optional[asyncio.Task] = None
        self.last_run_time: Optional[str] = None
        self.last_run_result: Optional[Dict[str, Any]] = None
        self.last_action: Optional[str] = None
        self.trade_logs: List[Dict[str, Any]] = []

    def start(self):
        """Starts the autopilot background daemon loop."""
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info(f"[AutoPilot Trader] Started background daemon (Interval: {self.interval}s, Auto-Buy: {self.is_enabled})")

    def stop(self):
        """Stops the autopilot background daemon loop."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("[AutoPilot Trader] Stopped background daemon")

    def set_enabled(self, enabled: bool):
        """Toggles automated broker buy execution."""
        self.is_enabled = enabled
        logger.info(f"[AutoPilot Trader] Auto-buy enabled set to {enabled}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the autopilot operational status."""
        return {
            "is_running": self.is_running,
            "is_enabled": self.is_enabled,
            "interval_seconds": self.interval,
            "last_run_time": self.last_run_time,
            "last_run_result": self.last_run_result,
            "last_action": self.last_action,
            "trade_logs_count": len(self.trade_logs),
            "recent_trades": self.trade_logs[-5:]
        }

    async def _scheduler_loop(self):
        """
        Background scheduler checking for market open window (22:30 ~ 05:00 KST)
        and executing scheduled quant scans and autonomous entries.
        """
        while self.is_running:
            try:
                now = datetime.now()
                # Run scheduled check during US market hours (22:30 ~ 06:00 KST)
                is_market_hours = (now.hour >= 22 or now.hour < 6)
                
                # Check if we should execute a scheduled cycle
                if self.is_enabled and is_market_hours:
                    # Count satellite slots only (QQQ/QLD cash-proxy excluded)
                    port = get_live_portfolio()
                    sats = satellite_holdings(port.get("holdings", []))
                    if len(sats) < MAX_SLOTS_BULL:
                        await self.run_autopilot_cycle(force_scan=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AutoPilot Loop Error] {e}", exc_info=True)

            await asyncio.sleep(self.interval)

    async def run_autopilot_cycle(self, force_scan: bool = True) -> Dict[str, Any]:
        """
        Executes a complete 100% full-auto cycle:
          1. Scans 60 universe tickers via generate_dashboard_feed.py
          2. Extracts #1 Top Conviction Pick & 3-Slot Integer share allocation
          3. Inspects current portfolio slot occupancy (Max 2 stock slots)
          4. Verifies deduplication (Not already in portfolio)
          5. Executes real KIS Buy order and logs to SQLite SSOT
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_run_time = now_str
        logger.info(f"[AutoPilot Cycle] Initiating full-auto trade cycle at {now_str} (Force Scan: {force_scan})")

        # Step 1: Run fresh scan if requested or if cache is missing
        if force_scan or not os.path.exists(DASHBOARD_JSON):
            try:
                from generate_dashboard_feed import build_dashboard_data
                await asyncio.to_thread(build_dashboard_data)
                logger.info("[AutoPilot Cycle] Fresh 60-ticker universe scan completed successfully")
            except Exception as scan_err:
                logger.error(f"[AutoPilot Scan Error] {scan_err}")

        # Step 2: Load dashboard feed & #1 Top Pick
        if not os.path.exists(DASHBOARD_JSON):
            res = {"status": "error", "message": "Dashboard feed not available", "timestamp": now_str}
            self.last_run_result = res
            return res

        try:
            with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
                feed = json.load(f)
        except Exception as e:
            res = {"status": "error", "message": f"Failed to read feed: {e}", "timestamp": now_str}
            self.last_run_result = res
            return res

        top_pick = feed.get("top_conviction_pick")
        if not top_pick:
            # No leadership signal → ensure idle capital stays parked in QQQ
            proxy_note = self._ensure_cash_proxy_parked(feed)
            res = {
                "status": "skipped",
                "reason": "NO_QUALIFIED_TOP_PICK",
                "message": "현재 기준을 충족하는 1위 확신도 종목이 없습니다. 유휴 자본은 QQQ Cash Proxy에 파킹합니다.",
                "cash_proxy": proxy_note,
                "timestamp": now_str,
            }
            self.last_run_result = res
            self.last_action = "NO_TOP_PICK_QQQ_PARK"
            return res

        ticker = str(top_pick.get("ticker", "")).upper()
        ticker_name = top_pick.get("name", ticker)
        sizing = top_pick.get("sizing", {})
        shares = int(sizing.get("shares", 0))
        cur_price = float(top_pick.get("price", 0.0))

        if not sizing.get("eligible", False) or shares <= 0 or cur_price <= 0:
            res = {"status": "skipped", "reason": "SIZING_NOT_ELIGIBLE", "message": f"{ticker} 슬롯 자본금 부족 또는 유효하지 않은 수량", "timestamp": now_str}
            self.last_run_result = res
            self.last_action = f"SIZING_INELIGIBLE_{ticker}"
            return res

        # Step 3: Inspect satellite holdings & Slot capacity (exclude QQQ/QLD proxy)
        port = get_live_portfolio()
        holdings = port.get("holdings", [])
        sats = satellite_holdings(holdings)
        active_tickers = [str(h["ticker"]).upper() for h in holdings]

        # Guardrail 1: Deduplication (Do not buy if already holding)
        if ticker in active_tickers:
            res = {
                "status": "skipped",
                "reason": "ALREADY_IN_WALLET",
                "ticker": ticker,
                "message": f"{ticker_name} ({ticker}) 종목을 이미 포트폴리오에 보유 중입니다.",
                "timestamp": now_str
            }
            self.last_run_result = res
            self.last_action = f"ALREADY_HOLDING_{ticker}"
            return res

        # Guardrail 2: Dynamic Regime Sizing (Max 3 slots in Bull, Max 2 in Bear)
        is_bull_regime = bool(sizing.get("is_bull_regime", feed.get("macro", {}).get("is_bull_regime", True)))
        max_allowed_slots = MAX_SLOTS_BULL if is_bull_regime else MAX_SLOTS_BEAR

        if len(sats) >= max_allowed_slots:
            proxy_note = self._ensure_cash_proxy_parked(feed)
            res = {
                "status": "skipped",
                "reason": "SLOTS_FULL",
                "message": (
                    f"C1-M2 슬롯 한도 도달 (위성 {len(sats)}/{max_allowed_slots} / "
                    f"{'상승장 50/30/20' if is_bull_regime else '하락장 25/25'}). "
                    "유휴분은 QQQ Cash Proxy 유지."
                ),
                "holdings_count": len(sats),
                "max_slots": max_allowed_slots,
                "is_bull_regime": is_bull_regime,
                "cash_proxy": proxy_note,
                "timestamp": now_str
            }
            self.last_run_result = res
            self.last_action = f"SLOTS_FULL_MAX_{max_allowed_slots}"
            return res

        # Guardrail 3: Direct Broker SSOT Verification (Physical protection against duplicate buys)
        if default_kis_broker.is_configured():
            try:
                broker_bal = default_kis_broker.get_overseas_balance()
                broker_holdings = broker_bal.get("holdings", [])
                broker_sats = [bh for bh in broker_holdings if not is_proxy_ticker(str(bh.get("ticker", "")))]
                broker_tickers = [str(bh.get("ticker", "")).upper() for bh in broker_holdings]
                if ticker in broker_tickers:
                    res = {
                        "status": "skipped",
                        "reason": "ALREADY_IN_BROKER_HOLDINGS",
                        "ticker": ticker,
                        "message": f"증권사 실계좌에 {ticker} 종목이 이미 체결되어 보유 중입니다.",
                        "timestamp": now_str
                    }
                    self.last_run_result = res
                    self.last_action = f"ALREADY_IN_BROKER_{ticker}"
                    return res
                if len(broker_sats) >= max_allowed_slots:
                    res = {
                        "status": "skipped",
                        "reason": "BROKER_SLOTS_FULL",
                        "message": f"증권사 실계좌 위성 슬롯 만석 도달 (실보유 위성: {len(broker_sats)}개)",
                        "timestamp": now_str
                    }
                    self.last_run_result = res
                    self.last_action = f"BROKER_SLOTS_FULL_{len(broker_sats)}"
                    return res
            except Exception as b_err:
                logger.warning(f"[AutoPilot Broker Guardrail Error] {b_err}")

        # Step 4: Execute Autonomous Buy via KIS OpenAPI if Auto-Buy is ON
        if not self.is_enabled:
            res = {
                "status": "skipped",
                "reason": "AUTO_BUY_DISABLED",
                "ticker": ticker,
                "message": "전자동 매수(Auto-Buy) 기능이 비활성화(PAUSED) 상태입니다.",
                "timestamp": now_str
            }
            self.last_run_result = res
            self.last_action = "AUTO_BUY_PAUSED"
            return res

        # Check KIS Live Price before placing order
        live_price = None
        if default_kis_broker.is_configured():
            try:
                live_price = default_kis_broker.get_live_price(ticker)
            except Exception:
                pass
        exec_price = live_price if live_price and live_price > 0 else cur_price
        needed_usd = float(shares) * float(exec_price)

        # C1-M2: free cash from QQQ/QLD proxy before leadership entry
        proxy_sells = self._free_proxy_cash_for_entry(needed_usd, holdings)

        # Institutional Marketable Limit: Add 0.5% slippage buffer to cross the spread and guarantee instant fill
        marketable_exec_price = round(exec_price * 1.005, 2)

        order_id = None
        broker_status = "SIMULATED"
        broker_msg = "시뮬레이션 모드 매수 접수"

        if default_kis_broker.is_configured():
            try:
                # Detect exchange (NYS or NASD)
                ex_cd = "NYSE" if ticker in ["CVX", "JNJ", "XOM", "UNH", "PG", "JPM", "V", "MA", "LLY"] else "NASD"
                broker_res = default_kis_broker.place_order(
                    ticker=ticker,
                    side="BUY",
                    qty=shares,
                    price=marketable_exec_price,
                    order_type="00",
                    exchange=ex_cd
                )
                order_id = broker_res.get("order_id")
                broker_status = broker_res.get("status", "error")
                broker_msg = broker_res.get("broker_message", broker_res.get("message", ""))
                if not is_broker_order_ack(broker_status):
                    res = {
                        "status": "skipped",
                        "reason": str(broker_status or "BROKER_ORDER_NOT_ACKED"),
                        "ticker": ticker,
                        "message": broker_msg or "증권사 주문 확인 실패. 로컬 원장에 매수를 기록하지 않았습니다.",
                        "client_order_id": broker_res.get("client_order_id"),
                        "cash_proxy_sells": proxy_sells,
                        "timestamp": now_str,
                    }
                    self.last_run_result = res
                    self.last_action = f"BROKER_NOT_ACKED_{ticker}"
                    return res
            except Exception as ex_err:
                logger.error(f"[AutoPilot KIS Order Error] {ex_err}")
                broker_status = "FAILED"
                broker_msg = str(ex_err)

        # Step 5: Save to SQLite SSOT (stop = entry * 0.95 / C1-M2 -5%)
        target_price = derive_target_price(exec_price)
        stop_loss_price = derive_stop_price(exec_price)
        partial_tp_price = derive_partial_tp_price(exec_price)
        
        inserted_id = add_portfolio_buy(
            ticker=ticker,
            buy_price=exec_price,
            quantity=float(shares),
            buy_date=now_str[:10],
            target_price=target_price,
            stop_loss_price=stop_loss_price,
            partial_tp_price=partial_tp_price
        )

        trade_record = {
            "timestamp": now_str,
            "holding_id": inserted_id,
            "ticker": ticker,
            "name": ticker_name,
            "shares": shares,
            "buy_price": exec_price,
            "stop_loss_price": stop_loss_price,
            "stop_loss_pct": -HARD_STOP_PCT,
            "slot_weight": sizing.get("slot_weight"),
            "total_usd": round(exec_price * shares, 2),
            "order_id": order_id,
            "broker_status": broker_status,
            "broker_message": broker_msg,
            "cash_proxy_sells": proxy_sells,
            "strategy": "C1-M2 Rank Conviction Entry (50/30/20 + QQQ Proxy)"
        }
        self.trade_logs.append(trade_record)
        self.last_action = f"BOUGHT_{ticker}_{shares}SHARES"

        # Step 6: Broadcast live alerts to WebSockets
        fresh_port = sync_portfolio_prices()
        await hub.broadcast(EventType.AUTOPILOT_BUY, trade_record)
        await hub.broadcast_delta(EventType.POSITION_DELTA, {
            "ticker": ticker,
            "action": "BUY",
            "holding": next(
                (h for h in (fresh_port.get("holdings") or []) if str(h.get("ticker", "")).upper() == ticker.upper()),
                None,
            ),
        })
        await hub.broadcast(EventType.PORTFOLIO_UPDATE, fresh_port)

        res = {
            "status": "success",
            "action": "AUTONOMOUS_BUY_EXECUTED",
            "trade": trade_record,
            "timestamp": now_str
        }
        self.last_run_result = res
        logger.info(f"[AutoPilot Cycle Complete] Successfully executed autonomous buy: {ticker} {shares} shares @ ${exec_price}")
        return res

    def _leverage_mode_now(self) -> bool:
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
            return bool(
                evaluate_dynamic_leverage(
                    spy_close=spy.get("spy_close"),
                    spy_sma200=spy.get("spy_sma200"),
                    vix=vix,
                ).get("leverage_mode")
            )
        except Exception:
            return False

    def _etf_price(self, ticker: str) -> float:
        try:
            if default_kis_broker.is_configured():
                px = default_kis_broker.get_live_price(ticker)
                if px and float(px) > 0:
                    return float(px)
        except Exception:
            pass
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            fi = getattr(t, "fast_info", None)
            if fi:
                p = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                if p and float(p) > 0:
                    return float(p)
        except Exception:
            pass
        return 0.0

    def _ensure_cash_proxy_parked(self, feed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Park idle NAV into QQQ (+ QLD if leverage regime). Planning-only unless auto-buy enabled."""
        port = get_live_portfolio()
        holdings = port.get("holdings", [])
        equity = float(port.get("total_equity_usd") or port.get("total_value") or 7500.0)
        cash = float(port.get("cash_usd") or port.get("cash") or 0.0)
        leverage_on = self._leverage_mode_now()
        qqq_px = self._etf_price(CASH_PROXY_TICKER)
        qld_px = self._etf_price("QLD") if leverage_on else 0.0
        plan = build_cash_proxy_plan(
            total_equity_usd=equity,
            holdings=holdings,
            cash_usd=cash,
            leverage_mode=leverage_on,
            qqq_price=qqq_px,
            qld_price=qld_px,
        )
        executed = []
        if self.is_enabled:
            for act in plan.get("actions") or []:
                if act.get("side") != "BUY":
                    continue
                ticker = act["ticker"]
                qty = int(act["qty"])
                px = qqq_px if ticker == CASH_PROXY_TICKER else qld_px
                if qty <= 0 or px <= 0:
                    continue
                if default_kis_broker.is_configured():
                    try:
                        broker_res = default_kis_broker.place_order(
                            ticker=ticker, side="BUY", qty=qty,
                            price=round(px * 1.005, 2), order_type="00", exchange="NASD",
                        )
                        if not is_broker_order_ack(broker_res.get("status", "error")):
                            continue
                    except Exception as e:
                        logger.error(f"[AutoPilot CashProxy Buy Error] {e}")
                        continue
                add_portfolio_buy(
                    ticker=ticker,
                    buy_price=px,
                    quantity=float(qty),
                    buy_date=datetime.now().strftime("%Y-%m-%d"),
                    target_price=derive_target_price(px),
                    stop_loss_price=derive_stop_price(px),
                    partial_tp_price=derive_partial_tp_price(px),
                )
                executed.append(act)
        plan["executed"] = executed
        return plan

    def _free_proxy_cash_for_entry(
        self, needed_usd: float, holdings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Sell QLD then QQQ to raise cash for a satellite buy."""
        qqq_px = self._etf_price(CASH_PROXY_TICKER)
        qld_px = self._etf_price("QLD")
        plan = raise_cash_from_proxy(
            needed_usd=needed_usd,
            holdings=holdings,
            qqq_price=qqq_px,
            qld_price=qld_px,
        )
        executed: List[Dict[str, Any]] = []
        for act in plan:
            ticker = act["ticker"]
            qty = int(act["qty"])
            px = qqq_px if ticker == CASH_PROXY_TICKER else qld_px
            if qty <= 0 or px <= 0:
                continue
            if default_kis_broker.is_configured():
                try:
                    broker_res = default_kis_broker.place_order(
                        ticker=ticker, side="SELL", qty=qty,
                        price=round(px * 0.995, 2), order_type="00", exchange="NASD",
                    )
                    if not is_broker_order_ack(broker_res.get("status", "error")):
                        continue
                except Exception as e:
                    logger.error(f"[AutoPilot CashProxy Sell Error] {e}")
                    continue
            # Mark local proxy lots sold (best-effort match by ticker)
            for h in list(holdings):
                if str(h.get("ticker", "")).upper() != ticker:
                    continue
                hid = h.get("id")
                if hid is not None:
                    try:
                        record_portfolio_sell(
                            holding_id=int(hid),
                            sell_price=px,
                            reason=f"FREE_CASH_FOR_LEADERSHIP ({act.get('reason')})",
                        )
                    except Exception:
                        pass
                break
            executed.append({**act, "fill_price": px})
            logger.info(f"[AutoPilot] Freed proxy cash: SELL {ticker} x{qty} @ ${px:.2f}")
        return executed


# Global singleton instance
default_autopilot = AutoPilotTrader(check_interval_seconds=60)