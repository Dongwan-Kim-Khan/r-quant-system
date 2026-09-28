"""
Exchange Router Module

Resolves US equity tickers to their respective execution exchange codes for KIS OpenAPI.
Valid KIS exchange codes: "NASD", "NYSE", "AMEX".
"""

from typing import Set

AMEX_EXCHANGE_TICKERS: Set[str] = {
    "QLD", "SPY", "ITA", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLV", "XLY"
}

NYSE_EXCHANGE_TICKERS: Set[str] = {
    "ABBV", "ABT", "ALB", "APD", "AXP", "BA", "BAC", "BAH", "BLK", "BMY",
    "BX", "C", "CAT", "CB", "CCJ", "CMG", "CMI", "COP", "CRM", "CTVA",
    "CVX", "DD", "DE", "DELL", "DHR", "DIS", "DOW", "DUK", "ECL", "EMR",
    "EOG", "ETN", "FCX", "FDX", "GD", "GE", "GEV", "GS", "GWW", "HD",
    "HEI", "HII", "HPQ", "HWM", "IBM", "IONQ", "ITW", "JNJ", "JPM", "KKR",
    "KO", "LHX", "LLY", "LMT", "LOW", "MA", "MCD", "MDT", "MO", "MPC",
    "MRK", "MS", "NEE", "NEM", "NET", "NKE", "NOC", "NOW", "OKLO", "ORCL",
    "OXY", "PFE", "PG", "PH", "PM", "PSX", "RBLX", "ROK", "RTX", "SCCO",
    "SCHW", "SHW", "SLB", "SNOW", "SO", "SPOT", "SRE", "SYK", "T", "TDG",
    "TGT", "TJX", "TMO", "TSM", "TT", "TXT", "UNH", "UNP", "UPS", "V",
    "VALE", "VLO", "VST", "VZ", "WFC", "XOM", "ZTS"
}

VALID_EXCHANGE_CODES = ("NASD", "NYSE", "AMEX")


def resolve_order_exchange(ticker: str) -> str:
    """
    Resolves the KIS order exchange code for a given ticker symbol.

    Args:
        ticker: Ticker symbol (e.g. "AAPL", "QLD", "CAT").

    Returns:
        One of "AMEX", "NYSE", or "NASD" (default for NASDAQ and others).
    """
    clean = str(ticker or "").strip().upper()
    if clean in AMEX_EXCHANGE_TICKERS:
        return "AMEX"
    if clean in NYSE_EXCHANGE_TICKERS:
        return "NYSE"
    return "NASD"
