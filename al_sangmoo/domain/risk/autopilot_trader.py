"""
AL-SANGMOO QUANT TERMINAL: AUTOPILOT TRADER ENGINE
C1-M2 multi-slot conviction entries (50/30/20 bull · 25/25 bear),
residual idle-NAV QQQ(+QLD) cash-proxy parking, and KIS OpenAPI execution.
"""

import asyncio
import logging
import os
import json
import re
import hashlib
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Tuple

from al_sangmoo.infrastructure.persistence import (
    add_portfolio_buy,
    claim_autopilot_entry_session,
    claim_proxy_order_intent,
    clear_proxy_order_intent,
    get_live_portfolio,
    has_autopilot_entry_session,
    mark_proxy_order_submitted,
    record_portfolio_ticker_sell_quantity,
    sync_portfolio_prices,
)
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.infrastructure.idempotent_order import (
    is_broker_order_ack,
    make_client_order_id,
)
from al_sangmoo.core.market_time import is_us_regular_hours, now_us_eastern
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
from al_sangmoo.domain.risk import exit_cooldown
from al_sangmoo.api.hub import hub, EventType
from al_sangmoo.domain.risk.cash_proxy import (
    build_cash_proxy_plan,
    is_proxy_ticker,
    raise_cash_from_proxy,
    satellite_holdings,
    serialized_proxy_orders,
)
from al_sangmoo.domain.risk.macro_guardrail import evaluate_dynamic_leverage
from al_sangmoo.domain.quant.macro import fetch_spy_trend_regime
from al_sangmoo.domain.risk.position_sizer import calculate_target_shares

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_JSON = os.path.join(BASE_DIR, "dashboard_data.json")
CONFIRMED_EOD_JSON = os.path.join(BASE_DIR, "dashboard_data.eod.json")

# Minimum quant conviction required to auto-enter a satellite position.
ENTRY_GATE_MIN_CONVICTION = 60.0
# Previous-session EOD signals may open new satellites once, near the next open.
ENTRY_WINDOW_START_MINUTE_ET = 9 * 60 + 30
ENTRY_WINDOW_END_MINUTE_ET = 10 * 60
MAX_EOD_SIGNAL_AGE_DAYS = 4


def evaluate_daily_entry_schedule(now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Enforce the live equivalent of the backtest's previous-EOD -> next-open entry.

    New satellite entries are allowed only from 09:30 through 09:59 ET, once per
    US session. Any satellite exit recorded for that session blocks all refill
    entries; cash-proxy parking remains independent.
    """
    now_et = now_us_eastern(now)
    session_date = now_et.strftime("%Y-%m-%d")
    result: Dict[str, Any] = {
        "eligible": False,
        "reason": "OUTSIDE_DAILY_ENTRY_WINDOW",
        "session_date": session_date,
        "now_et": now_et.isoformat(),
        "window_et": "09:30-10:00",
    }
    if now_et.weekday() >= 5:
        result["reason"] = "NON_TRADING_DAY"
        return result
    minute = now_et.hour * 60 + now_et.minute
    if not (ENTRY_WINDOW_START_MINUTE_ET <= minute < ENTRY_WINDOW_END_MINUTE_ET):
        return result
    if exit_cooldown.has_session_exit(session_date):
        result["reason"] = "SESSION_EXIT_LOCKOUT"
        return result
    if has_autopilot_entry_session(session_date):
        result["reason"] = "DAILY_ENTRY_ALREADY_CLAIMED"
        return result
    result["eligible"] = True
    result["reason"] = "PASS"
    return result


def validate_confirmed_eod_feed(
    feed: Dict[str, Any],
    entry_session_date: str,
) -> Dict[str, Any]:
    """Require a feed stamped from a completed session before today's entry."""
    signal_day = str(feed.get("signal_session_date") or "").strip()
    result = {
        "eligible": False,
        "reason": "EOD_FEED_UNVERIFIED",
        "signal_session_date": signal_day or None,
        "entry_session_date": entry_session_date,
    }
    if "signal_is_eod_confirmed" not in feed:
        return result
    if feed.get("signal_is_eod_confirmed") is not True:
        result["reason"] = "EOD_FEED_UNCONFIRMED"
        return result
    source_hash = str(feed.get("signal_source_hash") or "")
    source_rows = feed.get("signal_source_rows")
    if not re.fullmatch(
        r"[0-9a-f]{64}",
        source_hash,
    ):
        result["reason"] = "EOD_SOURCE_PROVENANCE_INVALID"
        return result
    if (
        not isinstance(source_rows, list)
        or not source_rows
        or not all(isinstance(row, dict) for row in source_rows)
    ):
        result["reason"] = "EOD_SOURCE_PROVENANCE_INVALID"
        return result
    canonical = json.dumps(
        sorted(source_rows, key=lambda row: str(row.get("ticker") or "")),
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != source_hash:
        result["reason"] = "EOD_SOURCE_PROVENANCE_INVALID"
        return result
    if any(
        str(row.get("bar_date") or "") != signal_day
        for row in source_rows
        if isinstance(row, dict)
    ):
        result["reason"] = "EOD_SOURCE_PROVENANCE_INVALID"
        return result
    try:
        signal_dt = datetime.strptime(signal_day, "%Y-%m-%d").date()
        entry_dt = datetime.strptime(entry_session_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return result
    age_days = (entry_dt - signal_dt).days
    result["age_days"] = age_days
    if age_days <= 0:
        result["reason"] = "INTRADAY_DAILY_BAR_NOT_CONFIRMED"
        return result
    if age_days > MAX_EOD_SIGNAL_AGE_DAYS:
        result["reason"] = "STALE_EOD_FEED"
        return result
    result["eligible"] = True
    result["reason"] = "PASS"
    return result


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
        self._pending_proxy_orders: Dict[str, float] = {}
        self._pending_order_ttl_sec: float = 300.0

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
            "recent_trades": self.trade_logs[-5:],
            "entry_policy": {
                "signal": "PREVIOUS_SESSION_EOD",
                "window_et": "09:30-10:00",
                "max_cycles_per_session": 1,
                "same_session_refill": False,
                "ticker_cooldown_hours": exit_cooldown.RE_ENTRY_COOLDOWN_SEC / 3600.0,
            },
        }

    async def _scheduler_loop(self):
        """
        DST-aware US regular-session loop. Satellite entry is attempted once
        during 09:30-10:00 ET; cash-proxy alignment remains intraday-capable.
        """
        while self.is_running:
            try:
                # Entries: previous-session EOD signal, once in the next-open window.
                # Cash-proxy alignment may continue during regular hours.
                if self.is_enabled and is_us_regular_hours():
                    schedule = evaluate_daily_entry_schedule()
                    if schedule.get("eligible"):
                        # Claiming happens inside the cycle, even when slots are full,
                        # so a later same-session exit cannot reopen the entry window.
                        await self.run_autopilot_cycle(
                            force_scan=False,
                            enforce_entry_schedule=True,
                        )
                    else:
                        await asyncio.to_thread(self._ensure_cash_proxy_parked)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AutoPilot Loop Error] {e}", exc_info=True)

            await asyncio.sleep(self.interval)

    async def run_autopilot_cycle(
        self,
        force_scan: bool = True,
        enforce_entry_schedule: bool = True,
    ) -> Dict[str, Any]:
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

        if enforce_entry_schedule:
            if force_scan:
                res = {
                    "status": "skipped",
                    "reason": "INTRADAY_RESCAN_FORBIDDEN",
                    "message": (
                        "장중 일봉 재스캔은 확정되지 않은 캔들을 사용할 수 있어 금지됩니다. "
                        "전일 EOD 피드를 사용하세요."
                    ),
                    "cash_proxy": {"executed": []},
                    "timestamp": now_str,
                }
                self.last_run_result = res
                self.last_action = "INTRADAY_RESCAN_FORBIDDEN"
                return res
            schedule = evaluate_daily_entry_schedule()
            if not schedule.get("eligible"):
                res = {
                    "status": "skipped",
                    "reason": schedule.get("reason"),
                    "message": (
                        "위성 신규 진입은 전일 EOD 신호로 미국장 09:30~10:00 ET에 "
                        "세션당 1회만 허용됩니다. 유휴 자본은 Cash Proxy 정책이 담당합니다."
                    ),
                    "entry_schedule": schedule,
                    "cash_proxy": {"executed": []},
                    "timestamp": now_str,
                }
                self.last_run_result = res
                self.last_action = str(schedule.get("reason"))
                return res
            claimed = claim_autopilot_entry_session(
                str(schedule["session_date"]),
                source="AUTOPILOT",
                claimed_at=now_str,
            )
            if not claimed:
                schedule["eligible"] = False
                schedule["reason"] = "DAILY_ENTRY_ALREADY_CLAIMED"
                res = {
                    "status": "skipped",
                    "reason": "DAILY_ENTRY_ALREADY_CLAIMED",
                    "message": "오늘의 위성 진입 사이클이 이미 실행 또는 선점되었습니다.",
                    "entry_schedule": schedule,
                    "cash_proxy": {"executed": []},
                    "timestamp": now_str,
                }
                self.last_run_result = res
                self.last_action = "DAILY_ENTRY_ALREADY_CLAIMED"
                return res
            if exit_cooldown.has_session_exit(str(schedule["session_date"])):
                res = {
                    "status": "skipped",
                    "reason": "SESSION_EXIT_LOCKOUT",
                    "message": "진입 세션 선점 중 청산이 감지되어 당일 위성 보충을 차단했습니다.",
                    "entry_schedule": schedule,
                    "cash_proxy": {"executed": []},
                    "timestamp": now_str,
                }
                self.last_run_result = res
                self.last_action = "SESSION_EXIT_LOCKOUT"
                return res

        entry_feed_path = (
            CONFIRMED_EOD_JSON
            if enforce_entry_schedule and os.path.exists(CONFIRMED_EOD_JSON)
            else DASHBOARD_JSON
        )
        if enforce_entry_schedule and not os.path.exists(entry_feed_path):
            res = {
                "status": "skipped",
                "reason": "CONFIRMED_EOD_FEED_MISSING",
                "message": "확정된 전일 EOD 피드가 없어 오늘의 위성 진입을 건너뜁니다.",
                "cash_proxy": {"executed": []},
                "timestamp": now_str,
            }
            self.last_run_result = res
            self.last_action = "CONFIRMED_EOD_FEED_MISSING"
            return res

        if force_scan or (not os.path.exists(DASHBOARD_JSON) and not enforce_entry_schedule):
            try:
                from generate_dashboard_feed import build_dashboard_data
                await asyncio.to_thread(build_dashboard_data)
                logger.info("[AutoPilot Cycle] Fresh 60-ticker universe scan completed successfully")
            except Exception as scan_err:
                logger.error(f"[AutoPilot Scan Error] {scan_err}")

        if not os.path.exists(entry_feed_path):
            res = {"status": "error", "message": "Dashboard feed not available", "timestamp": now_str}
            self.last_run_result = res
            return res

        try:
            with open(entry_feed_path, "r", encoding="utf-8") as f:
                feed = json.load(f)
        except Exception as e:
            res = {"status": "error", "message": f"Failed to read feed: {e}", "timestamp": now_str}
            self.last_run_result = res
            return res

        if enforce_entry_schedule:
            feed_gate = validate_confirmed_eod_feed(
                feed,
                str(schedule["session_date"]),
            )
            if not feed_gate.get("eligible"):
                res = {
                    "status": "skipped",
                    "reason": feed_gate.get("reason"),
                    "message": (
                        "전일 종가가 확정된 일봉 피드만 신규 위성 진입에 사용할 수 있습니다."
                    ),
                    "entry_schedule": schedule,
                    "feed_gate": feed_gate,
                    "cash_proxy": {"executed": []},
                    "timestamp": now_str,
                }
                self.last_run_result = res
                self.last_action = str(feed_gate.get("reason"))
                return res

        candidates = self._collect_ranked_candidates(feed)
        is_bull_regime = self._resolve_bull_regime(feed, candidates)
        max_allowed_slots = MAX_SLOTS_BULL if is_bull_regime else MAX_SLOTS_BEAR
        weights = list(SLOT_WEIGHTS_BULL if is_bull_regime else SLOT_WEIGHTS_BEAR)

        entries: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        considered: Set[str] = set()
        pending_entry: Optional[Dict[str, Any]] = None
        broker_snapshot_block: Optional[Dict[str, Any]] = None
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

            pick, gate = self._next_unheld_candidate(candidates, held=held, considered=considered)
            if pick is None:
                skipped.append({
                    "reason": "NO_ELIGIBLE_CANDIDATE",
                    "message": (
                        f"빈 슬롯 #{next_slot_rank} 남음 — 미보유 적격 주도주 소진. "
                        "잔여 유휴 NAV는 Cash Proxy로 파킹합니다."
                    ),
                    "empty_slots": max_allowed_slots - len(sats),
                    "slot_weight": weights[next_slot_rank - 1] if next_slot_rank <= len(weights) else None,
                    "gate": gate,
                })
                break

            ticker = str(pick.get("ticker", "")).upper()
            considered.add(ticker)

            # Broker SSOT: skip if already on the street book
            broker_block = self._broker_slot_guard(ticker, max_allowed_slots)
            if broker_block:
                skipped.append(broker_block)
                if broker_block.get("reason") in {
                    "BROKER_SLOTS_FULL",
                    "BROKER_SNAPSHOT_UNVERIFIED",
                }:
                    if broker_block.get("reason") == "BROKER_SNAPSHOT_UNVERIFIED":
                        broker_snapshot_block = broker_block
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
                if (
                    entry_res.get("status") == "submitted"
                    or entry_res.get("reason") in {
                        "BROKER_BUY_AWAITING_RECONCILIATION",
                        "BROKER_BUY_PENDING",
                        "CASH_PROXY_SELL_PENDING",
                    }
                ):
                    # Local holdings/cash are intentionally unchanged until the
                    # broker fill is reconciled. Submitting another candidate or
                    # parking proxy now can over-allocate the same slot/cash.
                    pending_entry = entry_res
                    break
                # A confirmed rejection may advance to the next ranked candidate.
                continue

        if pending_entry:
            proxy_note = {
                "status": "SKIPPED_PENDING_SATELLITE_ORDER",
                "reason": pending_entry.get("reason"),
                "executed": [],
            }
        elif broker_snapshot_block:
            proxy_note = {
                "status": "SKIPPED_BROKER_SNAPSHOT_UNVERIFIED",
                "reason": "BROKER_SNAPSHOT_UNVERIFIED",
                "executed": [],
            }
        else:
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
        elif pending_entry:
            status = "submitted"
            reason = str(
                pending_entry.get("reason")
                or "BROKER_BUY_AWAITING_RECONCILIATION"
            )
            action = "SATELLITE_ORDER_PENDING"
            message = str(
                pending_entry.get("message")
                or "증권사 체결 및 원장 대조 대기 중입니다."
            )
        elif broker_snapshot_block:
            status = "skipped"
            reason = "BROKER_SNAPSHOT_UNVERIFIED"
            action = "BROKER_SNAPSHOT_UNVERIFIED"
            message = str(broker_snapshot_block.get("message") or reason)
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
        # Backward-compat single-candidate ticker when simulation skipped under AUTO_BUY_DISABLED
        first_disabled = next(
            (s for s in skipped if s.get("reason") == "AUTO_BUY_DISABLED" and s.get("ticker")),
            None,
        )
        if first_disabled:
            res["ticker"] = first_disabled["ticker"]
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

    def _resolve_kijun(self, candidate: Dict[str, Any]) -> float:
        val = candidate.get("kijun") or candidate.get("kijun_26")
        if val is not None:
            try:
                f = float(val)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
        gap = candidate.get("kijun_gap_pct") or candidate.get("kijun_gap")
        price = candidate.get("price") or candidate.get("entry_price")
        if gap is not None and price is not None:
            try:
                g = float(gap)
                p = float(price)
                if p > 0 and (1.0 + g / 100.0) > 0:
                    return round(p / (1.0 + g / 100.0), 4)
            except (TypeError, ValueError):
                pass
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
        feed_or_candidates: Any,
        active_tickers: Optional[Any] = None,
        broker_tickers: Optional[List[str]] = None,
        cooldown_tickers: Optional[List[str]] = None,
        held: Optional[Set[str]] = None,
        considered: Optional[Set[str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Walk the ranked conviction list and return the first candidate that is not
        already held AND passes the Entry Gate.

        Returns (candidate, gate_result) or (None, {"reason": "NO_ELIGIBLE_CANDIDATE", ...}).
        """
        if isinstance(feed_or_candidates, dict):
            ranked = feed_or_candidates.get("ranked_conviction_list") or []
            if not ranked:
                top = feed_or_candidates.get("top_conviction_pick")
                ranked = [top] if top else []
            candidates = [c for c in ranked if isinstance(c, dict) and c.get("ticker")]
        elif isinstance(feed_or_candidates, list):
            candidates = feed_or_candidates
        else:
            candidates = []

        held_set: Set[str] = set()
        if held is not None:
            held_set.update(str(t).upper().strip() for t in held)
        if active_tickers is not None:
            if isinstance(active_tickers, (set, list, tuple)):
                held_set.update(str(t).upper().strip() for t in active_tickers)

        considered_set: Set[str] = set()
        if considered is not None:
            considered_set.update(str(t).upper().strip() for t in considered)

        if broker_tickers is not None:
            if isinstance(broker_tickers, set):
                # Positional call: _next_unheld_candidate(candidates, held, considered)
                considered_set.update(str(t).upper().strip() for t in broker_tickers)
            elif isinstance(broker_tickers, (list, tuple)):
                held_set.update(str(t).upper().strip() for t in broker_tickers)

        if cooldown_tickers is not None:
            cooldowns = list(cooldown_tickers)
        else:
            cooldowns = exit_cooldown.cooldown_tickers()

        skipped_cands: List[Dict[str, Any]] = []
        for pick in candidates:
            if not pick or not isinstance(pick, dict):
                continue
            ticker = str(pick.get("ticker", "")).upper().strip()
            if not ticker or ticker in held_set or ticker in considered_set or is_proxy_ticker(ticker):
                continue
            kijun = self._resolve_kijun(pick)
            gate = evaluate_entry_gate(pick, kijun, cooldowns)
            if gate.get("eligible"):
                return pick, gate
            skipped_cands.append({"ticker": ticker, "reason": gate.get("reason")})
            logger.info(
                f"[AutoPilot Entry Gate] SKIP {ticker}: {gate.get('reason')} "
                f"(price=${gate.get('price')}, kijun=${gate.get('kijun_26')})"
            )

        return None, {"reason": "NO_ELIGIBLE_CANDIDATE", "skipped": skipped_cands}

    def _broker_slot_guard(self, ticker: str, max_allowed_slots: int) -> Optional[Dict[str, Any]]:
        if not default_kis_broker.is_configured():
            return None
        try:
            broker_bal = default_kis_broker.get_overseas_balance()
            if (
                broker_bal.get("status") != "success"
                or broker_bal.get("snapshot_complete") is not True
            ):
                return {
                    "status": "skipped",
                    "reason": "BROKER_SNAPSHOT_UNVERIFIED",
                    "ticker": ticker,
                    "message": (
                        "증권사 전 거래소 잔고 스냅샷이 완전하지 않아 "
                        "신규 위성 주문과 Proxy 파킹을 차단합니다."
                    ),
                }
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
            return {
                "status": "skipped",
                "reason": "BROKER_SNAPSHOT_UNVERIFIED",
                "ticker": ticker,
                "message": f"증권사 잔고 확인 실패로 신규 주문 차단: {b_err}",
            }
        return None

    @exit_cooldown.serialized_async_satellite_transition
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
        current_session = now_us_eastern().strftime("%Y-%m-%d")
        if exit_cooldown.has_session_exit(current_session):
            return {
                "status": "skipped",
                "reason": "SESSION_EXIT_LOCKOUT",
                "ticker": ticker,
                "slot_rank": slot_rank,
                "message": "당일 위성 청산이 감지되어 다음 세션까지 신규 위성 진입을 차단합니다.",
            }
        pending_key = f"BUY:{ticker}"
        pending_at = self._pending_proxy_orders.get(pending_key, 0.0)
        if pending_at and (time.time() - pending_at) < self._pending_order_ttl_sec:
            return {
                "status": "skipped",
                "reason": "BROKER_BUY_PENDING",
                "ticker": ticker,
                "slot_rank": slot_rank,
                "message": "증권사 매수 체결·원장 동기화 대기 중입니다.",
            }
        self._pending_proxy_orders.pop(pending_key, None)

        live_price = None
        if default_kis_broker.is_configured():
            if not is_us_regular_hours():
                return {
                    "status": "skipped",
                    "reason": "MARKET_CLOSED",
                    "ticker": ticker,
                    "slot_rank": slot_rank,
                    "message": "미국 정규장 외 신규 위성 주문은 차단됩니다.",
                }
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
            port_cash = float(
                port_now.get("cash_usd")
                or port_now.get("free_cash_usd")
                or port_now.get("cash")
                or 0.0
            )
        except Exception:
            port_cash = 0.0
        shortfall = max(0.0, needed_usd - max(0.0, port_cash))
        proxy_sells = self._free_proxy_cash_for_entry(shortfall, holdings) if shortfall > 0 else []
        if any(x.get("status") == "SUBMITTED_AWAITING_RECONCILIATION" for x in proxy_sells):
            return {
                "status": "skipped",
                "reason": "CASH_PROXY_SELL_PENDING",
                "ticker": ticker,
                "slot_rank": slot_rank,
                "message": "Cash Proxy 매도 체결·원장 동기화 대기 중입니다.",
                "cash_proxy_sells": proxy_sells,
            }
        marketable_exec_price = round(exec_price * 1.005, 2)

        order_id = None
        broker_status = "SIMULATED"
        broker_msg = "시뮬레이션 모드 매수 접수"

        if default_kis_broker.is_configured():
            try:
                ex_cd = "NYSE" if ticker in ["CVX", "DELL", "JNJ", "XOM", "UNH", "PG", "JPM", "V", "MA", "LLY"] else "NASD"
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
                if broker_status != "filled":
                    self._pending_proxy_orders[pending_key] = time.time()
                    return {
                        "status": "submitted",
                        "reason": "BROKER_BUY_AWAITING_RECONCILIATION",
                        "ticker": ticker,
                        "slot_rank": slot_rank,
                        "order_id": order_id,
                        "message": broker_msg or "증권사 매수 접수 후 체결 동기화 대기 중입니다.",
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
            execution_log={
                "order_type": "AUTOPILOT_SATELLITE_ENTRY",
                "status": (
                    "SIMULATED" if broker_status == "SIMULATED" else "FILLED"
                ),
                "message": f"Autopilot C1-M2 satellite entry slot #{slot_rank}",
                "order_id": str(
                    order_id or f"SIM-AUTOPILOT-{ticker}-{int(time.time() * 1000)}"
                ),
                "timestamp": now_str,
            },
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

    @serialized_proxy_orders
    def _ensure_cash_proxy_parked(self, feed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Align idle NAV into QQQ (+ QLD if 1.5x).
        Executes both SELL (delever / trim) and BUY (park) when auto-buy enabled.
        """
        del feed  # reserved for future feed-driven price hints
        port = get_live_portfolio()
        holdings = list(port.get("holdings", []))
        equity = float(port.get("total_equity_usd") or port.get("total_value") or 7500.0)
        cash = float(port.get("cash_usd") or port.get("free_cash_usd") or port.get("cash") or 0.0)
        leverage_on = self._leverage_mode_now()
        qqq_px = self._etf_price(CASH_PROXY_TICKER)
        qld_px = self._etf_price("QLD")  # always fetch — needed for QLD sells when delevering
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
            actions = list(plan.get("actions") or [])
            ordered = [a for a in actions if a.get("side") == "SELL"] + [
                a for a in actions if a.get("side") == "BUY"
            ]
            for act in ordered:
                ticker = str(act.get("ticker") or "").upper()
                side = str(act.get("side") or "").upper()
                qty = int(act.get("qty") or 0)
                px = qqq_px if ticker == CASH_PROXY_TICKER else qld_px
                if not ticker or side not in {"BUY", "SELL"} or qty <= 0 or px <= 0:
                    continue
                pending_key = f"{side}:{ticker}"
                pending_at = self._pending_proxy_orders.get(pending_key, 0.0)
                if pending_at and (time.time() - pending_at) < self._pending_order_ttl_sec:
                    if side == "SELL":
                        break
                    continue
                self._pending_proxy_orders.pop(pending_key, None)
                client_order_id = make_client_order_id()
                if not claim_proxy_order_intent(
                    ticker=ticker,
                    side=side,
                    quantity=float(qty),
                    price=px,
                    owner="AUTOPILOT_ALIGN",
                    exchange="AMEX" if ticker == "QLD" else "NASD",
                    client_order_id=client_order_id,
                    baseline_quantity=sum(
                        float(h.get("quantity") or 0.0)
                        for h in holdings
                        if str(h.get("ticker") or "").upper() == ticker
                    ),
                ):
                    executed.append({
                        **act,
                        "status": "PERSISTENT_ORDER_PENDING",
                    })
                    if side == "SELL":
                        break
                    continue
                broker_status = "SIMULATED"
                order_id = None
                if default_kis_broker.is_configured():
                    try:
                        limit = round(px * (1.005 if side == "BUY" else 0.995), 2)
                        broker_res = default_kis_broker.place_order(
                            ticker=ticker, side=side, qty=qty,
                            price=limit, order_type="00",
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
                                f"[AutoPilot CashProxy] {side} {ticker} x{qty} rejected: "
                                f"{broker_res.get('message')}"
                            )
                            continue
                        if broker_status != "filled":
                            # Keep SQLite at broker-confirmed holdings until reconciliation observes the fill.
                            self._pending_proxy_orders[pending_key] = time.time()
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
                            if side == "SELL":
                                break
                            continue
                    except Exception as e:
                        logger.error(f"[AutoPilot CashProxy {side} Error] {e}")
                        continue
                audit_log = {
                    "order_type": "AUTOPILOT_CASH_PROXY_REBALANCE",
                    "status": (
                        "SIMULATED" if broker_status == "SIMULATED" else "FILLED"
                    ),
                    "message": f"Autopilot cash-proxy sleeve: {act.get('reason')}",
                    "order_id": str(
                        order_id
                        or f"SIM-PROXY-{side}-{ticker}-{int(time.time() * 1000)}"
                    ),
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
                        logger.error(f"[AutoPilot CashProxy Sell Ledger Error] {e}")
                        ledger_sold = 0.0
                    if broker_status == "SIMULATED" and ledger_sold + 1e-9 < qty:
                        clear_proxy_order_intent(ticker)
                        continue
                clear_proxy_order_intent(ticker)
                executed.append({**act, "fill_price": px})
                logger.info(
                    f"[AutoPilot] Cash-proxy sleeve: {side} {ticker} x{qty} @ ${px:.2f} "
                    f"({act.get('reason')})"
                )
        plan["executed"] = executed
        return plan

    @serialized_proxy_orders
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
            pending_key = f"SELL:{ticker}"
            pending_at = self._pending_proxy_orders.get(pending_key, 0.0)
            if pending_at and (time.time() - pending_at) < self._pending_order_ttl_sec:
                executed.append({**act, "status": "SUBMITTED_AWAITING_RECONCILIATION"})
                break
            self._pending_proxy_orders.pop(pending_key, None)
            client_order_id = make_client_order_id()
            if not claim_proxy_order_intent(
                ticker=ticker,
                side="SELL",
                quantity=float(qty),
                price=px,
                owner="AUTOPILOT_FREE_CASH",
                exchange="AMEX" if ticker == "QLD" else "NASD",
                client_order_id=client_order_id,
                baseline_quantity=sum(
                    float(h.get("quantity") or 0.0)
                    for h in holdings
                    if str(h.get("ticker") or "").upper() == ticker
                ),
            ):
                executed.append({
                    **act,
                    "status": "PERSISTENT_ORDER_PENDING",
                })
                break
            broker_status = "SIMULATED"
            order_id = None
            if default_kis_broker.is_configured():
                try:
                    broker_res = default_kis_broker.place_order(
                        ticker=ticker, side="SELL", qty=qty,
                        price=round(px * 0.995, 2), order_type="00",
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
                        self._pending_proxy_orders[pending_key] = time.time()
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
                        })
                        break
                except Exception as e:
                    logger.error(f"[AutoPilot CashProxy Sell Error] {e}")
                    continue
            # Mark only sold proxy shares across all lots; keep residual shares.
            try:
                ledger_sold = record_portfolio_ticker_sell_quantity(
                    ticker=ticker,
                    sell_price=px,
                    quantity=float(qty),
                    reason=f"FREE_CASH_FOR_LEADERSHIP ({act.get('reason')})",
                    execution_log={
                        "order_type": "AUTOPILOT_FREE_PROXY_CASH",
                        "status": (
                            "SIMULATED"
                            if broker_status == "SIMULATED"
                            else "FILLED"
                        ),
                        "message": f"Autopilot frees proxy cash: {act.get('reason')}",
                        "order_id": str(
                            order_id
                            or (
                                f"SIM-PROXY-SELL-{ticker}-"
                                f"{int(time.time() * 1000)}"
                            )
                        ),
                    },
                )
            except Exception as ledger_err:
                logger.error(
                    f"[AutoPilot Proxy Partial Sell Ledger Error] {ledger_err}"
                )
                ledger_sold = 0.0
            if broker_status == "SIMULATED" and ledger_sold + 1e-9 < qty:
                clear_proxy_order_intent(ticker)
                continue
            clear_proxy_order_intent(ticker)
            executed.append({**act, "fill_price": px})
            logger.info(f"[AutoPilot] Freed proxy cash: SELL {ticker} x{qty} @ ${px:.2f}")
        return executed


# Global singleton instance
default_autopilot = AutoPilotTrader(check_interval_seconds=60)