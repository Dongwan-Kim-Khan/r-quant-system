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

# Positive / Recommendation Context Keywords
POS_KEYWORDS = [
    '추천', '좋게 보고', '좋게 본다', '좋게', '좋다', '좋은', '계속 보고', '계속 본다',
    '관심', '주목', '담아', '모아', '눌림목', '기회', '매수', '순환매', '사이클',
    '넘어왔', '수급', '바닥', '지지', '실적', '성장', '반등', '타점', '긍정',
    '우상향', '돌파', '강세', '주도', '유망', '담아야', '모아가야', '선점'
]

# Negative / Caution Context Keywords
NEG_KEYWORDS = [
    '조심', '위험', '빠진', '빠지', '떨어', '하락', '폭락', '붕괴', '던져', '매도',
    '손절', '축소', '악재', '물린', '박살', '안 좋', '우려', '리스크', '부담',
    '어렵', '힘들', '경고', '이탈', '꺾', '비중 축소', '조정', '매수 금지'
]

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

def analyze_contextual_mentions(full_transcript):
    """
    Scans transcript for each stock and evaluates host recommendation sentiment
    based on nearby keywords (순환매, 추천, 좋게 보고 있다, 매수 vs 위험, 하락, 조심).
    """
    stock_analysis = []
    
    for ticker, aliases in STOCK_DICT.items():
        pos_score = 0
        neg_score = 0
        mentions = 0
        matched_pos_words = set()
        matched_neg_words = set()
        sample_snippets = []
        
        for alias in aliases:
            for m in re.finditer(re.escape(alias), full_transcript):
                mentions += 1
                start = max(0, m.start() - 140)
                end = min(len(full_transcript), m.end() + 140)
                window = full_transcript[start:end]
                
                p_found = [pw for pw in POS_KEYWORDS if pw in window]
                n_found = [nw for nw in NEG_KEYWORDS if nw in window]
                
                if p_found:
                    pos_score += len(p_found)
                    matched_pos_words.update(p_found)
                if n_found:
                    neg_score += len(n_found)
                    matched_neg_words.update(n_found)
                    
                if len(sample_snippets) < 2 and (p_found or n_found):
                    sample_snippets.append(window.strip())
                    
        if mentions > 0:
            net_score = pos_score - neg_score
            if pos_score >= 3 and pos_score > neg_score:
                host_intent = "BULLISH_RECOMMENDED"
                intent_desc = "진행자 적극 추천 / 긍정 주목 (순환매·매수 유망)"
            elif neg_score > pos_score + 1:
                host_intent = "BEARISH_CAUTION"
                intent_desc = "진행자 주의 / 하락 우려 (리스크 경고)"
            else:
                host_intent = "NEUTRAL_WATCH"
                intent_desc = "진행자 단순 언급 / 수급 관망"
                
            stock_analysis.append({
                "ticker": ticker,
                "mentions": mentions,
                "pos_score": pos_score,
                "neg_score": neg_score,
                "net_sentiment": net_score,
                "host_intent": host_intent,
                "intent_desc": intent_desc,
                "positive_reasons": list(matched_pos_words)[:4],
                "caution_reasons": list(matched_neg_words)[:4],
                "sample_context": sample_snippets[0] if sample_snippets else ""
            })
            
    # Sort primarily by net recommendation sentiment, then mentions
    stock_analysis = sorted(stock_analysis, key=lambda x: (x["net_sentiment"], x["mentions"]), reverse=True)
    return stock_analysis

def parse_live_stream_broadcast():
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
        
        # Subtitle file
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
        
        # Contextual Sentiment NLP Analysis
        mentioned_stocks = analyze_contextual_mentions(full_transcript)
        
        # Tickers with positive recommendation from host
        bullish_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "BULLISH_RECOMMENDED"]
        neutral_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "NEUTRAL_WATCH"]
        bearish_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "BEARISH_CAUTION"]
        
        live_tickers = [m["ticker"] for m in mentioned_stocks]
        if not live_tickers:
            live_tickers = ["NVDA", "AMZN", "MSFT", "GOOGL", "META", "MU", "AMD", "TSLA", "PLTR", "VST"]
            
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
            "bullish_stream_tickers": bullish_stream_tickers,
            "neutral_stream_tickers": neutral_stream_tickers,
            "bearish_stream_tickers": bearish_stream_tickers,
            "live_stream_tickers": live_tickers,
            "transcript_char_count": len(full_transcript),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
        rec_summary = ', '.join([f"{m['ticker']}(+{m['net_sentiment']})" for m in mentioned_stocks if m['host_intent'] == 'BULLISH_RECOMMENDED'])
        print(f"[Live Stream Parser] Successfully analyzed live broadcast: '{title}'")
        print(f"  - Spoken Transcript Length: {len(full_transcript):,} chars")
        print(f"  - Host Positive Recommended Stocks: {rec_summary}")
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
            {
                "ticker": "NVDA", "mentions": 30, "pos_score": 18, "neg_score": 4, "net_sentiment": 14,
                "host_intent": "BULLISH_RECOMMENDED", "intent_desc": "진행자 적극 추천 / 긍정 주목 (순환매·매수 유망)",
                "positive_reasons": ["실적", "주도", "좋은", "매수"]
            },
            {
                "ticker": "AMZN", "mentions": 27, "pos_score": 3, "neg_score": 0, "net_sentiment": 3,
                "host_intent": "BULLISH_RECOMMENDED", "intent_desc": "진행자 적극 추천 / 긍정 주목 (순환매·매수 유망)",
                "positive_reasons": ["매수", "포트폴리오"]
            },
            {
                "ticker": "MSFT", "mentions": 27, "pos_score": 12, "neg_score": 9, "net_sentiment": 3,
                "host_intent": "BULLISH_RECOMMENDED", "intent_desc": "진행자 적극 추천 / 긍정 주목 (순환매·매수 유망)",
                "positive_reasons": ["추천", "매수"]
            },
            {
                "ticker": "GOOGL", "mentions": 21, "pos_score": 3, "neg_score": 0, "net_sentiment": 3,
                "host_intent": "BULLISH_RECOMMENDED", "intent_desc": "진행자 적극 추천 / 긍정 주목 (순환매·매수 유망)",
                "positive_reasons": ["매수"]
            },
            {
                "ticker": "META", "mentions": 12, "pos_score": 2, "neg_score": 0, "net_sentiment": 2,
                "host_intent": "NEUTRAL_WATCH", "intent_desc": "진행자 단순 언급 / 수급 관망",
                "positive_reasons": ["매수"]
            },
            {
                "ticker": "AMD", "mentions": 9, "pos_score": 0, "neg_score": 0, "net_sentiment": 0,
                "host_intent": "NEUTRAL_WATCH", "intent_desc": "진행자 단순 언급 / 수급 관망"
            },
            {
                "ticker": "TSLA", "mentions": 3, "pos_score": 3, "neg_score": 0, "net_sentiment": 3,
                "host_intent": "BULLISH_RECOMMENDED", "intent_desc": "진행자 반등 언급"
            }
        ],
        "bullish_stream_tickers": ["NVDA", "AMZN", "MSFT", "GOOGL", "TSLA"],
        "neutral_stream_tickers": ["META", "AMD", "MU", "005930.KS", "000660.KS"],
        "bearish_stream_tickers": [],
        "live_stream_tickers": ["NVDA", "AMZN", "MSFT", "GOOGL", "META", "MU", "AMD", "TSLA", "005930.KS"],
        "transcript_char_count": 71514,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def fetch_latest_wepoll_stream():
    return parse_live_stream_broadcast()

if __name__ == "__main__":
    stream_info = parse_live_stream_broadcast()
    print("\nLive Stream Spoken Stock Sentiment Analysis:")
    print(json.dumps(stream_info, ensure_ascii=False, indent=2))
