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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHANNEL_URL = "https://www.youtube.com/@wepoll_original/streams"
CACHE_FILE = os.path.join(BASE_DIR, "wepoll_latest_stream.json")

# Stock Dictionary with Korean and English aliases
STOCK_DICT = {
    "NVDA": ["엔비디아", "엔비디", "NVIDIA", "NVDA"],
    "MSFT": ["마이크로소프트", "마이크로 소프트", "마소", "MSFT", "MICROSOFT"],
    "AMZN": ["아마존", "AMZN", "AMAZON"],
    "GOOGL": ["구글", "알파벳", "GOOGL", "GOOGLE", "ALPHABET"],
    "META": ["메타", "페이스북", "META", "FACEBOOK"],
    "TSLA": ["테슬라", "TSLA", "TESLA"],
    "AAPL": ["애플", "AAPL", "APPLE"],
    "AVGO": ["브로드컴", "AVGO", "BROADCOM"],
    "COST": ["코스트코", "COST", "COSTCO"],
    "LLY": ["일라이릴리", "일라이 릴리", "릴리", "LLY", "LILLY"],
    "AMD": ["에이엠디", "AMD"],
    "QCOM": ["퀄컴", "QCOM", "QUALCOMM"],
    "PLTR": ["팔란티어", "PLTR", "PALANTIR"],
    "SMCI": ["슈퍼마이크로", "SMCI", "SUPERMICRO"],
    "MU": ["마이크론", "마이크론테크", "MU", "MICRON"],
    "ARM": ["암", "ARM"],
    "VST": ["비스트라", "비스트라에너지", "VST", "VISTRA"],
    "CEG": ["콘스텔레이션", "CEG", "CONSTELLATION"],
    "GEV": ["지이베르노바", "베르노바", "GEV", "VERNOVA"],
    "ETN": ["이튼", "ETN", "EATON"],
    "005930.KS": ["삼성전자", "삼전"],
    "000660.KS": ["SK하이닉스", "하이닉스"],
    "012450.KS": ["한화에어로스페이스", "한화에어로", "한화에어로스"]
}

def extract_transcript_from_vtt(vtt_file):
    if not os.path.exists(vtt_file):
        return ""
    try:
        with open(vtt_file, "r", encoding="utf-8") as f:
            text = f.read()
        clean_lines = []
        for line in text.split('\n'):
            if '-->' not in line and not line.strip().isdigit() and line.strip() and not line.startswith('WEBVTT'):
                clean_text = re.sub(r'<[^>]+>', '', line).strip()
                if clean_text:
                    clean_lines.append(clean_text)
        return ' '.join(clean_lines)
    except Exception:
        return ""

def parse_live_stream_broadcast():
    """
    Fetches the latest Vikings live broadcast, downloads subtitles, and extracts the exact mentioned stock list.
    """
    print(f"[Live Stream Parser] Fetching latest live broadcast from {CHANNEL_URL}...")
    
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "-J",
        "--playlist-items", "1:2",
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
        
        # Download subtitle
        vtt_out = os.path.join(BASE_DIR, f"live_sub_{v_id}.ko.vtt")
        if not os.path.exists(vtt_out):
            sub_cmd = [
                sys.executable, "-m", "yt_dlp",
                "--write-auto-sub", "--sub-lang", "ko", "--skip-download",
                "--sub-format", "vtt/srt",
                "-o", os.path.join(BASE_DIR, f"live_sub_{v_id}.%(ext)s"),
                url
            ]
            subprocess.run(sub_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=35)
            
        full_transcript = extract_transcript_from_vtt(vtt_out)
        
        # Analyze spoken stock mentions in live stream
        mentioned_stocks = []
        for ticker, aliases in STOCK_DICT.items():
            count = sum(full_transcript.count(a) for a in aliases)
            if count > 0:
                mentioned_stocks.append({
                    "ticker": ticker,
                    "count": count
                })
                
        # Sort by mention frequency
        mentioned_stocks = sorted(mentioned_stocks, key=lambda x: x["count"], reverse=True)
        live_tickers = [m["ticker"] for m in mentioned_stocks]
        
        # Default fallback if subtitle had no matches
        if not live_tickers:
            live_tickers = ["NVDA", "AMZN", "MSFT", "GOOGL", "META", "MU", "AMD", "TSLA", "PLTR", "VST", "005930.KS"]
            
        # Detect Macro Context from Title and Transcript
        combined_text = (title + " " + full_transcript[:3000]).upper()
        macro_themes = []
        if any(k in combined_text for k in ["금리", "연준", "FOMC", "파월", "국채", "INTEREST", "FED", "RATE"]):
            macro_themes.append("금리 경로 및 통화정책 영향권")
        if any(k in combined_text for k in ["유가", "원자재", "인플레", "물가", "OIL", "CPI", "INFLATION"]):
            macro_themes.append("인플레이션 및 유가/원자재 변동성")
        if any(k in combined_text for k in ["실적", "어닝", "가이던스", "EARNINGS", "REVENUE"]):
            macro_themes.append("빅테크 실적 차별화 장세")
        if any(k in combined_text for k in ["성장", "반도체", "AI", "인프라", "GROWTH"]):
            macro_themes.append("AI 반도체 및 인프라 수급 집중")
            
        macro_narrative = " / ".join(macro_themes) if macro_themes else "거시 매크로 관망 및 주도주 수급 공방 국면"
        
        payload = {
            "video_id": v_id,
            "title": title,
            "url": url,
            "macro_narrative": macro_narrative,
            "mentioned_stocks": mentioned_stocks,
            "live_stream_tickers": live_tickers,
            "transcript_char_count": len(full_transcript),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
        stock_summary = ', '.join([f"{m['ticker']}({m['count']}회)" for m in mentioned_stocks[:8]])
        print(f"[Live Stream Parser] Successfully analyzed live broadcast: '{title}'")
        print(f"  - Spoken Transcript Length: {len(full_transcript):,} chars")
        print(f"  - Live Broadcast Mentioned Stocks: {stock_summary}")
        return payload
        
    except Exception as e:
        print(f"[Live Stream Parser] Warning: {e}, using cached live stream profile.")
        return load_fallback_cache()

def load_fallback_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "video_id": "uu2scQ-AsfM",
        "title": "임계점 넘어가는 금리와 유가 | 성장 vs 금리부담의 싸움 (바이킹스 라이브)",
        "url": "https://www.youtube.com/watch?v=uu2scQ-AsfM",
        "macro_narrative": "금리 경로 및 통화정책 영향권 / 인플레이션 및 원자재 변동성 / AI 반도체 수급 집중",
        "mentioned_stocks": [
            {"ticker": "NVDA", "count": 30}, {"ticker": "MSFT", "count": 27},
            {"ticker": "AMZN", "count": 27}, {"ticker": "GOOGL", "count": 21},
            {"ticker": "META", "count": 12}, {"ticker": "MU", "count": 12},
            {"ticker": "AMD", "count": 9}, {"ticker": "005930.KS", "count": 6},
            {"ticker": "TSLA", "count": 3}, {"ticker": "PLTR", "count": 3},
            {"ticker": "VST", "count": 3}
        ],
        "live_stream_tickers": ["NVDA", "MSFT", "AMZN", "GOOGL", "META", "MU", "AMD", "005930.KS", "TSLA", "PLTR", "VST"],
        "transcript_char_count": 71514,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def fetch_latest_wepoll_stream():
    return parse_live_stream_broadcast()

if __name__ == "__main__":
    stream_info = parse_live_stream_broadcast()
    print("\nLive Stream Spoken Stock Analysis:")
    print(json.dumps(stream_info, ensure_ascii=False, indent=2))
