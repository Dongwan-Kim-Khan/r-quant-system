import os
import sys
import json
import subprocess
import re
from datetime import datetime

# Windows encoding fix
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

CHANNEL_URL = "https://www.youtube.com/@wepoll_original/streams"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wepoll_latest_stream.json")

# Structured Sector Universe with Representative Market Leaders
SECTOR_UNIVERSE = {
    "AI_SEMICONDUCTOR": {
        "name": "AI 반도체 및 하드웨어 인프라",
        "keywords": ["반도체", "엔비디아", "AI", "인프라", "칩", "GPU", "파운드리", "HBM", "데이터센터", "SEMICONDUCTOR", "NVIDIA", "CHIP"],
        "leaders": ["NVDA", "AVGO", "AMD", "QCOM", "MRVL", "MU", "ARM", "SMCI", "000660.KS"]
    },
    "BIGTECH_CLOUD": {
        "name": "빅테크 및 엔터프라이즈 클라우드",
        "keywords": ["빅테크", "소프트웨어", "클라우드", "마이크로소프트", "구글", "아마존", "애플", "메타", "SaaS", "AI서비스", "TECH"],
        "leaders": ["MSFT", "GOOGL", "AMZN", "AAPL", "META", "NOW", "PLTR", "PANW", "CRWD"]
    },
    "BIOTECH_HEALTHCARE": {
        "name": "바이오 및 차세대 헬스케어",
        "keywords": ["바이오", "헬스케어", "비만치료제", "일라이릴리", "노보노디스크", "제약", "임상", "FDA", "GLP-1", "BIOTECH", "HEALTHCARE"],
        "leaders": ["LLY", "VRTX", "ISRG", "UNH", "ABBV"]
    },
    "ENERGY_POWER_INFRA": {
        "name": "전력 인프라 및 에너지/원전",
        "keywords": ["전력", "원전", "에너지", "유가", "SMR", "변압기", "인프라", "유틸리티", "원자력", "ENERGY", "OIL", "POWER"],
        "leaders": ["VST", "CEG", "GEV", "ETN", "XOM", "CVX"]
    },
    "DEFENSE_AEROSPACE": {
        "name": "방위산업 및 항공우주",
        "keywords": ["방산", "지정학", "안보", "우주", "항공", "DEFENSE", "AEROSPACE"],
        "leaders": ["RTX", "LMT", "012450.KS"]
    },
    "CONSUMER_RETAIL": {
        "name": "필수 소비재 및 대형 유통",
        "keywords": ["소비", "유통", "소비자", "인플레", "실적", "리테일", "코스트코", "월마트", "RETAIL", "CONSUMER"],
        "leaders": ["COST", "WMT", "AMZN", "TGT"]
    },
    "ROBOTICS_MOBILITY": {
        "name": "로보틱스 및 자율주행 모빌리티",
        "keywords": ["로봇", "자율주행", "휴머노이드", "테슬라", "FSD", "ROBOT", "MOBILITY", "AUTONOMOUS"],
        "leaders": ["TSLA", "ISRG", "SYM", "UBER"]
    }
}

# Direct Name Mapping
STOCK_MAP = {
    "아마존": "AMZN", "AMAZON": "AMZN", "AMZN": "AMZN",
    "일라이릴리": "LLY", "릴리": "LLY", "LLY": "LLY",
    "엔비디아": "NVDA", "NVIDIA": "NVDA", "NVDA": "NVDA",
    "테슬라": "TSLA", "TESLA": "TSLA", "TSLA": "TSLA",
    "애플": "AAPL", "APPLE": "AAPL", "AAPL": "AAPL",
    "마이크로소프트": "MSFT", "MSFT": "MSFT",
    "메타": "META", "META": "META",
    "구글": "GOOGL", "알파벳": "GOOGL", "GOOGL": "GOOGL",
    "브로드컴": "AVGO", "AVGO": "AVGO",
    "코스트코": "COST", "COST": "COST",
    "AMD": "AMD", "퀄컴": "QCOM", "팔란티어": "PLTR",
    "슈퍼마이크로": "SMCI", "넷플릭스": "NFLX", "ASML": "ASML",
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "한화에어로스페이스": "012450.KS"
}

def analyze_macro_and_sectors(title, tags, description):
    """
    Extracts high-level macro narrative and identifies target focus sectors.
    """
    combined_text = f"{title} {' '.join(tags)} {description}".upper()
    
    # 1. Detect Macro Theme
    macro_themes = []
    if any(k in combined_text for k in ["금리", "연준", "FOMC", "파월", "국채", "INTEREST", "FED", "RATE"]):
        macro_themes.append("금리 경로 및 통화정책 영향권")
    if any(k in combined_text for k in ["유가", "원자재", "인플레", "물가", "OIL", "CPI", "INFLATION"]):
        macro_themes.append("인플레이션 및 원자재 변동성")
    if any(k in combined_text for k in ["실적", "어닝", "가이던스", "EARNINGS", "REVENUE"]):
        macro_themes.append("기업 실적 차별화 장세")
    if any(k in combined_text for k in ["성장", "빅테크", "주도주", "GROWTH"]):
        macro_themes.append("주도 성장주 수급 집중")
        
    if not macro_themes:
        macro_themes.append("거시 매크로 관망 및 주도 섹터 선별 국면")
        
    # 2. Identify Focus Sectors
    active_sectors = []
    candidate_tickers = []
    
    for sec_key, sec_data in SECTOR_UNIVERSE.items():
        score = 0
        for kw in sec_data["keywords"]:
            if kw.upper() in combined_text:
                score += 1
        if score > 0:
            active_sectors.append({
                "key": sec_key,
                "name": sec_data["name"],
                "score": score,
                "leaders": sec_data["leaders"]
            })
            for leader in sec_data["leaders"]:
                if leader not in candidate_tickers:
                    candidate_tickers.append(leader)
                    
    # Sort active sectors by relevance
    active_sectors = sorted(active_sectors, key=lambda x: x["score"], reverse=True)
    
    # Default fallback to top key growth sectors if none explicitly matched
    if not active_sectors:
        default_keys = ["AI_SEMICONDUCTOR", "BIGTECH_CLOUD", "BIOTECH_HEALTHCARE"]
        for k in default_keys:
            active_sectors.append({
                "key": k,
                "name": SECTOR_UNIVERSE[k]["name"],
                "score": 1,
                "leaders": SECTOR_UNIVERSE[k]["leaders"]
            })
            for leader in SECTOR_UNIVERSE[k]["leaders"]:
                if leader not in candidate_tickers:
                    candidate_tickers.append(leader)
                    
    # 3. Extract directly mentioned tickers
    direct_tickers = []
    for name, ticker in STOCK_MAP.items():
        if name.upper() in combined_text:
            if ticker not in direct_tickers:
                direct_tickers.append(ticker)
                
    # Prioritize direct tickers at the very top of candidate list
    final_candidates = direct_tickers + [t for t in candidate_tickers if t not in direct_tickers]
    
    return {
        "macro_narrative": " / ".join(macro_themes),
        "focus_sectors": [s["name"] for s in active_sectors[:3]],
        "active_sector_data": active_sectors[:3],
        "direct_tickers": direct_tickers,
        "screen_candidates": final_candidates[:15]
    }

def fetch_latest_wepoll_stream():
    """
    Fetches the latest live stream recording from @wepoll_original/streams and analyzes macro/sectors.
    """
    print(f"[Live Stream Parser] Fetching latest broadcast from {CHANNEL_URL}...")
    
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "-J",
        "--playlist-items", "1:3",
        CHANNEL_URL
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=25)
        if res.returncode != 0:
            return load_fallback_cache()
            
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        if not entries:
            return load_fallback_cache()
            
        latest = entries[0]
        v_id = latest.get("id")
        title = latest.get("title", "")
        url = f"https://www.youtube.com/watch?v={v_id}"
        
        detail_cmd = [
            sys.executable, "-m", "yt_dlp",
            "-j",
            "--skip-download",
            url
        ]
        detail_res = subprocess.run(detail_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=25)
        tags = []
        description = ""
        if detail_res.returncode == 0:
            try:
                detail_data = json.loads(detail_res.stdout)
                tags = detail_data.get("tags") or []
                description = detail_data.get("description") or ""
            except Exception:
                pass
                
        analysis = analyze_macro_and_sectors(title, tags, description)
        
        payload = {
            "video_id": v_id,
            "title": title,
            "url": url,
            "macro_narrative": analysis["macro_narrative"],
            "focus_sectors": analysis["focus_sectors"],
            "active_sector_data": analysis["active_sector_data"],
            "direct_tickers": analysis["direct_tickers"],
            "screen_candidates": analysis["screen_candidates"],
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
        print(f"[Live Stream Parser] Stream: '{title}'")
        print(f"  - Macro Flow: {payload['macro_narrative']}")
        print(f"  - Focus Sectors: {', '.join(payload['focus_sectors'])}")
        print(f"  - Sector Leaders to Screen: {', '.join(payload['screen_candidates'])}")
        return payload
        
    except Exception as e:
        print(f"[Live Stream Parser] Warning: {e}, using cached stream profile.")
        return load_fallback_cache()

def load_fallback_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "video_id": "latest_live",
        "title": "임계점 넘어가는 금리와 유가 | 성장 vs 금리부담 (바이킹스 라이브)",
        "url": "https://www.youtube.com/@wepoll_original/streams",
        "macro_narrative": "금리 경로 및 통화정책 영향권 / 주도 성장주 수급 집중",
        "focus_sectors": ["AI 반도체 및 하드웨어 인프라", "빅테크 및 엔터프라이즈 클라우드", "바이오 및 차세대 헬스케어"],
        "active_sector_data": [],
        "direct_tickers": ["AMZN", "LLY", "NVDA"],
        "screen_candidates": ["NVDA", "AVGO", "AMD", "MSFT", "AMZN", "GOOGL", "LLY", "COST", "TSLA", "META"],
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

if __name__ == "__main__":
    stream_info = fetch_latest_wepoll_stream()
    print("\nParsed Stream & Sector Leaders:")
    print(json.dumps(stream_info, ensure_ascii=False, indent=2))
