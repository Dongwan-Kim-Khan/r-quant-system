"""
Legacy Facade for Database Access.
Delegates to al_sangmoo.infrastructure.persistence.
"""
from al_sangmoo.infrastructure.persistence import (
    get_connection,
    init_database,
    get_db,
    init_db,
    save_macro_history_record,
    get_latest_macro_record,
    add_portfolio_buy,
    record_portfolio_sell,
    reset_all_holdings,
    get_live_portfolio,
    save_recommendation_matrix_record,
    get_recommendations_matrix,
    close_portfolio_position,
    clear_portfolio
)
