"""
AL-SANGMOO QUANT TERMINAL: AUTOPILOT TRADER ENGINE
C1-M2 multi-slot conviction entries (50/30/20 bull · 25/25 bear),
residual idle-NAV QQQ(+QLD) cash-proxy parking, and KIS OpenAPI execution.
"""

import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Set

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
    SLOT_WEIGHTS_BEAR,
    SLOT_WEIGHTS_BULL,
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
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")


class AutoPilotTrader:
    """
    100% Unattended Full-Auto Trading Engine (C1-M2).
    Fills empty satellite slots sequentially from ranked_conviction_list
    (50/30/20 or 25/25), frees cash from QQQ/QLD when needed, then parks
    any residual idle NAV back into the cash-proxy sleeve.
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
        C1-M2 multi-slot cycle:
          1. Fresh scan (optional) → load dashboard feed
          2. Walk ranked_conviction_list; skip held tickers; fill empty slots
             with next-slot weights (bull 50/30/20, bear 25/25)
          3. Free QQQ/QLD proxy cash when entry cash is short
          4. Always park residual idle NAV into QQQ(+QLD) before return
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_run_time = now_str
        logger.info(
            f"[AutoPilot Cycle] Multi-slot C1-M2 cycle at {now_str} (Force Scan: {force_scan})"
        )

        if force_scan or not os.path.exists(DASHBOARD_JSON):
            try:
                from generate_dashboard_feed import build_dashboard_data
                await asyncio.to_thread(build_dashboard_data)
                logger.info("[AutoPilot Cycle] Fresh 60-ticker universe scan completed successfully")
            except Exception as scan_err:
                logger.error(f"[AutoPilot Scan Error] {scan_err}")

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

        candidates = self._collect_ranked_candidates(feed)
        is_bull_regime = self._resolve_bull_regime(feed, candidates)
        max_allowed_slots = MAX_SLOTS_BULL if is_bull_regime else MAX_SLOTS_BEAR
        weights = list(SLOT_WEIGHTS_BULL if is_bull_regime else SLOT_WEIGHTS_BEAR)

        entries: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        considered: Set[str] = set()
        # Virtual slot cursor for Auto-Buy OFF planning logs (ledger unchanged)
        planning_slot_rank: Optional[int] = None

        if not candidates:
            proxy_note = self._ensure_cash_proxy_parked(feed)
            res = {
                "status": "skipped",
                "reason": "NO_QUALIFIED_TOP_PICK",
                "message": "적격 주도주 후보가 없습니다. 유휴 자본은 QQQ Cash Proxy에 파킹합니다.",
                "cash_proxy": proxy_note,
                "is_bull_regime": is_bull_regime,
                "max_slots": max_allowed_slots,
                "entries": entries,
                "skipped": skipped,
                "timestamp": now_str,
            }
            self.last_run_result = res
            self.last_action = "NO_TOP_PICK_QQQ_PARK"
            return res

        # Multi-slot fill: keep buying next unheld ranked name until slots full
        while True:
            port = get_live_portfolio()
            holdings = port.get("holdings", [])
            sats = satellite_holdings(holdings)
            held = {str(h.get("ticker", "")).upper() for h in holdings}

            if len(sats) >= max_allowed_slots:
                skipped.append({
                    "reason": "SLOTS_FULL",
                    "message": (
                        f"위성 슬롯 만석 ({len(sats)}/{max_allowed_slots} / "
                        f"{'상승장 50/30/20' if is_bull_regime else '하락장 25/25'})"
                    ),
                    "holdings_count": len(sats),
                    "max_slots": max_allowed_slots,
                })
                break

            live_slot_rank = len(sats) + 1
            if planning_slot_rank is None:
                planning_slot_rank = live_slot_rank
            next_slot_rank = live_slot_rank if self.is_enabled else int(planning_slot_rank)

            if next_slot_rank > max_allowed_slots:
                skipped.append({
                    "reason": "SLOTS_FULL",
                    "message": f"계획 슬롯 한도 도달 ({max_allowed_slots})",
                    "max_slots": max_allowed_slots,
                })
                break

            pick = self._next_unheld_candidate(candidates, held, considered)
            if pick is None:
                skipped.append({
                    "reason": "NO_MORE_UNHELD_CANDIDATES",
                    "message": (
                        f"빈 슬롯 #{next_slot_rank} 남음 — 미보유 적격 주도주 소진. "
                        "잔여 유휴 NAV는 Cash Proxy로 파킹합니다."
                    ),
                    "empty_slots": max_allowed_slots - len(sats),
                    "slot_weight": weights[next_slot_rank - 1] if next_slot_rank <= len(weights) else None,
                })
                break

            ticker = str(pick.get("ticker", "")).upper()
            considered.add(ticker)

            # Broker SSOT: skip if already on the street book
            broker_block = self._broker_slot_guard(ticker, max_allowed_slots)
            if broker_block:
                skipped.append(broker_block)
                if broker_block.get("reason") in ("BROKER_SLOTS_FULL",):
                    break
                continue

            equity = float(
                port.get("total_equity_usd")
                or port.get("total_value")
                or pick.get("sizing", {}).get("remaining_cash_usd")
                or 7500.0
            )
            cur_price = float(pick.get("price", 0.0) or 0.0)
            sizing = calculate_target_shares(
                portfolio_nav=equity,
                current_price=cur_price,
                slot_rank=next_slot_rank,
                is_bull=is_bull_regime,
            )
            pick = dict(pick)
            pick["sizing"] = sizing
            pick["slot_rank"] = next_slot_rank
            pick["slot_weight"] = sizing.get("slot_weight")

            if not sizing.get("eligible", False) or int(sizing.get("shares", 0) or 0) <= 0 or cur_price <= 0:
                skipped.append({
                    "status": "skipped",
                    "reason": "SIZING_NOT_ELIGIBLE",
                    "ticker": ticker,
                    "slot_rank": next_slot_rank,
                    "message": sizing.get("reason") or f"{ticker} 슬롯 자본금 부족 또는 유효하지 않은 수량",
                })
                continue

            if not self.is_enabled:
                skipped.append({
                    "status": "skipped",
                    "reason": "AUTO_BUY_DISABLED",
                    "ticker": ticker,
                    "slot_rank": next_slot_rank,
                    "slot_weight": sizing.get("slot_weight"),
                    "shares": int(sizing.get("shares", 0)),
                    "message": (
                        f"전자동 매수 OFF — 슬롯#{next_slot_rank} "
                        f"({float(sizing.get('slot_weight') or 0)*100:.0f}%) "
                        f"{ticker} {int(sizing.get('shares', 0))}주 시뮬레이션 스킵"
                    ),
                })
                planning_slot_rank = int(planning_slot_rank) + 1
                continue

            entry_res = await self._execute_satellite_entry(
                pick=pick,
                slot_rank=next_slot_rank,
                holdings=holdings,
                now_str=now_str,
            )
            if entry_res.get("status") == "success":
                entries.append(entry_res)
            else:
                skipped.append(entry_res)
                # Hard broker ack failure: try next candidate for remaining slots
                continue

        proxy_note = self._ensure_cash_proxy_parked(feed)

        if entries:
            status = "success"
            reason = "MULTI_SLOT_ENTRIES"
            action = f"BOUGHT_{len(entries)}_SLOTS"
            message = f"C1-M2 멀티 슬롯 진입 {len(entries)}건 체결. 잔여 유휴분 Cash Proxy 점검 완료."
        elif any(s.get("reason") == "AUTO_BUY_DISABLED" for s in skipped):
            status = "skipped"
            reason = "AUTO_BUY_DISABLED"
            action = "AUTO_BUY_PAUSED_MULTI_SLOT"
            message = "전자동 매수 OFF — 슬롯 후보 시뮬레이션만 수행. 유휴분은 Cash Proxy 계획에 반영."
        elif any(s.get("reason") == "SLOTS_FULL" for s in skipped) and not entries:
            status = "skipped"
            reason = "SLOTS_FULL"
            action = f"SLOTS_FULL_MAX_{max_allowed_slots}"
            message = skipped[0].get("message", "슬롯 만석")
        else:
            status = "skipped"
            reason = (skipped[0].get("reason") if skipped else "NO_ACTION")
            action = f"SKIPPED_{reason}"
            message = (
                skipped[0].get("message")
                if skipped
                else "진입 없음. 잔여 유휴분은 Cash Proxy에 파킹합니다."
            )

        res = {
            "status": status,
            "reason": reason,
            "action": "MULTI_SLOT_CYCLE",
            "message": message,
            "is_bull_regime": is_bull_regime,
            "max_slots": max_allowed_slots,
            "slot_weights": weights,
            "entries": entries,
            "skipped": skipped,
            "cash_proxy": proxy_note,
            "timestamp": now_str,
        }
        # Backward-compat single-trade field when exactly one buy landed
        if len(entries) == 1 and entries[0].get("trade"):
            res["trade"] = entries[0]["trade"]
        self.last_run_result = res
        self.last_action = action
        logger.info(
            f"[AutoPilot Cycle Complete] status={status} entries={len(entries)} "
            f"skipped={len(skipped)} idle_proxy={proxy_note.get('idle_nav_usd')}"
        )
        return res

    def _collect_ranked_candidates(self, feed: Dict[str, Any]) -> List[Dict[str, Any]]:
        ranked = feed.get("ranked_conviction_list") or []
        if isinstance(ranked, list) and ranked:
            return [c for c in ranked if isinstance(c, dict) and c.get("ticker")]
        picks: List[Dict[str, Any]] = []
        for key in ("top_conviction_pick", "top_conviction_runner_up"):
            item = feed.get(key)
            if isinstance(item, dict) and item.get("ticker"):
                picks.append(item)
        return picks

    def _resolve_bull_regime(
        self, feed: Dict[str, Any], candidates: List[Dict[str, Any]]
    ) -> bool:
        if candidates:
            sizing = candidates[0].get("sizing") or {}
            if "is_bull_regime" in sizing:
                return bool(sizing.get("is_bull_regime"))
        slot_summary = feed.get("slot_allocation_summary") or {}
        if "is_bull_regime" in slot_summary:
            return bool(slot_summary.get("is_bull_regime"))
        macro = feed.get("macro") or {}
        if "is_bull_regime" in macro:
            return bool(macro.get("is_bull_regime"))
        return True

    def _next_unheld_candidate(
        self,
        candidates: List[Dict[str, Any]],
        held: Set[str],
        considered: Set[str],
    ) -> Optional[Dict[str, Any]]:
        for pick in candidates:
            ticker = str(pick.get("ticker", "")).upper()
            if not ticker or ticker in held or ticker in considered or is_proxy_ticker(ticker):
                continue
            return pick
        return None

    def _broker_slot_guard(self, ticker: str, max_allowed_slots: int) -> Optional[Dict[str, Any]]:
        if not default_kis_broker.is_configured():
            return None
        try:
            broker_bal = default_kis_broker.get_overseas_balance()
            broker_holdings = broker_bal.get("holdings", [])
            broker_sats = [bh for bh in broker_holdings if not is_proxy_ticker(str(bh.get("ticker", "")))]
            broker_tickers = [str(bh.get("ticker", "")).upper() for bh in broker_holdings]
            if ticker in broker_tickers:
                return {
                    "status": "skipped",
                    "reason": "ALREADY_IN_BROKER_HOLDINGS",
                    "ticker": ticker,
                    "message": f"증권사 실계좌에 {ticker} 종목이 이미 체결되어 보유 중입니다.",
                }
            if len(broker_sats) >= max_allowed_slots:
                return {
                    "status": "skipped",
                    "reason": "BROKER_SLOTS_FULL",
                    "message": f"증권사 실계좌 위성 슬롯 만석 도달 (실보유 위성: {len(broker_sats)}개)",
                }
        except Exception as b_err:
            logger.warning(f"[AutoPilot Broker Guardrail Error] {b_err}")
        return None

    async def _execute_satellite_entry(
        self,
        pick: Dict[str, Any],
        slot_rank: int,
        holdings: List[Dict[str, Any]],
        now_str: str,
    ) -> Dict[str, Any]:
        """Place one satellite buy after freeing proxy cash if needed."""
        ticker = str(pick.get("ticker", "")).upper()
        ticker_name = pick.get("name", ticker)
        sizing = pick.get("sizing") or {}
        shares = int(sizing.get("shares", 0) or 0)
        cur_price = float(pick.get("price", 0.0) or 0.0)

        live_price = None
        if default_kis_broker.is_configured():
            try:
                live_price = default_kis_broker.get_live_price(ticker)
            except Exception:
                pass
        exec_price = live_price if live_price and live_price > 0 else cur_price
        needed_usd = float(shares) * float(exec_price)

        # Free only the cash shortfall from QQQ/QLD (not the full notional if cash exists)
        port_cash = 0.0
        try:
            port_now = get_live_portfolio()
            port_cash = float(port_now.get("cash_usd") or port_now.get("cash") or 0.0)
        except Exception:
            port_cash = 0.0
        shortfall = max(0.0, needed_usd - max(0.0, port_cash))
        proxy_sells = self._free_proxy_cash_for_entry(shortfall, holdings) if shortfall > 0 else []
        marketable_exec_price = round(exec_price * 1.005, 2)

        order_id = None
        broker_status = "SIMULATED"
        broker_msg = "시뮬레이션 모드 매수 접수"

        if default_kis_broker.is_configured():
            try:
                ex_cd = "NYSE" if ticker in ["CVX", "JNJ", "XOM", "UNH", "PG", "JPM", "V", "MA", "LLY"] else "NASD"
                broker_res = default_kis_broker.place_order(
                    ticker=ticker,
                    side="BUY",
                    qty=shares,
                    price=marketable_exec_price,
                    order_type="00",
                    exchange=ex_cd,
                )
                order_id = broker_res.get("order_id")
                broker_status = broker_res.get("status", "error")
                broker_msg = broker_res.get("broker_message", broker_res.get("message", ""))
                if not is_broker_order_ack(broker_status):
                    return {
                        "status": "skipped",
                        "reason": str(broker_status or "BROKER_ORDER_NOT_ACKED"),
                        "ticker": ticker,
                        "slot_rank": slot_rank,
                        "message": broker_msg or "증권사 주문 확인 실패. 로컬 원장에 매수를 기록하지 않았습니다.",
                        "client_order_id": broker_res.get("client_order_id"),
                        "cash_proxy_sells": proxy_sells,
                    }
            except Exception as ex_err:
                logger.error(f"[AutoPilot KIS Order Error] {ex_err}")
                return {
                    "status": "skipped",
                    "reason": "BROKER_ORDER_FAILED",
                    "ticker": ticker,
                    "slot_rank": slot_rank,
                    "message": str(ex_err),
                    "cash_proxy_sells": proxy_sells,
                }

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
            partial_tp_price=partial_tp_price,
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
            "slot_rank": slot_rank,
            "slot_weight": sizing.get("slot_weight"),
            "total_usd": round(exec_price * shares, 2),
            "order_id": order_id,
            "broker_status": broker_status,
            "broker_message": broker_msg,
            "cash_proxy_sells": proxy_sells,
            "strategy": f"C1-M2 Multi-Slot Entry #{slot_rank} (50/30/20 + QQQ Proxy)",
        }
        self.trade_logs.append(trade_record)
        self.last_action = f"BOUGHT_{ticker}_{shares}SHARES_SLOT{slot_rank}"

        fresh_port = sync_portfolio_prices()
        await hub.broadcast(EventType.AUTOPILOT_BUY, trade_record)
        await hub.broadcast_delta(EventType.POSITION_DELTA, {
            "ticker": ticker,
            "action": "BUY",
            "holding": next(
                (
                    h for h in (fresh_port.get("holdings") or [])
                    if str(h.get("ticker", "")).upper() == ticker
                ),
                None,
            ),
        })
        await hub.broadcast(EventType.PORTFOLIO_UPDATE, fresh_port)

        logger.info(
            f"[AutoPilot] Slot#{slot_rank} buy: {ticker} {shares} @ ${exec_price} "
            f"(weight={sizing.get('slot_weight')})"
        )
        return {
            "status": "success",
            "action": "AUTONOMOUS_BUY_EXECUTED",
            "ticker": ticker,
            "slot_rank": slot_rank,
            "trade": trade_record,
            "timestamp": now_str,
        }

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