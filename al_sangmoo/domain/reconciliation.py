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
    get_connection,
    init_database,
    get_live_portfolio
)

logger = logging.getLogger(__name__)
_RECONCILE_LOCK = threading.Lock()


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
    if broker_balance.get("status") != "success":
        return {
            "status": "error",
            "timestamp": now_str,
            "message": f"Broker balance inquiry failed: {broker_balance.get('message')}",
            "discrepancies": []
        }
        
    broker_holdings = {h["ticker"].upper(): h for h in broker_balance.get("holdings", [])}
    
    # 2. Fetch local SQLite holdings
    local_portfolio = get_live_portfolio()
    local_holdings = {h["ticker"].upper(): h for h in local_portfolio.get("holdings", [])}
    
    discrepancies: List[Dict[str, Any]] = []
    calibrations: List[Dict[str, Any]] = []
    matched_count = 0
    
    all_tickers = set(broker_holdings.keys()).union(set(local_holdings.keys()))
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
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
                        new_val = b_qty * b_cur
                        new_cost = b_qty * b_avg
                        pnl_pct = ((b_cur - b_avg) / b_avg * 100) if b_avg > 0 else 0.0
                        pnl_amt = new_val - new_cost
                        cursor.execute("""
                        UPDATE my_portfolio
                        SET current_price = ?, current_value = ?, pnl_pct = ?, pnl_amount = ?
                        WHERE id = ?
                        """, (b_cur, new_val, pnl_pct, pnl_amt, l_item["id"]))
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
                    b_cur = float(b_item.get("current_price", b_avg))
                    new_val = b_qty * b_cur
                    new_cost = b_qty * b_avg
                    pnl_pct = ((b_cur - b_avg) / b_avg * 100) if b_avg > 0 else 0.0
                    pnl_amt = new_val - new_cost
                    target_pr = derive_target_price(b_avg)
                    stop_pr = derive_stop_price(b_avg)
                    partial_tp = derive_partial_tp_price(b_avg)
                    
                    cursor.execute("""
                    UPDATE my_portfolio
                    SET buy_price = ?, current_price = ?, total_cost = ?,
                        current_value = ?, pnl_pct = ?, pnl_amount = ?,
                        target_price = ?, stop_loss_price = ?, partial_tp_price = ?,
                        exit_advice = ?
                    WHERE id = ?
                    """, (
                        b_avg, b_cur, new_cost,
                        new_val, pnl_pct, pnl_amt,
                        target_pr, stop_pr, partial_tp,
                        f"실계좌 평단가 동기화 보정 ({now_str})", l_item["id"]
                    ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "UPDATE_AVG_PRICE",
                        "old_avg_price": l_avg,
                        "new_avg_price": b_avg
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
                    # Update local holding to match broker reality
                    effective_avg = b_avg if b_avg > 0 else l_avg
                    b_cur = float(b_item.get("current_price", effective_avg))
                    new_val = b_qty * b_cur
                    new_cost = b_qty * effective_avg
                    pnl_pct = ((b_cur - effective_avg) / effective_avg * 100) if effective_avg > 0 else 0.0
                    pnl_amt = new_val - new_cost
                    target_pr = derive_target_price(effective_avg)
                    stop_pr = derive_stop_price(effective_avg)
                    partial_tp = derive_partial_tp_price(effective_avg)
                    
                    cursor.execute("""
                    UPDATE my_portfolio
                    SET quantity = ?, buy_price = ?, current_price = ?, total_cost = ?,
                        current_value = ?, pnl_pct = ?, pnl_amount = ?,
                        target_price = ?, stop_loss_price = ?, partial_tp_price = ?,
                        exit_advice = ?
                    WHERE id = ?
                    """, (
                        b_qty, effective_avg, b_cur, new_cost,
                        new_val, pnl_pct, pnl_amt,
                        target_pr, stop_pr, partial_tp,
                        f"실계좌 대조 보정 ({now_str})", l_item["id"]
                    ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "UPDATE_QUANTITY_AND_PRICE",
                        "old_qty": l_qty,
                        "new_qty": b_qty,
                        "old_avg_price": l_avg,
                        "new_avg_price": effective_avg
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
                    b_cur = float(b_item.get("current_price", b_avg))
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
                    
            # Sub-case 4: In Local SQLite only (Closed on broker or sold externally)
            elif l_item and not b_item:
                discrepancy = {
                    "ticker": ticker,
                    "type": "LOCAL_ONLY",
                    "broker_qty": 0.0,
                    "local_qty": l_qty
                }
                discrepancies.append(discrepancy)
                
                # Defensive safety: Only auto-close if broker returned at least 1 other valid holding,
                # preventing wiping out local DB during network/broker query glitches.
                if auto_calibrate and len(broker_holdings) > 0:
                    cursor.execute("""
                    UPDATE my_portfolio
                    SET status = 'SOLD', sell_date = ?, exit_advice = ?
                    WHERE id = ?
                    """, (
                        datetime.now().strftime("%Y-%m-%d"),
                        f"실계좌 청산 감지 보정 ({now_str})", l_item["id"]
                    ))
                    calibrations.append({
                        "ticker": ticker,
                        "action": "CLOSE_LOCAL_POSITION",
                        "id": l_item["id"]
                    })
                else:
                    logger.debug(f"[Reconciliation Guard] Preserved local position for {ticker} (Broker returned 0 total holdings)")
                    
        conn.commit()

        
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
