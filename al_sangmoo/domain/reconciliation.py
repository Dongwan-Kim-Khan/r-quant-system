"""
AL-SANGMOO QUANT TERMINAL: PORTFOLIO RECONCILIATION ENGINE
Compares local SQLite portfolio (my_portfolio) against live broker holdings (KIS API)
and calibrates corporate actions (splits, dividend shares, manual trades).
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Any, List

from al_sangmoo.core.constants import derive_partial_tp_price, derive_stop_price, derive_target_price
from al_sangmoo.infrastructure.brokers.kis_broker import default_kis_broker
from al_sangmoo.infrastructure.persistence import (
    clear_satellite_order_intent,
    clear_proxy_order_intent,
    get_connection,
    init_database,
    get_live_portfolio,
    get_proxy_order_intents,
    get_satellite_order_intents,
    record_execution_log,
    save_account_snapshot,
    update_proxy_order_intent_state,
    update_satellite_order_intent_state,
)
from al_sangmoo.domain.risk import exit_cooldown
from al_sangmoo.domain.risk.cash_proxy import (
    holdings_mark_usd,
    is_proxy_ticker,
    reconcile_overseas_nav,
)

logger = logging.getLogger(__name__)
_RECONCILE_LOCK = threading.Lock()


def _reconcile_satellite_quantity(
    cursor,
    ticker: str,
    aggregate: Dict[str, Any],
    broker_qty: float,
    broker_avg: float,
    broker_current: float,
    now_str: str,
) -> Dict[str, Any]:
    """Adjust satellite lots without replacing one lot by broker aggregate qty."""
    lots = list(aggregate.get("_lots") or [])
    local_qty = float(aggregate.get("quantity") or 0.0)
    local_avg = float(aggregate.get("buy_price") or 0.0)
    diff = broker_qty - local_qty
    quantity_scale = broker_qty / local_qty if local_qty > 0 else 1.0
    price_scale = broker_avg / local_avg if local_avg > 0 else 1.0
    is_cost_preserving_corporate_action = (
        local_qty > 0
        and local_avg > 0
        and broker_qty > 0
        and broker_avg > 0
        and abs(diff) > 1e-9
        and abs(broker_avg - local_avg) >= 0.005
        and abs(quantity_scale * price_scale - 1.0) <= 0.01
    )
    if is_cost_preserving_corporate_action:
        for lot in lots:
            new_qty = float(lot.get("quantity") or 0.0) * quantity_scale
            new_avg = float(lot.get("buy_price") or 0.0) * price_scale
            cursor.execute("""
            UPDATE my_portfolio
            SET quantity = ?, buy_price = ?, current_price = ?, total_cost = ?,
                current_value = ?, pnl_pct = ?, pnl_amount = ?,
                target_price = ?, stop_loss_price = ?, partial_tp_price = ?,
                peak_high = peak_high * ?, kijun_26 = kijun_26 * ?,
                atr_14 = atr_14 * ?, trailing_floor = trailing_floor * ?,
                exit_advice = ?
            WHERE id = ?
            """, (
                new_qty,
                new_avg,
                broker_current,
                new_qty * new_avg,
                new_qty * broker_current,
                (
                    (broker_current - new_avg) / new_avg * 100.0
                    if new_avg > 0
                    else 0.0
                ),
                (broker_current - new_avg) * new_qty,
                derive_target_price(new_avg),
                derive_stop_price(new_avg),
                derive_partial_tp_price(new_avg),
                price_scale,
                price_scale,
                price_scale,
                price_scale,
                f"기업행사 비례 보정 ({now_str})",
                lot["id"],
            ))
        return {
            "action": "ADJUST_SATELLITE_CORPORATE_ACTION",
            "sold_lots": [],
        }
    if diff > 0:
        prior_cost = sum(
            float(lot.get("quantity") or 0.0)
            * float(lot.get("buy_price") or 0.0)
            for lot in lots
        )
        inferred_cost = broker_avg * broker_qty - prior_cost
        new_avg = inferred_cost / diff if inferred_cost > 0 else broker_avg
        cursor.execute("""
        INSERT INTO my_portfolio (
            ticker, buy_date, buy_price, quantity, current_price,
            total_cost, current_value, pnl_pct, pnl_amount,
            target_price, stop_loss_price, partial_tp_price,
            status, exit_advice, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HOLDING', ?, ?)
        """, (
            ticker,
            datetime.now().strftime("%Y-%m-%d"),
            new_avg,
            diff,
            broker_current,
            new_avg * diff,
            broker_current * diff,
            (
                (broker_current - new_avg) / new_avg * 100.0
                if new_avg > 0
                else 0.0
            ),
            (broker_current - new_avg) * diff,
            derive_target_price(new_avg),
            derive_stop_price(new_avg),
            derive_partial_tp_price(new_avg),
            f"실계좌 추가 매수 로트 동기화 ({now_str})",
            now_str,
        ))
        return {"action": "ADD_SATELLITE_LOT", "sold_lots": []}

    if abs(broker_avg - local_avg) < 0.005:
        remaining_sell = abs(diff)
        sold_lots: List[Dict[str, Any]] = []
        for lot in lots:
            if remaining_sell <= 1e-9:
                break
            lot_qty = float(lot.get("quantity") or 0.0)
            sold_qty = min(lot_qty, remaining_sell)
            if sold_qty <= 0:
                continue
            remaining_qty = lot_qty - sold_qty
            sold_lots.append({
                "id": int(lot["id"]),
                "ticker": ticker,
                "buy_date": str(lot.get("buy_date") or ""),
                "buy_price": float(lot.get("buy_price") or 0.0),
                "quantity": sold_qty,
            })
            if remaining_qty <= 1e-9:
                cursor.execute("""
                UPDATE my_portfolio
                SET status = 'SOLD', sell_date = ?, sell_price = NULL,
                    quantity = ?, current_price = ?, current_value = 0,
                    pnl_pct = 0, pnl_amount = 0,
                    exit_advice = ?
                WHERE id = ?
                """, (
                    datetime.now().strftime("%Y-%m-%d"),
                    lot_qty,
                    broker_current,
                    f"실계좌 매도 체결가 확인 대기 ({now_str})",
                    lot["id"],
                ))
            else:
                lot_avg = float(lot.get("buy_price") or 0.0)
                cursor.execute("""
                UPDATE my_portfolio
                SET quantity = ?, total_cost = ?, current_price = ?,
                    current_value = ?, pnl_pct = ?, pnl_amount = ?,
                    exit_advice = ?
                WHERE id = ?
                """, (
                    remaining_qty,
                    remaining_qty * lot_avg,
                    broker_current,
                    remaining_qty * broker_current,
                    (
                        (broker_current - lot_avg) / lot_avg * 100.0
                        if lot_avg > 0
                        else 0.0
                    ),
                    (broker_current - lot_avg) * remaining_qty,
                    f"실계좌 부분 매도 수량 동기화 ({now_str})",
                    lot["id"],
                ))
            remaining_sell -= sold_qty
        if remaining_sell > 1e-6:
            raise RuntimeError(
                f"Unable to allocate broker sell delta across {ticker} lots"
            )
        return {"action": "REDUCE_SATELLITE_LOTS", "sold_lots": sold_lots}

    # Quantity and average moved together: treat as a split/corporate action,
    # preserving each lot's independent trailing state on the adjusted scale.
    quantity_scale = broker_qty / local_qty if local_qty > 0 else 1.0
    price_scale = broker_avg / local_avg if local_avg > 0 else 1.0
    for lot in lots:
        new_qty = float(lot.get("quantity") or 0.0) * quantity_scale
        new_avg = float(lot.get("buy_price") or 0.0) * price_scale
        cursor.execute("""
        UPDATE my_portfolio
        SET quantity = ?, buy_price = ?, current_price = ?, total_cost = ?,
            current_value = ?, pnl_pct = ?, pnl_amount = ?,
            target_price = ?, stop_loss_price = ?, partial_tp_price = ?,
            peak_high = peak_high * ?, kijun_26 = kijun_26 * ?,
            atr_14 = atr_14 * ?, trailing_floor = trailing_floor * ?,
            exit_advice = ?
        WHERE id = ?
        """, (
            new_qty,
            new_avg,
            broker_current,
            new_qty * new_avg,
            new_qty * broker_current,
            (
                (broker_current - new_avg) / new_avg * 100.0
                if new_avg > 0
                else 0.0
            ),
            (broker_current - new_avg) * new_qty,
            derive_target_price(new_avg),
            derive_stop_price(new_avg),
            derive_partial_tp_price(new_avg),
            price_scale,
            price_scale,
            price_scale,
            price_scale,
            f"기업행사 비례 보정 ({now_str})",
            lot["id"],
        ))
    return {"action": "ADJUST_SATELLITE_CORPORATE_ACTION", "sold_lots": []}


def _confirmed_fill_price(
    intent_states: List[Dict[str, Any]],
    ticker: str,
    side: str,
    quantity: float,
) -> float:
    prices = []
    for item in intent_states:
        intent = item["intent"]
        state = item["state"]
        if str(intent.get("ticker") or "").upper() != ticker.upper():
            continue
        if str(intent.get("side") or "").upper() != side.upper():
            continue
        if float(state.get("filled_quantity") or 0.0) + 1e-9 < quantity:
            continue
        fill_price = float(state.get("fill_price") or 0.0)
        if fill_price > 0:
            prices.append(fill_price)
    return prices[0] if len(prices) == 1 else 0.0


def _persist_broker_account_snapshot(broker_balance: Dict[str, Any]) -> None:
    """Store reconciled overseas NAV. Persist lot+cash when output2 is insane; never persist 372k-style output2."""
    if not isinstance(broker_balance, dict):
        return
    mode = str(broker_balance.get("mode") or "").upper()
    if mode in {"", "SIMULATED", "SIMULATOR"}:
        return
    holdings = broker_balance.get("holdings") or []
    marked = holdings_mark_usd(holdings)

    cash_val = float(broker_balance.get("cash_available_usd") or 0.0)
    equity_val = float(broker_balance.get("total_equity_usd") or 0.0)
    stock_eval_val = float(broker_balance.get("stock_eval_usd") or 0.0)

    rec = reconcile_overseas_nav(
        holdings_eval=marked,
        equity=equity_val,
        cash=cash_val,
        stock_eval=stock_eval_val,
    )
    rec_equity = float(rec.get("total_equity_usd") or 0.0)
    if rec_equity <= 0:
        return
    if rec.get("sane"):
        persist_source = rec.get("source") or "KIS"
    elif rec.get("source") == "HOLDINGS" and marked > 0:
        persist_source = "HOLDINGS"
    else:
        return
    save_account_snapshot({
        "total_equity_usd": rec["total_equity_usd"],
        "cash_available_usd": rec["cash_available_usd"],
        "stock_eval_usd": rec["stock_eval_usd"],
        "realized_pnl_usd": float(broker_balance.get("realized_pnl_usd") or 0.0),
        "unrealized_pnl_usd": float(broker_balance.get("unrealized_pnl_usd") or 0.0),
        "unrealized_pnl_pct": float(broker_balance.get("unrealized_pnl_pct") or 0.0),
        "unrealized_pnl_krw": float(broker_balance.get("unrealized_pnl_krw") or 0.0),
        "total_pnl_usd": float(broker_balance.get("total_pnl_usd") or 0.0),
        "total_pnl_pct": float(broker_balance.get("total_pnl_pct") or 0.0),
        "source": persist_source,
        "mode": mode,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@exit_cooldown.serialized_satellite_transition
def check_sync(auto_calibrate: bool = True) -> Dict[str, Any]:
    """Serialize broker-to-SQLite calibration to prevent duplicate imports."""
    with _RECONCILE_LOCK:
        return _check_sync_unlocked(auto_calibrate=auto_calibrate)


def _check_sync_unlocked(auto_calibrate: bool = True) -> Dict[str, Any]:
    """
    Executes a 1-time daily reconciliation audit between KIS Broker and local SQLite DB.
    
    Args:
        auto_calibrate: If True, automatically updates SQLite portfolio quantities and avg prices
                        to match broker source-of-truth.
                        
    Returns:
        Audit report dict containing match status, discrepancies, and calibration actions.
    """
    init_database()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Fetch live broker holdings (Single TR call)
    broker_balance = default_kis_broker.get_overseas_balance()
    if broker_balance.get("status") not in ("success", "partial"):
        return {
            "status": "error",
            "timestamp": now_str,
            "message": f"Broker balance inquiry failed: {broker_balance.get('message')}",
            "discrepancies": []
        }
        
    broker_holdings = {h["ticker"].upper(): h for h in broker_balance.get("holdings", [])}
    broker_mode = str(broker_balance.get("mode") or "").upper()
    broker_can_confirm_empty = (
        broker_balance.get("snapshot_complete") is True
        and broker_mode not in {"", "SIMULATED", "SIMULATOR"}
        and "cash_available_usd" in broker_balance
    )

    intent_states: List[Dict[str, Any]] = []
    for kind, intents in (
        ("PROXY", get_proxy_order_intents()),
        ("SATELLITE", get_satellite_order_intents()),
    ):
        for intent in intents:
            try:
                state = default_kis_broker.get_overseas_order_state(
                    ticker=intent["ticker"],
                    order_id=str(intent.get("order_id") or ""),
                    client_order_id=str(intent.get("client_order_id") or ""),
                    exchange=str(intent.get("exchange") or "NASD"),
                )
            except Exception as exc:
                state = {
                    "status": "UNKNOWN",
                    "terminal": False,
                    "filled_quantity": float(
                        intent.get("filled_quantity") or 0.0
                    ),
                    "error": str(exc),
                }
            intent_states.append({
                "kind": kind,
                "intent": intent,
                "state": state,
            })
    
    # 2. Fetch local SQLite holdings
    local_portfolio = get_live_portfolio()
    local_groups: Dict[str, List[Dict[str, Any]]] = {}
    for holding in local_portfolio.get("holdings", []):
        local_groups.setdefault(str(holding["ticker"]).upper(), []).append(holding)

    # Broker balances are ticker-aggregated, while repeated proxy parking may
    # create several local lots. Compare aggregate-to-aggregate and consolidate
    # duplicate active rows before applying broker corrections.
    local_holdings: Dict[str, Dict[str, Any]] = {}
    for ticker, lots in local_groups.items():
        total_qty = sum(float(lot.get("quantity") or 0.0) for lot in lots)
        total_cost = sum(
            float(lot.get("buy_price") or 0.0)
            * float(lot.get("quantity") or 0.0)
            for lot in lots
        )
        primary = dict(min(lots, key=lambda lot: int(lot["id"])))
        primary["quantity"] = total_qty
        primary["buy_price"] = total_cost / total_qty if total_qty > 0 else 0.0
        primary["total_cost"] = total_cost
        primary["buy_date"] = min(str(lot.get("buy_date") or "") for lot in lots)
        primary["_lots"] = sorted(lots, key=lambda lot: int(lot["id"]))
        primary["_extra_lot_ids"] = [
            int(lot["id"]) for lot in lots if int(lot["id"]) != int(primary["id"])
        ] if is_proxy_ticker(ticker) else []
        local_holdings[ticker] = primary
    
    discrepancies: List[Dict[str, Any]] = []
    calibrations: List[Dict[str, Any]] = []
    execution_events: List[Dict[str, Any]] = []
    matched_count = 0
    
    all_tickers = set(broker_holdings.keys()).union(set(local_holdings.keys()))
    
    with get_connection() as conn:
        cursor = conn.cursor()

        for ticker, holding in local_holdings.items():
            extra_ids = list(holding.get("_extra_lot_ids") or [])
            if (
                not auto_calibrate
                or not extra_ids
                or not is_proxy_ticker(ticker)
            ):
                continue
            qty = float(holding["quantity"])
            avg = float(holding["buy_price"])
            cur = float(holding.get("current_price") or avg)
            cur_val = qty * cur
            pnl_pct = ((cur - avg) / avg * 100.0) if avg > 0 else 0.0
            pnl_amt = cur_val - (qty * avg)
            placeholders = ",".join("?" for _ in extra_ids)
            cursor.execute(
                f"DELETE FROM my_portfolio WHERE id IN ({placeholders})",
                tuple(extra_ids),
            )
            cursor.execute(
                """
                UPDATE my_portfolio
                SET buy_date = ?, buy_price = ?, quantity = ?, total_cost = ?,
                    current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?,
                    target_price = ?, stop_loss_price = ?, partial_tp_price = ?,
                    exit_advice = ?
                WHERE id = ?
                """,
                (
                    holding["buy_date"],
                    avg,
                    qty,
                    qty * avg,
                    cur,
                    cur_val,
                    pnl_pct,
                    pnl_amt,
                    derive_target_price(avg),
                    derive_stop_price(avg),
                    derive_partial_tp_price(avg),
                    f"로컬 다중 매수 로트 통합 ({now_str})",
                    holding["id"],
                ),
            )
            calibrations.append({
                "ticker": ticker,
                "action": "CONSOLIDATE_LOCAL_LOTS",
                "merged_lot_count": len(extra_ids) + 1,
                "quantity": qty,
                "avg_price": avg,
            })
        
        for ticker in all_tickers:
            b_item = broker_holdings.get(ticker)
            l_item = local_holdings.get(ticker)
            
            b_qty = float(b_item["quantity"]) if b_item else 0.0
            l_qty = float(l_item["quantity"]) if l_item else 0.0
            b_avg = float(b_item.get("avg_price", 0.0)) if b_item else 0.0
            l_avg = float(l_item.get("buy_price", 0.0)) if l_item else 0.0
            
            # Sub-case 1: Exact match (Quantity & Price match within epsilon).
            # Qty & avg agree, but the live current price must STILL be refreshed
            # from the broker so the local DB / dashboard never freeze on a stale
            # quote (root-cause fix for prices stuck at a past value).
            if b_item and l_item and abs(b_qty - l_qty) < 1e-4 and abs(b_avg - l_avg) < 0.005:
                matched_count += 1
                if auto_calibrate and b_avg > 0:
                    b_cur = float(b_item.get("current_price") or 0.0)
                    if b_cur > 0:
                        quote_lots = (
                            list(l_item.get("_lots") or [])
                            if not is_proxy_ticker(ticker)
                            else [l_item]
                        )
                        for lot in quote_lots:
                            lot_qty = float(lot.get("quantity") or 0.0)
                            lot_avg = float(lot.get("buy_price") or 0.0)
                            new_val = lot_qty * b_cur
                            new_cost = lot_qty * lot_avg
                            lot_pnl_pct = (
                                (b_cur - lot_avg) / lot_avg * 100
                                if lot_avg > 0
                                else 0.0
                            )
                            cursor.execute("""
                            UPDATE my_portfolio
                            SET current_price = ?, current_value = ?,
                                pnl_pct = ?, pnl_amount = ?
                            WHERE id = ?
                            """, (
                                b_cur,
                                new_val,
                                lot_pnl_pct,
                                new_val - new_cost,
                                lot["id"],
                            ))
                        calibrations.append({
                            "ticker": ticker,
                            "action": "UPDATE_CURRENT_PRICE",
                            "current_price": b_cur
                        })
                continue
                
            # Sub-case 2: Price Mismatch only (Quantity matches, but execution entry price differs)
            if b_item and l_item and abs(b_qty - l_qty) < 1e-4 and abs(b_avg - l_avg) >= 0.005:
                discrepancy = {
                    "ticker": ticker,
                    "type": "PRICE_MISMATCH",
                    "broker_qty": b_qty,
                    "local_qty": l_qty,
                    "broker_avg_price": b_avg,
                    "local_avg_price": l_avg,
                    "diff_price": b_avg - l_avg
                }
                discrepancies.append(discrepancy)
                
                if auto_calibrate and b_avg > 0:
                    b_cur = float(b_item.get("current_price") or b_avg)
                    if not is_proxy_ticker(ticker):
                        scale = b_avg / l_avg if l_avg > 0 else 1.0
                        for lot in list(l_item.get("_lots") or []):
                            lot_qty = float(lot.get("quantity") or 0.0)
                            lot_avg = float(lot.get("buy_price") or 0.0) * scale
                            lot_value = lot_qty * b_cur
                            lot_pnl_pct = (
                                (b_cur - lot_avg) / lot_avg * 100.0
                                if lot_avg > 0
                                else 0.0
                            )
                            cursor.execute("""
                            UPDATE my_portfolio
                            SET buy_price = ?, current_price = ?, total_cost = ?,
                                current_value = ?, pnl_pct = ?, pnl_amount = ?,
                                target_price = ?, stop_loss_price = ?,
                                partial_tp_price = ?, peak_high = peak_high * ?,
                                kijun_26 = kijun_26 * ?, atr_14 = atr_14 * ?,
                                trailing_floor = trailing_floor * ?,
                                exit_advice = ?
                            WHERE id = ?
                            """, (
                                lot_avg,
                                b_cur,
                                lot_qty * lot_avg,
                                lot_value,
                                lot_pnl_pct,
                                lot_value - lot_qty * lot_avg,
                                derive_target_price(lot_avg),
                                derive_stop_price(lot_avg),
                                derive_partial_tp_price(lot_avg),
                                scale,
                                scale,
                                scale,
                                scale,
                                f"실계좌 평단가 비례 보정 ({now_str})",
                                lot["id"],
                            ))
                    else:
                        new_val = b_qty * b_cur
                        new_cost = b_qty * b_avg
                        pnl_pct = (
                            (b_cur - b_avg) / b_avg * 100
                            if b_avg > 0
                            else 0.0
                        )
                        cursor.execute("""
                        UPDATE my_portfolio
                        SET buy_price = ?, current_price = ?, total_cost = ?,
                            current_value = ?, pnl_pct = ?, pnl_amount = ?,
                            target_price = ?, stop_loss_price = ?,
                            partial_tp_price = ?, exit_advice = ?
                        WHERE id = ?
                        """, (
                            b_avg,
                            b_cur,
                            new_cost,
                            new_val,
                            pnl_pct,
                            new_val - new_cost,
                            derive_target_price(b_avg),
                            derive_stop_price(b_avg),
                            derive_partial_tp_price(b_avg),
                            f"실계좌 평단가 동기화 보정 ({now_str})",
                            l_item["id"],
                        ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "UPDATE_AVG_PRICE",
                        "old_avg_price": l_avg,
                        "new_avg_price": b_avg
                    })
                    execution_events.append({
                        "ticker": ticker,
                        "side": "SYNC",
                        "quantity": 0.0,
                        "price": b_avg,
                        "action": "UPDATE_AVG_PRICE",
                        "message": (
                            f"Broker reconciliation corrected average price "
                            f"${l_avg:.4f} -> ${b_avg:.4f}"
                        ),
                        "arm_exit_lock": False,
                    })

            # Sub-case 3: Quantity mismatch (e.g. stock split, partial sell on mobile app)
            elif b_item and l_item and abs(b_qty - l_qty) >= 1e-4:
                diff = b_qty - l_qty
                discrepancy = {
                    "ticker": ticker,
                    "type": "QUANTITY_MISMATCH",
                    "broker_qty": b_qty,
                    "local_qty": l_qty,
                    "diff": diff,
                    "broker_avg_price": b_avg,
                    "local_avg_price": l_avg
                }
                discrepancies.append(discrepancy)
                
                if auto_calibrate:
                    effective_avg = b_avg if b_avg > 0 else l_avg
                    b_cur = float(b_item.get("current_price") or effective_avg)
                    sold_lots: List[Dict[str, Any]] = []
                    if is_proxy_ticker(ticker):
                        if diff < 0:
                            sold_lots = [{
                                "id": int(l_item["id"]),
                                "ticker": ticker,
                                "buy_date": str(l_item.get("buy_date") or ""),
                                "buy_price": l_avg,
                                "quantity": abs(diff),
                            }]
                        new_val = b_qty * b_cur
                        new_cost = b_qty * effective_avg
                        pnl_pct = (
                            (b_cur - effective_avg) / effective_avg * 100
                            if effective_avg > 0
                            else 0.0
                        )
                        cursor.execute("""
                        UPDATE my_portfolio
                        SET quantity = ?, buy_price = ?, current_price = ?,
                            total_cost = ?, current_value = ?, pnl_pct = ?,
                            pnl_amount = ?, target_price = ?,
                            stop_loss_price = ?, partial_tp_price = ?,
                            exit_advice = ?
                        WHERE id = ?
                        """, (
                            b_qty,
                            effective_avg,
                            b_cur,
                            new_cost,
                            new_val,
                            pnl_pct,
                            new_val - new_cost,
                            derive_target_price(effective_avg),
                            derive_stop_price(effective_avg),
                            derive_partial_tp_price(effective_avg),
                            f"실계좌 대조 보정 ({now_str})",
                            l_item["id"],
                        ))
                        reconcile_action = "UPDATE_QUANTITY_AND_PRICE"
                    else:
                        outcome = _reconcile_satellite_quantity(
                            cursor=cursor,
                            ticker=ticker,
                            aggregate=l_item,
                            broker_qty=b_qty,
                            broker_avg=effective_avg,
                            broker_current=b_cur,
                            now_str=now_str,
                        )
                        reconcile_action = str(outcome["action"])
                        sold_lots = list(outcome.get("sold_lots") or [])
                    calibrations.append({
                        "ticker": ticker,
                        "action": reconcile_action,
                        "old_qty": l_qty,
                        "new_qty": b_qty,
                        "old_avg_price": l_avg,
                        "new_avg_price": effective_avg
                    })
                    if reconcile_action == "ADJUST_SATELLITE_CORPORATE_ACTION":
                        execution_events.append({
                            "ticker": ticker,
                            "side": "SYNC",
                            "quantity": 0.0,
                            "price": effective_avg,
                            "action": reconcile_action,
                            "message": (
                                "Broker reconciliation proportionally adjusted "
                                "satellite lots for a corporate action"
                            ),
                            "arm_exit_lock": False,
                            "status": "RECONCILED",
                        })
                        continue

                    side = "BUY" if diff > 0 else "SELL"
                    confirmed_fill = (
                        _confirmed_fill_price(
                            intent_states,
                            ticker,
                            side,
                            abs(diff),
                        )
                        if side == "SELL"
                        else 0.0
                    )
                    if side == "SELL" and confirmed_fill > 0:
                        for sold_lot in sold_lots:
                            buy_price = float(sold_lot["buy_price"])
                            sold_qty = float(sold_lot["quantity"])
                            realized_pct = (
                                (confirmed_fill - buy_price)
                                / buy_price
                                * 100.0
                                if buy_price > 0
                                else 0.0
                            )
                            realized_amount = (
                                confirmed_fill - buy_price
                            ) * sold_qty
                            cursor.execute("""
                            INSERT INTO trade_history (
                                holding_id, ticker, buy_date, sell_date,
                                buy_price, sell_price, quantity, pnl_pct,
                                pnl_amount, reason, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                sold_lot["id"],
                                ticker,
                                sold_lot["buy_date"],
                                datetime.now().strftime("%Y-%m-%d"),
                                buy_price,
                                confirmed_fill,
                                sold_qty,
                                realized_pct,
                                realized_amount,
                                "RECONCILED_CONFIRMED_SELL_FILL",
                                now_str,
                            ))
                            cursor.execute("""
                            UPDATE my_portfolio
                            SET sell_price = CASE
                                    WHEN status = 'SOLD' THEN ?
                                    ELSE sell_price
                                END,
                                pnl_pct = CASE
                                    WHEN status = 'SOLD' THEN ?
                                    ELSE pnl_pct
                                END,
                                pnl_amount = CASE
                                    WHEN status = 'SOLD' THEN ?
                                    ELSE pnl_amount
                                END
                            WHERE id = ?
                            """, (
                                confirmed_fill,
                                realized_pct,
                                realized_amount,
                                sold_lot["id"],
                            ))
                    execution_events.append({
                        "ticker": ticker,
                        "side": side,
                        "quantity": abs(diff),
                        "price": (
                            effective_avg
                            if side == "BUY"
                            else (confirmed_fill or b_cur)
                        ),
                        "action": reconcile_action,
                        "message": (
                            f"Broker reconciliation observed {side} quantity delta "
                            f"{l_qty:g} -> {b_qty:g}"
                            + (
                                ""
                                if side == "BUY" or confirmed_fill > 0
                                else " (execution price unavailable; quote is estimated)"
                            )
                        ),
                        "arm_exit_lock": (
                            side == "SELL"
                            and b_qty <= 1e-4
                            and not is_proxy_ticker(ticker)
                        ),
                        "status": (
                            "RECONCILED"
                            if side == "BUY" or confirmed_fill > 0
                            else "RECONCILED_ESTIMATED"
                        ),
                    })
                    
            # Sub-case 3: In Broker only (Externally bought on MTS/HTS)
            elif b_item and not l_item:
                discrepancy = {
                    "ticker": ticker,
                    "type": "BROKER_ONLY",
                    "broker_qty": b_qty,
                    "local_qty": 0.0,
                    "broker_avg_price": b_item.get("avg_price", 0.0)
                }
                discrepancies.append(discrepancy)
                
                if auto_calibrate:
                    b_avg = float(b_item.get("avg_price", 0.0))
                    b_cur = float(b_item.get("current_price") or b_avg)
                    total_cost = b_qty * b_avg
                    target_pr = derive_target_price(b_avg)
                    stop_pr = derive_stop_price(b_avg)
                    partial_tp = derive_partial_tp_price(b_avg)
                    
                    cursor.execute("""
                    INSERT INTO my_portfolio (
                        ticker, buy_date, buy_price, quantity, current_price,
                        total_cost, current_value, pnl_pct, pnl_amount,
                        target_price, stop_loss_price, partial_tp_price,
                        status, exit_advice, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, 'HOLDING', '실계좌 동기화 등록', ?)
                    """, (
                        ticker, datetime.now().strftime("%Y-%m-%d"), b_avg, b_qty, b_cur,
                        total_cost, total_cost, target_pr, stop_pr, partial_tp, now_str
                    ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "IMPORT_FROM_BROKER",
                        "quantity": b_qty,
                        "avg_price": b_avg
                    })
                    execution_events.append({
                        "ticker": ticker,
                        "side": "BUY",
                        "quantity": b_qty,
                        "price": b_avg,
                        "action": "IMPORT_FROM_BROKER",
                        "message": "Broker reconciliation imported broker-only position",
                        "arm_exit_lock": False,
                    })
                    
            # Sub-case 4: In Local SQLite only (Closed on broker or sold externally)
            elif l_item and not b_item:
                discrepancy = {
                    "ticker": ticker,
                    "type": "LOCAL_ONLY",
                    "broker_qty": 0.0,
                    "local_qty": l_qty
                }
                discrepancies.append(discrepancy)
                
                # A successful live/paper snapshot with an explicit cash balance
                # can legitimately contain zero holdings after the final exit.
                # Never trust the adapter's synthetic SIMULATED empty snapshot.
                if auto_calibrate and broker_can_confirm_empty:
                    inferred_sell_price = float(
                        l_item.get("current_price")
                        or l_item.get("buy_price")
                        or 0.0
                    )
                    if is_proxy_ticker(ticker):
                        sold_lots = [{
                            "id": int(l_item["id"]),
                            "ticker": ticker,
                            "buy_date": str(l_item.get("buy_date") or ""),
                            "buy_price": float(l_item.get("buy_price") or 0.0),
                            "quantity": l_qty,
                        }]
                        cursor.execute("""
                        UPDATE my_portfolio
                        SET status = 'SOLD', sell_date = ?, sell_price = NULL,
                            current_price = ?, current_value = 0,
                            pnl_pct = 0, pnl_amount = 0, exit_advice = ?
                        WHERE id = ?
                        """, (
                            datetime.now().strftime("%Y-%m-%d"),
                            inferred_sell_price,
                            f"실계좌 매도 체결가 확인 대기 ({now_str})",
                            l_item["id"],
                        ))
                    else:
                        outcome = _reconcile_satellite_quantity(
                            cursor=cursor,
                            ticker=ticker,
                            aggregate=l_item,
                            broker_qty=0.0,
                            broker_avg=float(l_item.get("buy_price") or 0.0),
                            broker_current=inferred_sell_price,
                            now_str=now_str,
                        )
                        sold_lots = list(outcome.get("sold_lots") or [])

                    confirmed_fill = _confirmed_fill_price(
                        intent_states,
                        ticker,
                        "SELL",
                        l_qty,
                    )
                    fill_price = (
                        confirmed_fill if confirmed_fill > 0 else inferred_sell_price
                    )
                    if fill_price > 0:
                        for sold_lot in sold_lots:
                            buy_price = float(sold_lot["buy_price"])
                            sold_qty = float(sold_lot["quantity"])
                            pnl_pct = (
                                (fill_price - buy_price)
                                / buy_price
                                * 100.0
                                if buy_price > 0
                                else 0.0
                            )
                            pnl_amount = (
                                fill_price - buy_price
                            ) * sold_qty
                            fill_reason = (
                                "RECONCILED_CONFIRMED_SELL_FILL"
                                if confirmed_fill > 0
                                else "RECONCILED_ESTIMATED_SELL_FILL"
                            )
                            cursor.execute("""
                            INSERT INTO trade_history (
                                holding_id, ticker, buy_date, sell_date,
                                buy_price, sell_price, quantity, pnl_pct,
                                pnl_amount, reason, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                sold_lot["id"],
                                ticker,
                                sold_lot["buy_date"],
                                datetime.now().strftime("%Y-%m-%d"),
                                buy_price,
                                fill_price,
                                sold_qty,
                                pnl_pct,
                                pnl_amount,
                                fill_reason,
                                now_str,
                            ))
                            cursor.execute("""
                            UPDATE my_portfolio
                            SET sell_price = ?, pnl_pct = ?, pnl_amount = ?
                            WHERE id = ? AND status = 'SOLD'
                            """, (
                                fill_price,
                                pnl_pct,
                                pnl_amount,
                                sold_lot["id"],
                            ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "CLOSE_LOCAL_POSITION",
                        "ids": [lot["id"] for lot in sold_lots],
                    })
                    execution_events.append({
                        "ticker": ticker,
                        "side": "SELL",
                        "quantity": l_qty,
                        "price": confirmed_fill or inferred_sell_price,
                        "action": "CLOSE_LOCAL_POSITION",
                        "message": (
                            "Broker reconciliation observed broker-side full exit"
                            + (
                                ""
                                if confirmed_fill > 0
                                else " (execution price unavailable; quote is estimated)"
                            )
                        ),
                        "arm_exit_lock": not is_proxy_ticker(ticker),
                        "status": (
                            "RECONCILED"
                            if confirmed_fill > 0
                            else "RECONCILED_ESTIMATED"
                        ),
                    })
                else:
                    logger.debug(
                        f"[Reconciliation Guard] Preserved local position for {ticker} "
                        "(empty broker snapshot was not independently trustworthy)"
                    )
                    
        # Calibration, execution audit, and full-exit lock commit atomically.
        # Quote-only refreshes intentionally do not create execution events.
        for event in execution_events:
            order_id = (
                f"RECON-{event['action']}-{event['ticker']}-"
                f"{now_str.replace(' ', 'T').replace(':', '')}"
            )
            record_execution_log(
                ticker=event["ticker"],
                side=event["side"],
                quantity=float(event["quantity"]),
                price=float(event["price"]),
                order_type="BROKER_RECONCILIATION",
                status=str(event.get("status") or "RECONCILED"),
                message=event["message"],
                order_id=order_id,
                timestamp=now_str,
                connection=conn,
            )
            if event.get("arm_exit_lock"):
                exit_cooldown.record_exit(
                    event["ticker"],
                    reason="RECONCILED_FULL_EXIT",
                    source="RECONCILIATION",
                    order_id=order_id,
                    connection=conn,
                )
        for item in intent_states:
            intent = item["intent"]
            state = item["state"]
            ticker = str(intent["ticker"]).upper()
            side = str(intent["side"]).upper()
            state_name = str(state.get("status") or "UNKNOWN").upper()
            terminal = bool(state.get("terminal"))
            baseline_qty = float(intent.get("baseline_quantity") or 0.0)
            current_broker_qty = float(
                (broker_holdings.get(ticker) or {}).get("quantity") or 0.0
            )
            if side == "BUY":
                newly_observed_applied = max(
                    0.0,
                    current_broker_qty - baseline_qty,
                )
            else:
                newly_observed_applied = max(
                    0.0,
                    baseline_qty - current_broker_qty,
                )
            requested_quantity = float(intent.get("quantity") or 0.0)
            applied_quantity = min(
                max(
                    float(intent.get("applied_quantity") or 0.0),
                    newly_observed_applied,
                ),
                requested_quantity,
            )
            terminal_filled_quantity = float(
                state.get("filled_quantity") or requested_quantity
            )
            expected_applied = min(
                requested_quantity,
                terminal_filled_quantity,
            )
            try:
                created_at = datetime.strptime(
                    str(intent.get("created_at") or ""),
                    "%Y-%m-%d %H:%M:%S",
                )
                intent_age_seconds = (
                    datetime.now() - created_at
                ).total_seconds()
            except (TypeError, ValueError):
                intent_age_seconds = 0.0
            is_stale_expired = intent_age_seconds >= 43_200.0
            is_abandoned_claimed = (
                str(intent.get("status") or "").upper() == "CLAIMED"
                and not str(intent.get("order_id") or "").strip()
                and intent_age_seconds >= 300.0
            )
            not_found_terminal = (
                state_name == "NOT_FOUND"
                and bool(state.get("query_complete"))
                and (
                    (
                        str(intent.get("status") or "").upper() == "CLAIMED"
                        and intent_age_seconds >= 300.0
                    )
                    or intent_age_seconds >= 43_200.0
                )
            )
            holdings_confirmed_complete = (
                requested_quantity > 0
                and applied_quantity + 1e-9 >= requested_quantity
                and state_name in {"UNKNOWN", "NOT_FOUND"}
            )
            should_clear = terminal and (
                state_name in {"CANCELLED", "REJECTED"}
                or (
                    state_name == "FILLED"
                    and applied_quantity + 1e-9 >= expected_applied
                )
            )
            should_clear = (
                should_clear
                or not_found_terminal
                or is_stale_expired
                or is_abandoned_claimed
                or holdings_confirmed_complete
            )
            if item["kind"] == "PROXY":
                if should_clear:
                    clear_proxy_order_intent(ticker, connection=conn)
                else:
                    update_proxy_order_intent_state(
                        ticker=ticker,
                        status=state_name,
                        filled_quantity=float(
                            state.get("filled_quantity") or 0.0
                        ),
                        applied_quantity=applied_quantity,
                        connection=conn,
                    )
            else:
                if should_clear:
                    clear_satellite_order_intent(
                        ticker,
                        side,
                        connection=conn,
                    )
                else:
                    update_satellite_order_intent_state(
                        ticker=ticker,
                        side=side,
                        status=state_name,
                        filled_quantity=float(
                            state.get("filled_quantity") or 0.0
                        ),
                        applied_quantity=applied_quantity,
                        connection=conn,
                    )
        conn.commit()

    _persist_broker_account_snapshot(broker_balance)
        
    return {
        "status": "success",
        "timestamp": now_str,
        "mode": broker_balance.get("mode", "LIVE_API"),
        "total_broker_equity": broker_balance.get("total_equity_usd", 0.0),
        "cash_available_usd": broker_balance.get("cash_available_usd", 0.0),
        "matched_count": matched_count,
        "discrepancies_count": len(discrepancies),
        "calibrations_count": len(calibrations),
        "discrepancies": discrepancies,
        "calibrations": calibrations
    }
