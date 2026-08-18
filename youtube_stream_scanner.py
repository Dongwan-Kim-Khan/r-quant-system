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

# Stock Name to Ticker Dictionary (US + KR Key Stocks)
STOCK_MAP = {
    "아마존": "AMZN", "AMAZON": "AMZN", "AMZN": "AMZN",
    "일라이릴리": "LLY", "릴리": "LLY", "LLY": "LLY",
    "엔비디아": "NVDA", "NVIDIA": "NVDA", "NVDA": "NVDA",
    "테슬라": "TSLA", "TESLA": "TSLA", "TSLA": "TSLA",
    "애플": "AAPL", "APPLE": "AAPL", "AAPL": "AAPL",
    "마이크로소프트": "MSFT", "마이크로 소프트": "MSFT", "MSFT": "MSFT",
    "메타": "META", "META": "META", "페이스북": "META",
    "구글": "GOOGL", "알파벳": "GOOGL", "GOOGL": "GOOGL", "GOOG": "GOOGL",
    "브로드컴": "AVGO", "BROADCOM": "AVGO", "AVGO": "AVGO",
    "코스트코": "COST", "COSTCO": "COST", "COST": "COST",
    "AMD": "AMD", "에이엠디": "AMD",
    "퀄컴": "QCOM", "QUALCOMM": "QCOM", "QCOM": "QCOM",
    "팔란티어": "PLTR", "PLTR": "PLTR",
    "슈퍼마이크로": "SMCI", "SMCI": "SMCI",
    "넷플릭스": "NFLX", "NETFLIX": "NFLX", "NFLX": "NFLX",
    "ASML": "ASML",
    "코인베이스": "COIN", "COIN": "COIN",
    "ARM": "ARM", "암": "ARM",
    "마이크론": "MU", "MU": "MU",
    "크라우드스트라이크": "CRWD", "CRWD": "CRWD",
    "팔로알토": "PANW", "PANW": "PANW",
    "우버": "UBER", "UBER": "UBER",
    "삼성전자": "005930.KS", "삼전": "005930.KS",
    "SK하이닉스": "000660.KS", "하이닉스": "000660.KS",
    "한화에어로스페이스": "012450.KS", "한화에어로": "012450.KS"
}

def fetch_latest_wepoll_stream():
    """
    Fetches the latest live stream recording metadata from @wepoll_original/streams.
    """
    print(f"🎬 [유튜브 파서] {CHANNEL_URL} 최신 라이브 스트리밍 정보 조회 중...")
    
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
            print(f"⚠️ yt-dlp 조회 경고: {res.stderr[:200]}")
            return load_fallback_cache()
            
        data = json.loads(res.stdout)
        entries = data.get("entries", [])
        if not entries:
            return load_fallback_cache()
            
        latest = entries[0]
        v_id = latest.get("id")
        title = latest.get("title", "")
        url = f"https://www.youtube.com/watch?v={v_id}"
        
        # Detailed metadata (tags & description)
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
                
        # Extract candidate tickers from title, tags, and description
        extracted_tickers = extract_tickers_from_text(title + " " + " ".join(tags) + " " + description)
        
        # Detect market cycle / macro keywords
        cycles = extract_cycle_keywords(title + " " + description)
        
        payload = {
            "video_id": v_id,
            "title": title,
            "url": url,
            "tags": tags,
            "description_snippet": description[:300],
            "extracted_tickers": extracted_tickers,
            "cycle_keywords": cycles,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
        print(f"✅ [유튜브 파서 완료] 최신 라이브: '{title}' ({url})")
        print(f"   • 포착된 사이클/키워드: {', '.join(cycles) if cycles else '미장 시황/나스닥'}")
        print(f"   • 추출된 관련 티커: {', '.join(extracted_tickers) if extracted_tickers else '기본 유니버스 활용'}")
        return payload
        
    except Exception as e:
        print(f"⚠️ 유튜브 스트림 조회 실패 ({e}), 로컬 캐시를 사용합니다.")
        return load_fallback_cache()

def extract_tickers_from_text(text):
    found = set()
    text_upper = text.upper()
    
    # 1. Match Korean & English names in STOCK_MAP
    for name, ticker in STOCK_MAP.items():
        if name.upper() in text_upper:
            found.add(ticker)
            
    # 2. Match standard ticker patterns (e.g. $NVDA, AAPL, AMZN, QQQ)
    matches = re.findall(r'\b[A-Z]{2,5}\b', text_upper)
    for m in matches:
        if m in STOCK_MAP.values():
            found.add(m)
            
    return list(found)

def extract_cycle_keywords(text):
    keywords = ["금리", "유가", "인플레", "반도체", "AI", "빅테크", "로봇", "바이오", "원전", "방산", "소비재", "실적발표", "가을국면", "빈집선점", "양적긴축"]
    detected = [kw for kw in keywords if kw in text]
    return detected

def load_fallback_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "video_id": "latest_live",
        "title": "임계점 넘어가는 금리와 유가ㅣ성장 vs 금리부담의 싸움 (바이킹스 라이브)",
        "url": "https://www.youtube.com/@wepoll_original/streams",
        "extracted_tickers": ["AMZN", "LLY", "NVDA", "TSLA", "META", "AVGO", "COST"],
        "cycle_keywords": ["금리", "유가", "반도체", "빅테크", "빈집선점"],
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

if __name__ == "__main__":
    stream_info = fetch_latest_wepoll_stream()
    print("\n최신 스트림 파싱 결과:")
    print(json.dumps(stream_info, ensure_ascii=False, indent=2))
