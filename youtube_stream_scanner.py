import os
import sys
import json
import subprocess
import re
from datetime import datetime
import yfinance as yf
import pandas as pd

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

# Macro Defense / Stance Keywords
MACRO_DEFENSE_KEYWORDS = [
    '이번 주는', '이번주는', '이번 주', '사지 말자', '사지말자', '사지 마', '관망하자',
    '관망', '쉬어가자', '쉬어가', '현금 확보', '현금 비중', '차익실현', '차익 실현',
    '소나기', '피하자', '전쟁', '지정학', '유가 급등', '금리 부담', '금리부담',
    '리스크 관리', '몸 사리자', '신규 매수 자제'
]

def fetch_realtime_macro_gauges():
    """
    Fetches 5 institutional financial macro gauges:
    1. US 10-Year Treasury Yield (^TNX) - Stock valuation & equity risk premium
    2. Dollar Index (DX-Y.NYB) - Global tech liquidity & FX pressure
    3. VIX Volatility Index (^VIX) - Market fear & options hedging
    4. WTI Crude Oil (CL=F) - Headline inflation & cost shocks
    5. Gold Futures (GC=F) & US Short-term Treasury (^IRX) - Safe-haven & policy rate
    """
    gauges = {
        "us10y": {"val": 4.69, "delta": "+0.04%p", "status": "HIGH_BURDEN", "label": "미국채 10년물"},
        "dxy": {"val": 99.5, "delta": "+0.1%", "status": "NEUTRAL", "label": "달러 인덱스(DXY)"},
        "vix": {"val": 15.8, "delta": "+0.2%", "status": "NORMAL", "label": "VIX 공포지수"},
        "wti": {"val": 86.2, "delta": "-0.5%", "status": "ELEVATED", "label": "WTI 국제유가"},
        "gold": {"val": 4620.0, "delta": "+0.3%", "status": "STABLE", "label": "국제 금시세"}
    }
    
    try:
        tickers = ["^TNX", "DX-Y.NYB", "^VIX", "CL=F", "GC=F"]
        df = yf.download(tickers, period="5d", interval="1d", progress=False)
        if not df.empty and 'Close' in df:
            close_df = df['Close']
            
            # 1. US 10-Year Yield (^TNX)
            if '^TNX' in close_df:
                tnx_series = close_df['^TNX'].dropna()
                if len(tnx_series) >= 2:
                    cur_tnx = float(tnx_series.iloc[-1])
                    prev_tnx = float(tnx_series.iloc[-2])
                    delta = cur_tnx - prev_tnx
                    status = "CRITICAL_BURDEN" if cur_tnx >= 4.50 else ("HIGH_BURDEN" if cur_tnx >= 4.30 else ("BURDEN" if cur_tnx >= 4.10 else "OPTIMAL"))
                    gauges["us10y"] = {
                        "val": round(cur_tnx, 3),
                        "delta": f"{delta:+.2f}%p",
                        "status": status,
                        "label": "미국채 10년물"
                    }
                    
            # 2. Dollar Index (DX-Y.NYB)
            if 'DX-Y.NYB' in close_df:
                dxy_series = close_df['DX-Y.NYB'].dropna()
                if len(dxy_series) >= 2:
                    cur_dxy = float(dxy_series.iloc[-1])
                    prev_dxy = float(dxy_series.iloc[-2])
                    delta = ((cur_dxy - prev_dxy) / prev_dxy) * 100
                    status = "STRONG_DOLLAR" if cur_dxy >= 105.0 else ("ELEVATED" if cur_dxy >= 103.0 else ("NEUTRAL" if cur_dxy >= 100.0 else "SOFT_DOLLAR"))
                    gauges["dxy"] = {
                        "val": round(cur_dxy, 2),
                        "delta": f"{delta:+.1f}%",
                        "status": status,
                        "label": "달러 인덱스(DXY)"
                    }
                    
            # 3. VIX
            if '^VIX' in close_df:
                vix_series = close_df['^VIX'].dropna()
                if len(vix_series) >= 2:
                    cur_vix = float(vix_series.iloc[-1])
                    prev_vix = float(vix_series.iloc[-2])
                    delta = ((cur_vix - prev_vix) / prev_vix) * 100
                    status = "PANIC" if cur_vix >= 25.0 else ("CAUTION" if cur_vix >= 20.0 else ("NORMAL" if cur_vix >= 16.0 else "CALM"))
                    gauges["vix"] = {
                        "val": round(cur_vix, 2),
                        "delta": f"{delta:+.1f}%",
                        "status": status,
                        "label": "VIX 공포지수"
                    }
                    
            # 4. WTI Oil (CL=F)
            if 'CL=F' in close_df:
                wti_series = close_df['CL=F'].dropna()
                if len(wti_series) >= 2:
                    cur_wti = float(wti_series.iloc[-1])
                    prev_wti = float(wti_series.iloc[-2])
                    delta = ((cur_wti - prev_wti) / prev_wti) * 100
                    status = "INFLATION_SHOCK" if cur_wti >= 85.0 else ("ELEVATED" if cur_wti >= 80.0 else "STABLE")
                    gauges["wti"] = {
                        "val": round(cur_wti, 2),
                        "delta": f"{delta:+.1f}%",
                        "status": status,
                        "label": "WTI 국제유가"
                    }
                    
            # 5. Gold (GC=F)
            if 'GC=F' in close_df:
                gold_series = close_df['GC=F'].dropna()
                if len(gold_series) >= 2:
                    cur_gold = float(gold_series.iloc[-1])
                    prev_gold = float(gold_series.iloc[-2])
                    delta = ((cur_gold - prev_gold) / prev_gold) * 100
                    status = "HEDGE_DEMAND" if delta >= 1.5 else "STABLE"
                    gauges["gold"] = {
                        "val": round(cur_gold, 1),
                        "delta": f"{delta:+.1f}%",
                        "status": status,
                        "label": "국제 금시세"
                    }
    except Exception as e:
        print(f"[Macro Gauge Fetch Warning] {e}")
        
    return gauges

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

def analyze_macro_regime_and_climate(title, full_transcript, gauges):
    """
    Evaluates Gate-0 Macro Climate & Computes Macro Stance Index 2.0 (MSI: 0~100점).
    MSI 2.0 Formula:
      MSI = M_hard (60) + M_nlp (25) + M_shock (15)
    """
    combined_text = (title + " " + full_transcript).upper()
    
    # 1. Financial Macro Hard Gauges (M_hard: max 60 pts)
    us10y_val = gauges.get("us10y", {}).get("val", 4.4)
    vix_val = gauges.get("vix", {}).get("val", 16.0)
    wti_val = gauges.get("wti", {}).get("val", 80.0)
    dxy_val = gauges.get("dxy", {}).get("val", 100.0)
    
    # US 10Y Yield (Max 25 pts) - Crucial equity valuation anchor
    if us10y_val >= 4.50: us10y_pts = 25.0
    elif us10y_val >= 4.30: us10y_pts = 18.0
    elif us10y_val >= 4.10: us10y_pts = 10.0
    elif us10y_val >= 3.90: us10y_pts = 4.0
    else: us10y_pts = 0.0
    
    # VIX (Max 15 pts) - Hedging and panic index
    if vix_val >= 25.0: vix_pts = 15.0
    elif vix_val >= 20.0: vix_pts = 10.0
    elif vix_val >= 16.0: vix_pts = 5.0
    else: vix_pts = 0.0
    
    # WTI Oil (Max 10 pts) - Inflation pressure
    if wti_val >= 85.0: wti_pts = 10.0
    elif wti_val >= 80.0: wti_pts = 6.0
    elif wti_val >= 75.0: wti_pts = 3.0
    else: wti_pts = 0.0
    
    # Dollar Index (Max 10 pts) - Tech liquidity drain
    if dxy_val >= 105.0: dxy_pts = 10.0
    elif dxy_val >= 103.0: dxy_pts = 6.0
    elif dxy_val >= 100.0: dxy_pts = 2.0
    else: dxy_pts = 0.0
    
    m_hard = round(us10y_pts + vix_pts + wti_pts + dxy_pts, 1)
    
    # 2. Host Spoken NLP Directive Sentiment (M_nlp: max 25 pts)
    def_count = sum(full_transcript.count(kw) for kw in MACRO_DEFENSE_KEYWORDS)
    buy_count = sum(full_transcript.count(kw) for kw in ['눌림목', '매수 기회', '담아야', '모아가야', '분할 매수', '순환매 진입', '우상향'])
    
    if len(full_transcript) > 500:
        m_nlp = round(25.0 * (def_count / (def_count + buy_count + 0.1)), 1)
        m_nlp = min(25.0, max(0.0, m_nlp))
    else:
        # Fallback when subtitles are delayed/unavailable in cloud runner
        title_defense = any(k in title for k in ['쫄아있는', '금리', '하락', '위기', '붕괴', '경고', '리스크', '조심', '전쟁', '부채'])
        m_nlp = 16.0 if title_defense else 8.0
    
    # 3. Geopolitical & External Shock Factor (M_shock: max 15 pts)
    external_shocks = []
    m_shock = 0.0
    if any(k in combined_text for k in ["전쟁", "지정학", "중동", "우크라", "이란", "대만", "WAR", "CONFLICT"]):
        external_shocks.append("지정학적 분쟁 및 전쟁 리스크")
        m_shock += 6.0
    if any(k in combined_text for k in ["관세", "무역", "트럼프", "보복", "TARIFF", "TRADE"]):
        external_shocks.append("무역 분쟁 및 관세 불확실성")
        m_shock += 4.0
    if any(k in combined_text for k in ["금리", "연준", "FOMC", "파월", "국채", "INTEREST", "FED", "RATE"]):
        external_shocks.append("금리 경로 및 통화정책 영향권")
        m_shock += 5.0
    m_shock = min(15.0, m_shock)
    
    # Total MSI Calculation (0 ~ 100)
    msi_score = round(m_hard + m_nlp + m_shock, 1)
    msi_score = min(100.0, max(0.0, msi_score))
    
    if msi_score >= 75.0:
        macro_stance = "CASH_EXIT"
        macro_stance_kr = "현금화 / 숏 헤지 주간 (Red 75~100점)"
        macro_headline = f"[거시 게이트 0단계: 위험 경보 (MSI {msi_score}점) / 신규 매수 전면 중단 및 현금 확보]"
        macro_action_directive = (
            f"거시 위험 지수(MSI {msi_score}점)가 위험 경보 구간에 진입했습니다. "
            f"신규 매수를 전면 중단하고 보유 종목 차익/손절 관리에 집중하십시오."
        )
    elif msi_score >= 50.0:
        macro_stance = "DEFENSE_HOLD"
        macro_stance_kr = "신규 매수 보류 / 관망·현금 유지 주간 (Orange 50~74점)"
        macro_headline = f"[거시 게이트 0단계: 거시 위험 지수 {msi_score}점 / 신규 매수 보류 및 관망 권고]"
        macro_action_directive = (
            f"거시 위험 지수(MSI {msi_score}점: 10년물 금리 {us10y_val}%, 유가 ${wti_val}) 및 방송 지침상, "
            f"현재 장세는 신규 매수를 쉬어가고 관망해야 하는 장세입니다. "
            f"다만 거시 리스크 진정 시 즉시 공략할 최우선 1순위 후보 종목을 사전 선별합니다."
        )
    elif msi_score >= 30.0:
        macro_stance = "SELECTIVE_BUY"
        macro_stance_kr = "선별적 눌림목 분할 매수 주간 (Yellow 30~49점)"
        macro_headline = f"[거시 게이트 0단계: 거시 중립 (MSI {msi_score}점) / 선별적 눌림목 매수 유효]"
        macro_action_directive = (
            f"거시 위험 지수(MSI {msi_score}점)가 중립 범위에 위치합니다. "
            f"26일 기준선 지지가 확인된 주도 종목에 한하여 소액 분할 매수가 유효합니다."
        )
    else:
        macro_stance = "ACTIVE_BUY"
        macro_stance_kr = "적극 분할 매수 주간 (Green 0~29점)"
        macro_headline = f"[거시 게이트 0단계: 최적 매수 기후 (MSI {msi_score}점) / 적극 분할 매수 가능]"
        macro_action_directive = (
            f"거시 지표가 매우 우호적입니다. 1차 추천 종목에 대한 적극적인 분할 매수를 권고합니다."
        )
        
    return {
        "msi_score": msi_score,
        "msi_breakdown": {
            "m_hard": m_hard,
            "m_hard_max": 60,
            "us10y_pts": us10y_pts,
            "dxy_pts": dxy_pts,
            "vix_pts": vix_pts,
            "wti_pts": wti_pts,
            "m_nlp": m_nlp,
            "m_nlp_max": 25,
            "def_count": def_count,
            "buy_count": buy_count,
            "m_shock": m_shock,
            "m_shock_max": 15
        },
        "macro_stance": macro_stance,
        "macro_stance_kr": macro_stance_kr,
        "macro_headline": macro_headline,
        "macro_action_directive": macro_action_directive,
        "external_shocks": external_shocks if external_shocks else ["금리 경로 및 통화정책 영향권", "지정학적 리스크"],
        "defense_keyword_count": def_count
    }

def analyze_contextual_mentions(full_transcript):
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
            
    stock_analysis = sorted(stock_analysis, key=lambda x: (x["net_sentiment"], x["mentions"]), reverse=True)
    return stock_analysis

def parse_live_stream_broadcast():
    print(f"[Live Stream Parser] Fetching latest live broadcast from {CHANNEL_URL}...")
    
    # 1. Fetch Realtime Macro Gauges (VIX, 10Y Yield, WTI Oil)
    gauges = fetch_realtime_macro_gauges()
    
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "-J",
        "--playlist-items", "1:6",
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
            
        target_entry = None
        full_transcript = ""
        
        # Iterate over recent streams to find the primary Vikings broadcast with valid subtitles
        for entry in entries:
            v_id = entry.get("id")
            title = entry.get("title", "")
            if not v_id:
                continue
                
            vtt_out = os.path.join(BASE_DIR, f"live_sub_{v_id}.ko.vtt")
            if not os.path.exists(vtt_out):
                sub_cmd = [
                    sys.executable, "-m", "yt_dlp",
                    "--write-auto-sub", "--sub-lang", "ko", "--skip-download",
                    "--sub-format", "vtt/srt",
                    "-o", os.path.join(BASE_DIR, f"live_sub_{v_id}.%(ext)s"),
                    f"https://www.youtube.com/watch?v={v_id}"
                ]
                subprocess.run(sub_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=35)
                
            transcript = extract_transcript_from_vtt(vtt_out)
            
            # Prioritize Vikings main market broadcast with rich subtitle transcripts
            if ("바이킹스" in title or "VIKINGS" in title.upper() or len(transcript) > 5000) and len(transcript) > 0:
                target_entry = entry
                full_transcript = transcript
                break
                
        if not target_entry:
            target_entry = entries[0]
            v_id = target_entry.get("id")
            full_transcript = extract_transcript_from_vtt(os.path.join(BASE_DIR, f"live_sub_{v_id}.ko.vtt"))
            
        v_id = target_entry.get("id")
        title = target_entry.get("title", "")
        url = f"https://www.youtube.com/watch?v={v_id}"
        
        # 2. Contextual Sentiment NLP Analysis
        mentioned_stocks = analyze_contextual_mentions(full_transcript)
        
        # 3. Gate-0 Macro Climate & Stance Analysis
        macro_climate = analyze_macro_regime_and_climate(title, full_transcript, gauges)
        
        bullish_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "BULLISH_RECOMMENDED"]
        neutral_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "NEUTRAL_WATCH"]
        bearish_stream_tickers = [m["ticker"] for m in mentioned_stocks if m["host_intent"] == "BEARISH_CAUTION"]
        
        live_tickers = [m["ticker"] for m in mentioned_stocks]
        if not live_tickers:
            live_tickers = ["NVDA", "AMZN", "MSFT", "GOOGL", "META", "MU", "AMD", "TSLA", "PLTR", "VST"]
            
        payload = {
            "video_id": v_id,
            "title": title,
            "url": url,
            "macro_gauges": gauges,
            "macro_climate": macro_climate,
            "macro_narrative": " / ".join(macro_climate["external_shocks"]),
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
        print(f"[Live Stream Parser] Analyzed '{title}'")
        print(f"  - Gate-0 Macro Stance: {macro_climate['macro_headline']}")
        print(f"  - Macro Gauges: VIX {gauges['vix']['val']}, 10Y {gauges['us10y']['val']}%, WTI ${gauges['wti']['val']}")
        print(f"  - Host Recommended Stocks: {rec_summary}")
        return payload
        
    except Exception as e:
        print(f"[Live Stream Parser] Warning: {e}, using cached profile.")
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
        "macro_gauges": {
            "vix": {"val": 15.8, "delta": "+0.4%", "status": "NORMAL", "label": "VIX 공포지수"},
            "us10y": {"val": 4.42, "delta": "+1.2bp", "status": "BURDEN", "label": "미국채 10년물"},
            "wti": {"val": 78.5, "delta": "-0.5%", "status": "STABLE", "label": "WTI 국제유가"}
        },
        "macro_climate": {
            "macro_stance": "DEFENSE_HOLD",
            "macro_stance_kr": "신규 매수 보류 / 관망·현금 확보 주간",
            "macro_headline": "[거시 게이트 0단계: 이번 주 신규 매수 보류 / 관망·현금 유지 권고]",
            "macro_action_directive": "거시 지표(10년물 금리 4.42%, VIX 15.8, 유가 $78.5) 및 방송 지침상, 적어도 이번 주는 무리한 신규 매수를 쉬어가고 현금을 지키는 관망 주간입니다.",
            "external_shocks": ["금리 경로 및 통화정책 영향권", "인플레이션 및 원자재 변동성", "지정학적 리스크"],
            "defense_keyword_count": 8
        },
        "macro_narrative": "금리 경로 및 통화정책 영향권 / 인플레이션 및 원자재 변동성 / 지정학적 리스크",
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
            }
        ],
        "bullish_stream_tickers": ["NVDA", "AMZN"],
        "neutral_stream_tickers": ["META", "AMD", "GOOGL", "005930.KS"],
        "bearish_stream_tickers": ["000660.KS", "QCOM"],
        "live_stream_tickers": ["NVDA", "AMZN", "META", "AMD", "GOOGL"],
        "transcript_char_count": 71514,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def fetch_latest_wepoll_stream():
    return parse_live_stream_broadcast()

if __name__ == "__main__":
    stream_info = parse_live_stream_broadcast()
    print("\nGate-0 Macro Climate & Live Stream Sentiment:")
    print(json.dumps(stream_info.get("macro_climate"), ensure_ascii=False, indent=2))
    print(json.dumps(stream_info.get("macro_gauges"), ensure_ascii=False, indent=2))
