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

from al_sangmoo.infrastructure.persistence import (
    add_portfolio_buy,
    get_live_portfolio,
    sync_portfolio_prices,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.infrastructure.idempotent_order import is_broker_order_ack
from al_sangmoo.core.constants import (
    derive_partial_tp_price,
    derive_stop_price,
    derive_target_price,
)
from al_sangmoo.domain.risk import exit_cooldown
from al_sangmoo.api.hub import hub, EventType

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")

# Minimum quant conviction required to auto-enter a satellite position.
ENTRY_GATE_MIN_CONVICTION = 60.0


def evaluate_entry_gate(
    candidate: Dict[str, Any],
    kijun_26: float,
    cooldown_tickers: Optional[List[str]] = None,
    min_conviction: float = ENTRY_GATE_MIN_CONVICTION,
) -> Dict[str, Any]:
    """
    Satellite Entry Gate (SSOT). Decides whether a ranked candidate is eligible
    for an autonomous buy. A candidate must clear ALL three absolute conditions:

      1. price >= 26D kijun          -> never buy a ticker already below its
                                        baseline (Guardian would instantly exit it).
      2. conviction/bull >= 60 OR    -> minimum quant conviction.
         sizing.eligible == True
      3. not in re-entry cooldown    -> no same-day re-buy of a force-liquidated name.

    Returns {"eligible": bool, "reason": str, "price": float, "kijun_26": float}.
    A non-positive kijun is treated as "unknown" (the kijun leg is skipped) so a
    transient market-data failure cannot silently block every entry.
    """
    ticker = str(candidate.get("ticker", "")).upper().strip()
    price = float(candidate.get("price") or candidate.get("current_price") or 0.0)
    conviction = float(candidate.get("conviction_score") or 0.0)
    bull = float(candidate.get("bull_score") or 0.0)
    sizing = candidate.get("sizing") or {}
    eligible_flag = bool(sizing.get("eligible", False))
    kijun = float(
        kijun_26
        or candidate.get("kijun")
        or candidate.get("kijun_26")
        or 0.0
    )
    cooldowns = {str(t).upper().strip() for t in (cooldown_tickers or [])}

    result = {
        "eligible": False,
        "reason": "PASS",
        "ticker": ticker,
        "price": round(price, 4),
        "kijun_26": round(kijun, 4),
    }

    if not ticker or price <= 0:
        result["reason"] = "INVALID_PRICE"
        return result

    if ticker in cooldowns:
        result["reason"] = "RE_ENTRY_COOLDOWN"
        return result

    if kijun > 0 and price < kijun:
        result["reason"] = "BELOW_KIJUN"
        return result

    if not (conviction >= min_conviction or bull >= min_conviction or eligible_flag):
        result["reason"] = "LOW_CONVICTION"
        return result

    result["eligible"] = True
    return result


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
                    port = get_live_portfolio()
                    holdings = port.get("holdings", [])
                    if len(holdings) < 2:
                        await self.run_autopilot_cycle(force_scan=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AutoPilot Loop Error] {e}", exc_info=True)

            await asyncio.sleep(self.interval)

    def _resolve_kijun(self, candidate: Dict[str, Any]) -> float:
        """
        Resolve the candidate's 26D kijun. Prefer the value already carried in the
        feed; fall back to a live trailing snapshot (same source the Guardian uses)
        so the Entry Gate and the exit engine agree on the baseline.
        """
        kijun = float(candidate.get("kijun") or candidate.get("kijun_26") or 0.0)
        if kijun > 0:
            return kijun
        ticker = str(candidate.get("ticker", "")).upper().strip()
        if not ticker:
            return 0.0
        try:
            from al_sangmoo.domain.risk.trailing_stop import fetch_trailing_snapshot
            snap = fetch_trailing_snapshot(ticker)
            return float(snap.get("kijun_26") or 0.0)
        except Exception as exc:
            logger.debug(f"[AutoPilot Entry Gate] kijun snapshot failed for {ticker}: {exc}")
            return 0.0

    def _next_unheld_candidate(
        self,
        feed: Dict[str, Any],
        active_tickers: List[str],
        broker_tickers: Optional[List[str]] = None,
        cooldown_tickers: Optional[List[str]] = None,
    ):
        """
        Walk the ranked conviction list and return the first candidate that is not
        already held AND passes the Entry Gate. Ineligible candidates (below kijun,
        low conviction, or in re-entry cooldown) are skipped, never bought.

        Returns (candidate, gate_result) or (None, {"reason": ...}).
        """
        ranked = feed.get("ranked_conviction_list") or []
        if not ranked:
            top = feed.get("top_conviction_pick")
            ranked = [top] if top else []

        held = {str(t).upper().strip() for t in (active_tickers or [])}
        held |= {str(t).upper().strip() for t in (broker_tickers or [])}
        cooldowns = list(cooldown_tickers or [])

        skipped: List[Dict[str, Any]] = []
        for cand in ranked:
            if not cand:
                continue
            ticker = str(cand.get("ticker", "")).upper().strip()
            if not ticker or ticker in held:
                continue
            kijun = self._resolve_kijun(cand)
            gate = evaluate_entry_gate(cand, kijun, cooldowns)
            if gate["eligible"]:
                return cand, gate
            skipped.append({"ticker": ticker, "reason": gate["reason"]})
            logger.info(
                f"[AutoPilot Entry Gate] SKIP {ticker}: {gate['reason']} "
                f"(price=${gate['price']}, kijun=${gate['kijun_26']})"
            )

        return None, {"reason": "NO_ELIGIBLE_CANDIDATE", "skipped": skipped}

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
        slot_summary = feed.get("slot_allocation_summary") or {}
        if not top_pick and not (feed.get("ranked_conviction_list") or []):
            res = {"status": "skipped", "reason": "NO_QUALIFIED_TOP_PICK", "message": "현재 기준을 충족하는 1위 확신도 종목이 없습니다.", "timestamp": now_str}
            self.last_run_result = res
            self.last_action = "NO_TOP_PICK"
            return res

        # Step 3: Inspect active portfolio holdings & Slot capacity
        port = get_live_portfolio()
        holdings = port.get("holdings", [])
        active_tickers = [str(h["ticker"]).upper() for h in holdings]

        # Dynamic Regime Sizing (Max 3 slots in Bull, Max 2 in Bear)
        is_bull_regime = bool(
            (top_pick or {}).get("sizing", {}).get("is_bull_regime",
                                                    slot_summary.get("is_bull_regime", True))
        )
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

        # Guardrail: Direct Broker SSOT Verification (Physical protection against duplicate buys)
        broker_tickers: List[str] = []
        if default_kis_broker.is_configured():
            try:
                broker_bal = default_kis_broker.get_overseas_balance()
                broker_holdings = broker_bal.get("holdings", [])
                broker_tickers = [str(bh.get("ticker", "")).upper() for bh in broker_holdings]
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

        # Entry Gate: select first ranked candidate that is unheld AND clears the
        # kijun / conviction / re-entry-cooldown gates. Below-baseline or cooling-down
        # names are skipped, never bought (root-cause fix for the DIS whipsaw).
        cooldowns = exit_cooldown.cooldown_tickers()
        candidate, gate = self._next_unheld_candidate(
            feed, active_tickers, broker_tickers, cooldowns
        )
        if candidate is None:
            res = {
                "status": "skipped",
                "reason": "NO_ELIGIBLE_CANDIDATE",
                "message": "진입 적격성(기준선 상회·최소 확신도·재진입 쿨다운)을 통과한 후보가 없습니다.",
                "skipped": gate.get("skipped", []),
                "cooldown_tickers": cooldowns,
                "timestamp": now_str
            }
            self.last_run_result = res
            self.last_action = "NO_ELIGIBLE_CANDIDATE"
            return res

        ticker = str(candidate.get("ticker", "")).upper()
        ticker_name = candidate.get("name", ticker)
        sizing = candidate.get("sizing", {})
        shares = int(sizing.get("shares", 0))
        cur_price = float(candidate.get("price", 0.0))

        if not sizing.get("eligible", False) or shares <= 0 or cur_price <= 0:
            res = {"status": "skipped", "reason": "SIZING_NOT_ELIGIBLE", "message": f"{ticker} 슬롯 자본금 부족 또는 유효하지 않은 수량", "timestamp": now_str}
            self.last_run_result = res
            self.last_action = f"SIZING_INELIGIBLE_{ticker}"
            return res

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
                if not is_broker_order_ack(broker_status):
                    res = {
                        "status": "skipped",
                        "reason": str(broker_status or "BROKER_ORDER_NOT_ACKED"),
                        "ticker": ticker,
                        "message": broker_msg or "증권사 주문 확인 실패. 로컬 원장에 매수를 기록하지 않았습니다.",
                        "client_order_id": broker_res.get("client_order_id"),
                        "timestamp": now_str,
                    }
                    self.last_run_result = res
                    self.last_action = f"BROKER_NOT_ACKED_{ticker}"
                    return res
            except Exception as ex_err:
                logger.error(f"[AutoPilot KIS Order Error] {ex_err}")
                broker_status = "FAILED"
                broker_msg = str(ex_err)

        # Step 5: Save to SQLite SSOT
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
            "total_usd": round(exec_price * shares, 2),
            "order_id": order_id,
            "broker_status": broker_status,
            "broker_message": broker_msg,
            "strategy": "Full-Auto Rank #1 Conviction Entry (10M KRW Slot)"
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


# Global singleton instance
default_autopilot = AutoPilotTrader(check_interval_seconds=60)