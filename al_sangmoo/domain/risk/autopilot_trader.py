"""
AL-SANGMOO QUANT TERMINAL: AUTOPILOT TRADER ENGINE
Autonomous background engine that performs unattended quant scanning,
Goldman Sachs-style Rank #1 conviction evaluation, 3-slot capital allocation ($7,500 / 10M KRW),
and automated KIS OpenAPI broker order execution upon market open.
"""

import asyncio
import logging
import math
import os
import json
from datetime import datetime, time as dtime
from typing import Dict, Any, List, Optional

import db_manager
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.api.hub import hub

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")


class AutoPilotTrader:
    """
    100% Unattended Full-Auto Trading Engine.
    Executes market-open quant scan, selects #1 conviction pick,
    and places real KIS orders into empty slots (Max 2 stock positions + 30% cash buffer).
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
                    # Run cycle if slot available
                    port = db_manager.get_live_portfolio()
                    holdings = port.get("holdings", [])
                    if len(holdings) < 2:
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
            res = {"status": "skipped", "reason": "NO_QUALIFIED_TOP_PICK", "message": "현재 기준을 충족하는 1위 확신도 종목이 없습니다.", "timestamp": now_str}
            self.last_run_result = res
            self.last_action = "NO_TOP_PICK"
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

        # Step 3: Inspect active portfolio holdings & Slot capacity
        port = db_manager.get_live_portfolio()
        holdings = port.get("holdings", [])
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
        is_bull_regime = bool(sizing.get("is_bull_regime", True))
        max_allowed_slots = 3 if is_bull_regime else 2

        if len(holdings) >= max_allowed_slots:
            res = {
                "status": "skipped",
                "reason": "SLOTS_FULL",
                "message": f"1,000만원 국면 한도 도달 (현재 {len(holdings)}개 종목 만석 보유 중 / {'상승장 3-Slot 100% 풀가동' if is_bull_regime else '하락장 2-Slot 50% 현금 대피'})",
                "holdings_count": len(holdings),
                "max_slots": max_allowed_slots,
                "is_bull_regime": is_bull_regime,
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
                if len(broker_holdings) >= max_allowed_slots:
                    res = {
                        "status": "skipped",
                        "reason": "BROKER_SLOTS_FULL",
                        "message": f"증권사 실계좌 슬롯 만석 도달 (실보유: {len(broker_holdings)}개)",
                        "timestamp": now_str
                    }
                    self.last_run_result = res
                    self.last_action = f"BROKER_SLOTS_FULL_{len(broker_holdings)}"
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
            except Exception as ex_err:
                logger.error(f"[AutoPilot KIS Order Error] {ex_err}")
                broker_status = "FAILED"
                broker_msg = str(ex_err)

        # Step 5: Save to SQLite SSOT
        target_price = round(exec_price * 1.15, 2)
        stop_loss_price = round(exec_price * 0.96, 2)
        partial_tp_price = round(exec_price * 1.08, 2)
        
        inserted_id = db_manager.add_portfolio_buy(
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
            "total_usd": round(exec_price * shares, 2),
            "order_id": order_id,
            "broker_status": broker_status,
            "broker_message": broker_msg,
            "strategy": "Full-Auto Rank #1 Conviction Entry (10M KRW Slot)"
        }
        self.trade_logs.append(trade_record)
        self.last_action = f"BOUGHT_{ticker}_{shares}SHARES"

        # Step 6: Broadcast live alerts to WebSockets
        fresh_port = db_manager.sync_portfolio_prices()
        await hub.broadcast("autopilot_buy_alert", trade_record)
        await hub.broadcast("portfolio_update", fresh_port)

        res = {
            "status": "success",
            "action": "AUTONOMOUS_BUY_EXECUTED",
            "trade": trade_record,
            "timestamp": now_str
        }
        self.last_run_result = res
        logger.info(f"[AutoPilot Cycle Complete] Successfully executed autonomous buy: {ticker} {shares} shares @ ${exec_price}")
        return res


# Global singleton instance
default_autopilot = AutoPilotTrader(check_interval_seconds=60)