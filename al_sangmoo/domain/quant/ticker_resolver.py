"""
Universal Smart Ticker Resolver & Multi-Language Stock Search Index.
"""
import re
from typing import Dict, Any, List

# Comprehensive Multi-Language Stock Index (60 Global & Korean Leaders)
STOCK_DIRECTORY = [
    # Mega Tech & AI
    {"ticker": "NVDA", "name_kr": "엔비디아", "name_en": "NVIDIA Corp", "market": "NASDAQ"},
    {"ticker": "MSFT", "name_kr": "마이크로소프트 (마소)", "name_en": "Microsoft Corp", "market": "NASDAQ"},
    {"ticker": "AMZN", "name_kr": "아마존", "name_en": "Amazon.com Inc", "market": "NASDAQ"},
    {"ticker": "AAPL", "name_kr": "애플", "name_en": "Apple Inc", "market": "NASDAQ"},
    {"ticker": "GOOGL", "name_kr": "구글 (알파벳)", "name_en": "Alphabet Inc Class A", "market": "NASDAQ"},
    {"ticker": "META", "name_kr": "메타 (페이스북)", "name_en": "Meta Platforms Inc", "market": "NASDAQ"},
    {"ticker": "TSLA", "name_kr": "테슬라", "name_en": "Tesla Inc", "market": "NASDAQ"},
    {"ticker": "PLTR", "name_kr": "팔란티어", "name_en": "Palantir Technologies", "market": "NYSE"},
    {"ticker": "ORCL", "name_kr": "오라클", "name_en": "Oracle Corp", "market": "NYSE"},
    {"ticker": "CRM", "name_kr": "세일즈포스", "name_en": "Salesforce Inc", "market": "NYSE"},

    # Semiconductors Design, Foundry & Equipment
    {"ticker": "TSM", "name_kr": "TSMC (대만반도체)", "name_en": "Taiwan Semiconductor", "market": "NYSE"},
    {"ticker": "AVGO", "name_kr": "브로드컴", "name_en": "Broadcom Inc", "market": "NASDAQ"},
    {"ticker": "AMD", "name_kr": "에이엠디", "name_en": "Advanced Micro Devices", "market": "NASDAQ"},
    {"ticker": "QCOM", "name_kr": "퀄컴", "name_en": "QUALCOMM Inc", "market": "NASDAQ"},
    {"ticker": "ARM", "name_kr": "암 홀딩스", "name_en": "Arm Holdings plc", "market": "NASDAQ"},
    {"ticker": "MU", "name_kr": "마이크론", "name_en": "Micron Technology", "market": "NASDAQ"},
    {"ticker": "INTC", "name_kr": "인텔", "name_en": "Intel Corp", "market": "NASDAQ"},
    {"ticker": "ASML", "name_kr": "에이에스엠엘", "name_en": "ASML Holding NV", "market": "NASDAQ"},
    {"ticker": "AMAT", "name_kr": "어플라이드 머티어리얼즈", "name_en": "Applied Materials", "market": "NASDAQ"},
    {"ticker": "LRCX", "name_kr": "램리서치", "name_en": "Lam Research", "market": "NASDAQ"},
    {"ticker": "KLAC", "name_kr": "케이엘에이", "name_en": "KLA Corp", "market": "NASDAQ"},
    {"ticker": "SMCI", "name_kr": "슈퍼마이크로", "name_en": "Super Micro Computer", "market": "NASDAQ"},
    {"ticker": "TXN", "name_kr": "텍사스 인스트루먼트", "name_en": "Texas Instruments", "market": "NASDAQ"},

    # Power, Energy & Nuclear
    {"ticker": "VST", "name_kr": "비스트라 에너지", "name_en": "Vistra Corp", "market": "NYSE"},
    {"ticker": "CEG", "name_kr": "콘스텔레이션 에너지", "name_en": "Constellation Energy", "market": "NASDAQ"},
    {"ticker": "GEV", "name_kr": "지이 베르노바", "name_en": "GE Vernova Inc", "market": "NYSE"},
    {"ticker": "ETN", "name_kr": "이튼", "name_en": "Eaton Corp plc", "market": "NYSE"},
    {"ticker": "CCJ", "name_kr": "카메코 (우라늄)", "name_en": "Cameco Corp", "market": "NYSE"},
    {"ticker": "OKLO", "name_kr": "오클로 (원자력)", "name_en": "Oklo Inc", "market": "NYSE"},
    {"ticker": "XOM", "name_kr": "엑슨모빌", "name_en": "Exxon Mobil Corp", "market": "NYSE"},
    {"ticker": "CVX", "name_kr": "셰브론", "name_en": "Chevron Corp", "market": "NYSE"},

    # Cybersecurity, Cloud & Space
    {"ticker": "CRWD", "name_kr": "크라우드스트라이크", "name_en": "CrowdStrike Holdings", "market": "NASDAQ"},
    {"ticker": "PANW", "name_kr": "팔로알토 네트웍스", "name_en": "Palo Alto Networks", "market": "NASDAQ"},
    {"ticker": "NOW", "name_kr": "서비스나우", "name_en": "ServiceNow Inc", "market": "NYSE"},
    {"ticker": "RKLB", "name_kr": "로켓랩 (우주항공)", "name_en": "Rocket Lab USA", "market": "NASDAQ"},
    {"ticker": "IONQ", "name_kr": "아이온큐 (양자컴퓨터)", "name_en": "IonQ Inc", "market": "NYSE"},
    {"ticker": "SNOW", "name_kr": "스노우플레이크", "name_en": "Snowflake Inc", "market": "NYSE"},
    {"ticker": "NET", "name_kr": "클라우드플레어", "name_en": "Cloudflare Inc", "market": "NYSE"},

    # Financials, Fintech & Crypto
    {"ticker": "JPM", "name_kr": "JP모건 체이스", "name_en": "JPMorgan Chase & Co", "market": "NYSE"},
    {"ticker": "V", "name_kr": "비자", "name_en": "Visa Inc", "market": "NYSE"},
    {"ticker": "MA", "name_kr": "마스터카드", "name_en": "Mastercard Inc", "market": "NYSE"},
    {"ticker": "COIN", "name_kr": "코인베이스", "name_en": "Coinbase Global", "market": "NASDAQ"},
    {"ticker": "HOOD", "name_kr": "로빈후드", "name_en": "Robinhood Markets", "market": "NASDAQ"},
    {"ticker": "MSTR", "name_kr": "마이크로스트래티지", "name_en": "MicroStrategy Inc", "market": "NASDAQ"},

    # Healthcare & Consumers
    {"ticker": "LLY", "name_kr": "일라이 릴리 (비만치료제)", "name_en": "Eli Lilly and Co", "market": "NYSE"},
    {"ticker": "UNH", "name_kr": "유나이티드헬스", "name_en": "UnitedHealth Group", "market": "NYSE"},
    {"ticker": "JNJ", "name_kr": "존슨앤존슨", "name_en": "Johnson & Johnson", "market": "NYSE"},
    {"ticker": "COST", "name_kr": "코스트코", "name_en": "Costco Wholesale", "market": "NASDAQ"},
    {"ticker": "WMT", "name_kr": "월마트", "name_en": "Walmart Inc", "market": "NYSE"},
    {"ticker": "NFLX", "name_kr": "넷플릭스", "name_en": "Netflix Inc", "market": "NASDAQ"},
    {"ticker": "NKE", "name_kr": "나이키", "name_en": "Nike Inc", "market": "NYSE"},
    {"ticker": "DIS", "name_kr": "월트 디즈니", "name_en": "Walt Disney Co", "market": "NYSE"},

    # Global Defense & Aerospace
    {"ticker": "LMT", "name_kr": "록히드마틴 (F-35)", "name_en": "Lockheed Martin Corp", "market": "NYSE"},
    {"ticker": "RTX", "name_kr": "RTX (패트리어트/레이시온)", "name_en": "RTX Corp", "market": "NYSE"},
    {"ticker": "NOC", "name_kr": "노스롭 그루먼 (B-21)", "name_en": "Northrop Grumman Corp", "market": "NYSE"},
    {"ticker": "GE", "name_kr": "GE 에어로스페이스 (항공엔진)", "name_en": "GE Aerospace", "market": "NYSE"},

    # AI AdTech & Medical Robotics & Infrastructure
    {"ticker": "APP", "name_kr": "앱러빈 (AI 광고)", "name_en": "AppLovin Corp", "market": "NASDAQ"},
    {"ticker": "MRVL", "name_kr": "마벨 테크놀로지 (AI 커스텀칩)", "name_en": "Marvell Technology", "market": "NASDAQ"},
    {"ticker": "ISRG", "name_kr": "인튜이티브 서지컬 (다빈치 로봇)", "name_en": "Intuitive Surgical", "market": "NASDAQ"},

    # Korean Global Leaders
    {"ticker": "005930.KS", "name_kr": "삼성전자", "name_en": "Samsung Electronics", "market": "KOSPI"},
    {"ticker": "000660.KS", "name_kr": "SK하이닉스", "name_en": "SK Hynix", "market": "KOSPI"},
    {"ticker": "012450.KS", "name_kr": "한화에어로스페이스", "name_en": "Hanwha Aerospace", "market": "KOSPI"},

    # ETFs & Indexes
    {"ticker": "QQQ", "name_kr": "나스닥 100 ETF", "name_en": "Invesco QQQ Trust", "market": "NASDAQ"},
    {"ticker": "SPY", "name_kr": "S&P 500 ETF", "name_en": "SPDR S&P 500 ETF", "market": "NYSE"},
    {"ticker": "SOXX", "name_kr": "필라델피아 반도체 ETF", "name_en": "iShares Semiconductor ETF", "market": "NASDAQ"}
]

KR_CODE_REGEX = re.compile(r'^\d{6}$')

def _get_cached_price(ticker: str) -> float:
    try:
        from pathlib import Path
        import json
        c_path = Path(__file__).resolve().parents[2] / "data" / "charts" / f"{ticker.upper()}.json"
        if c_path.exists():
            with open(c_path, 'r', encoding='utf-8') as f:
                c_data = json.load(f)
                return float(c_data.get("latest_close") or (c_data.get("candles", [])[-1]["close"] if c_data.get("candles") else 0.0))
    except Exception:
        pass
    return 0.0

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
        # Check if exists in directory with .KQ or .KS
        for item in STOCK_DIRECTORY:
            if item["ticker"].startswith(q_raw):
                return item["ticker"]
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
        "한화에어로": "012450.KS",
        "한화에어로스페이스": "012450.KS",
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
    Guarantees 'name' and 'price' fields are present for UI compatibility.
    """
    if not query or len(query.strip()) == 0:
        matches = list(STOCK_DIRECTORY[:limit])
    else:
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

    enriched = []
    for item in matches[:limit]:
        entry = dict(item)
        entry["name"] = f"{item.get('name_kr', '')} ({item.get('name_en', '')})".strip() or item.get("ticker", "")
        entry["price"] = _get_cached_price(item["ticker"])
        enriched.append(entry)

    return enriched
