"""
Universal Smart Ticker Resolver & Multi-Language Stock Search Index.
"""
import re
from typing import Dict, Any, List

# Comprehensive Multi-Language Stock Index (US + KR Major Stocks)
STOCK_DIRECTORY = [
    # US Mega Tech & Semiconductors
    {"ticker": "NVDA", "name_kr": "엔비디아", "name_en": "NVIDIA Corp", "market": "NASDAQ"},
    {"ticker": "AAPL", "name_kr": "애플", "name_en": "Apple Inc", "market": "NASDAQ"},
    {"ticker": "MSFT", "name_kr": "마이크로소프트", "name_en": "Microsoft Corp", "market": "NASDAQ"},
    {"ticker": "AMZN", "name_kr": "아마존", "name_en": "Amazon.com Inc", "market": "NASDAQ"},
    {"ticker": "GOOGL", "name_kr": "구글 (알파벳 A)", "name_en": "Alphabet Inc Class A", "market": "NASDAQ"},
    {"ticker": "META", "name_kr": "메타 (페이스북)", "name_en": "Meta Platforms Inc", "market": "NASDAQ"},
    {"ticker": "TSLA", "name_kr": "테슬라", "name_en": "Tesla Inc", "market": "NASDAQ"},
    {"ticker": "AVGO", "name_kr": "브로드컴", "name_en": "Broadcom Inc", "market": "NASDAQ"},
    {"ticker": "TSM", "name_kr": "TSMC (대만반도체)", "name_en": "Taiwan Semiconductor", "market": "NYSE"},
    {"ticker": "AMD", "name_kr": "에이엠디", "name_en": "Advanced Micro Devices", "market": "NASDAQ"},
    {"ticker": "QCOM", "name_kr": "퀄컴", "name_en": "QUALCOMM Inc", "market": "NASDAQ"},
    {"ticker": "MU", "name_kr": "마이크론", "name_en": "Micron Technology", "market": "NASDAQ"},
    {"ticker": "INTC", "name_kr": "인텔", "name_en": "Intel Corp", "market": "NASDAQ"},
    {"ticker": "ARM", "name_kr": "암 홀딩스", "name_en": "Arm Holdings plc", "market": "NASDAQ"},
    {"ticker": "SMCI", "name_kr": "슈퍼마이크로", "name_en": "Super Micro Computer", "market": "NASDAQ"},
    {"ticker": "ASML", "name_kr": "에이에스엠엘", "name_en": "ASML Holding NV", "market": "NASDAQ"},
    
    # Growth & AI Leaders
    {"ticker": "PLTR", "name_kr": "팔란티어", "name_en": "Palantir Technologies", "market": "NYSE"},
    {"ticker": "LLY", "name_kr": "일라이 릴리", "name_en": "Eli Lilly and Co", "market": "NYSE"},
    {"ticker": "COST", "name_kr": "코스트코", "name_en": "Costco Wholesale", "market": "NASDAQ"},
    {"ticker": "VST", "name_kr": "비스트라 에너지", "name_en": "Vistra Corp", "market": "NYSE"},
    {"ticker": "CEG", "name_kr": "콘스텔레이션 에너지", "name_en": "Constellation Energy", "market": "NASDAQ"},
    {"ticker": "GEV", "name_kr": "지이 베르노바", "name_en": "GE Vernova Inc", "market": "NYSE"},
    {"ticker": "ETN", "name_kr": "이튼", "name_en": "Eaton Corp plc", "market": "NYSE"},
    {"ticker": "COIN", "name_kr": "코인베이스", "name_en": "Coinbase Global", "market": "NASDAQ"},
    {"ticker": "MSTR", "name_kr": "마이크로스트래티지", "name_en": "MicroStrategy Inc", "market": "NASDAQ"},
    {"ticker": "CRWD", "name_kr": "크라우드스트라이크", "name_en": "CrowdStrike Holdings", "market": "NASDAQ"},
    {"ticker": "HOOD", "name_kr": "로빈후드", "name_en": "Robinhood Markets", "market": "NASDAQ"},
    {"ticker": "RKLB", "name_kr": "로켓랩", "name_en": "Rocket Lab USA", "market": "NASDAQ"},
    {"ticker": "IONQ", "name_kr": "아이온큐", "name_en": "IonQ Inc", "market": "NYSE"},
    {"ticker": "NFLX", "name_kr": "넷플릭스", "name_en": "Netflix Inc", "market": "NASDAQ"},
    {"ticker": "BABA", "name_kr": "알리바바", "name_en": "Alibaba Group", "market": "NYSE"},

    # ETFs & Indexes
    {"ticker": "QQQ", "name_kr": "나스닥 100 ETF", "name_en": "Invesco QQQ Trust", "market": "NASDAQ"},
    {"ticker": "SPY", "name_kr": "S&P 500 ETF", "name_en": "SPDR S&P 500 ETF", "market": "NYSE"},
    {"ticker": "SOXX", "name_kr": "필라델피아 반도체 ETF", "name_en": "iShares Semiconductor ETF", "market": "NASDAQ"},
    {"ticker": "NVDA", "name_kr": "엔비디아", "name_en": "NVIDIA Corp", "market": "NASDAQ"},

    # Korean Major Bluechips
    {"ticker": "005930.KS", "name_kr": "삼성전자", "name_en": "Samsung Electronics", "market": "KOSPI"},
    {"ticker": "000660.KS", "name_kr": "SK하이닉스", "name_en": "SK Hynix", "market": "KOSPI"},
    {"ticker": "012450.KS", "name_kr": "한화에어로스페이스", "name_en": "Hanwha Aerospace", "market": "KOSPI"},
    {"ticker": "035420.KS", "name_kr": "네이버", "name_en": "NAVER Corp", "market": "KOSPI"},
    {"ticker": "035720.KS", "name_kr": "카카오", "name_en": "Kakao Corp", "market": "KOSPI"},
    {"ticker": "005380.KS", "name_kr": "현대차", "name_en": "Hyundai Motor", "market": "KOSPI"},
    {"ticker": "000270.KS", "name_kr": "기아", "name_en": "Kia Corp", "market": "KOSPI"},
    {"ticker": "373220.KS", "name_kr": "LG에너지솔루션", "name_en": "LG Energy Solution", "market": "KOSPI"},
    {"ticker": "247540.KQ", "name_kr": "에코프로비엠", "name_en": "EcoPro BM", "market": "KOSDAQ"},
    {"ticker": "086520.KQ", "name_kr": "에코프로", "name_en": "EcoPro", "market": "KOSDAQ"},
    {"ticker": "042700.KS", "name_kr": "한미반도체", "name_en": "Hanmi Semiconductor", "market": "KOSPI"}
]

KR_CODE_REGEX = re.compile(r'^\d{6}$')

def resolve_ticker(query: str) -> str:
    """
    Resolves raw user search input into a standardized Yahoo Finance ticker symbol.
    - Resolves Korean/English aliases (e.g. '애플' -> 'AAPL', '삼전' -> '005930.KS')
    - Formats 6-digit Korean codes ('005930' -> '005930.KS')
    - Cleans whitespace and upper-cases US tickers ('aapl' -> 'AAPL')
    """
    if not query:
        return "AMZN"
        
    q_clean = query.strip().upper()
    q_raw = query.strip()

    # 1. 6-digit Korean code check
    if KR_CODE_REGEX.match(q_raw):
        # Default to .KS if not specified
        return f"{q_raw}.KS"

    # 2. Check exact matches in directory
    for item in STOCK_DIRECTORY:
        if q_clean == item["ticker"].upper():
            return item["ticker"]
        if q_raw == item["name_kr"] or q_raw in item["name_kr"]:
            return item["ticker"]
        if q_clean == item["name_en"].upper() or q_clean in item["name_en"].upper():
            return item["ticker"]

    # 3. Special aliases / colloquial terms
    alias_map = {
        "삼전": "005930.KS",
        "하이닉스": "000660.KS",
        "에어로": "012450.KS",
        "마소": "MSFT",
        "구글": "GOOGL",
        "알파벳": "GOOGL",
        "페이스북": "META",
        "메타": "META",
        "암": "ARM",
        "슈마컴": "SMCI",
        "비스트라": "VST",
        "베르노바": "GEV",
        "릴리": "LLY",
        "팔란": "PLTR",
        "코베": "COIN",
        "마스트": "MSTR"
    }
    if q_raw in alias_map:
        return alias_map[q_raw]

    # 4. Fallback to clean uppercase symbol
    return q_clean

def search_ticker_suggestions(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Returns live autocomplete suggestions matching the query in ticker, Korean name, or English name.
    """
    if not query or len(query.strip()) == 0:
        return STOCK_DIRECTORY[:limit]
        
    q_lower = query.strip().lower()
    q_upper = query.strip().upper()

    matches = []
    
    # Priority 1: Ticker exact/prefix match
    for item in STOCK_DIRECTORY:
        if item["ticker"].startswith(q_upper):
            matches.append(item)
            
    # Priority 2: Korean or English name match
    for item in STOCK_DIRECTORY:
        if item not in matches:
            if q_lower in item["name_kr"].lower() or q_lower in item["name_en"].lower():
                matches.append(item)

    # Priority 3: Fallback custom ticker if valid format
    if not matches and re.match(r'^[A-Za-z0-9.\^=-]{1,15}$', q_upper):
        matches.append({
            "ticker": q_upper,
            "name_kr": f"직접 검색 ({q_upper})",
            "name_en": "Custom Global Ticker",
            "market": "GLOBAL"
        })

    return matches[:limit]
