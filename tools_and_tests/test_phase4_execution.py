"""
Comprehensive Phase 4 Test Suite:
Broker Gateway, Pre-Trade Guardrails, Macro Circuit Breakers & Online Backup Snapshots.
"""
import os
import sys
import time

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from al_sangmoo.domain.risk.order_guardrail import validate_pre_trade_guardrail
from al_sangmoo.domain.risk.macro_guardrail import evaluate_macro_circuit_breaker
from al_sangmoo.infrastructure.brokers.paper_broker import PaperTradingBroker
from al_sangmoo.infrastructure.backup import create_sqlite_backup, list_backups
import db_manager

def test_pre_trade_guardrails():
    print("\n[Test 1] Verifying Pre-Trade Risk Guardrails & Sanity Validation...")
    
    mock_holdings = [
        {"ticker": "NVDA", "current_value": 10000.0}
    ]
    total_equity = 100000.0

    # 1. Normal valid order
    val_ok = validate_pre_trade_guardrail(
        ticker="AMZN", price=250.0, quantity=10.0,
        total_equity=total_equity, active_holdings=mock_holdings, msi_score=45.0
    )
    assert val_ok["allowed"] is True
    print(f"  - Valid Order ($2,500 AMZN): PASSED ({val_ok['reason']})")

    # 2. Oversized order (Exceeds 25% single-asset cap)
    val_oversized = validate_pre_trade_guardrail(
        ticker="AMZN", price=250.0, quantity=150.0, # $37,500 > 25%
        total_equity=total_equity, active_holdings=mock_holdings, msi_score=45.0
    )
    assert val_oversized["allowed"] is False
    assert "초과" in val_oversized["reason"]
    print(f"  - Oversized Order ($37,500 > 25% Cap): REJECTED as expected ({val_oversized['reason']})")

    # 3. CASH_EXIT Macro Regime (MSI >= 75)
    val_cash_exit = validate_pre_trade_guardrail(
        ticker="AMZN", price=250.0, quantity=2.0,
        total_equity=total_equity, active_holdings=mock_holdings, msi_score=80.0
    )
    assert val_cash_exit["allowed"] is False
    assert "CASH_EXIT" in val_cash_exit["reason"]
    print(f"  - Macro CASH_EXIT Mode (MSI 80pt): REJECTED as expected ({val_cash_exit['reason']})")
    print("  -> PASSED: Pre-trade guardrail protection verified.")

def test_macro_circuit_breakers():
    print("\n[Test 2] Verifying Macro Circuit Breakers & Defensive Trailing Stops...")
    
    mock_holdings = [
        {"id": 1, "ticker": "AMZN", "buy_price": 200.0, "current_price": 250.0, "stop_loss_price": 194.0}
    ]

    # 1. Systemic Crisis Mode (MSI >= 75)
    cb_crisis = evaluate_macro_circuit_breaker(msi_score=82.0, holdings=mock_holdings)
    assert cb_crisis["macro_stance"] == "CASH_EXIT"
    assert cb_crisis["circuit_breaker_active"] is True
    assert cb_crisis["actions"][0]["action"] == "FORCE_LIQUIDATE"
    print(f"  - Systemic Shock (MSI 82pt): Generated {cb_crisis['actions'][0]['action']} action.")

    # 2. Defensive Mode (MSI 65)
    cb_defense = evaluate_macro_circuit_breaker(msi_score=65.0, holdings=mock_holdings)
    assert cb_defense["macro_stance"] == "DEFENSE_HOLD"
    assert cb_defense["circuit_breaker_active"] is True
    assert cb_defense["actions"][0]["action"] == "TIGHTEN_TRAILING_STOP"
    assert cb_defense["actions"][0]["adjusted_stop_price"] == 246.25 # 250 * 0.985
    print(f"  - Defensive Regime (MSI 65pt): Adjusted Stop ${cb_defense['actions'][0]['original_stop_price']} -> ${cb_defense['actions'][0]['adjusted_stop_price']:,.2f}")
    print("  -> PASSED: Automated macro circuit breaker and defensive trailing stop verified.")

def test_paper_broker_adapter():
    print("\n[Test 3] Verifying Paper Trading Execution Gateway Adapter...")
    broker = PaperTradingBroker(initial_cash=100000.0)
    db_manager.reset_all_holdings()

    # Buy order fill
    buy_fill = broker.submit_buy_order(ticker="LLY", price=950.0, quantity=2.0)
    assert buy_fill["status"] == "FILLED"
    pos_id = buy_fill["position_id"]
    print(f"  - Paper Buy Execution: Filled 2 shares LLY at ${buy_fill['fill_price']:,.2f} (Pos #{pos_id})")

    # Balance check
    balance = broker.get_account_balance()
    assert balance["total_invested"] > 0
    assert balance["total_equity"] > 0
    print(f"  - Account Equity: ${balance['total_equity']:,.2f} (Cash: ${balance['cash_available']:,.2f})")

    # Sell order fill
    sell_fill = broker.submit_sell_order(position_id=pos_id, price=1000.0, reason="PAPER_TP")
    assert sell_fill["status"] == "FILLED"
    print(f"  - Paper Sell Execution: Position #{pos_id} closed at ${sell_fill['fill_price']:,.2f}")

    db_manager.reset_all_holdings()
    print("  -> PASSED: Paper broker execution gateway verified.")

def test_sqlite_snapshot_backup():
    print("\n[Test 4] Verifying Online Non-Blocking SQLite Snapshot Backup...")
    res = create_sqlite_backup()
    assert res["status"] == "success"
    assert res["size_bytes"] > 0
    assert os.path.exists(res["file_path"])
    print(f"  - Created Snapshot Archive: {res['backup_file']} ({round(res['size_bytes']/1024, 2)} KB)")

    backup_list = list_backups()
    assert len(backup_list) > 0
    print(f"  - Available Backup Snapshots: {len(backup_list)} archives registered.")
    print("  -> PASSED: Online SQLite backup engine operational.")

if __name__ == "__main__":
    print("======================================================================")
    print("  R-SANGMOO QUANT PLATFORM: PHASE 4 EXECUTION & RISK TEST SUITE")
    print("======================================================================")
    
    test_pre_trade_guardrails()
    test_macro_circuit_breakers()
    test_paper_broker_adapter()
    test_sqlite_snapshot_backup()
    
    print("\n======================================================================")
    print("  ALL PHASE 4 EXECUTION & RISK TESTS PASSED! (100% GREEN)")
    print("======================================================================")
