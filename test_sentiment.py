import sys
import re
import os
import json

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

vtt_file = os.path.join(os.path.dirname(__file__), 'test_sub_uu2scQ-AsfM.ko.vtt')
with open(vtt_file, 'r', encoding='utf-8') as f:
    text = f.read()

clean_lines = [re.sub(r'<[^>]+>', '', l).strip() for l in text.split('\n') if '-->' not in l and not l.strip().isdigit() and l.strip() and not l.startswith('WEBVTT')]
full_transcript = ' '.join(clean_lines)

STOCK_DICT = {
    'NVDA': ['엔비디아', '엔비디'], 'AAPL': ['애플'], 'MSFT': ['마이크로소프트', '마소'],
    'AMZN': ['아마존'], 'GOOGL': ['구글', '알파벳'], 'META': ['메타', '페이스북'],
    'TSLA': ['테슬라'], 'AVGO': ['브로드컴'], 'COST': ['코스트코'],
    'LLY': ['일라이릴리', '릴리'], 'AMD': ['에이엠디', 'AMD'], 'QCOM': ['퀄컴'],
    'PLTR': ['팔란티어'], 'SMCI': ['슈퍼마이크로'], 'MU': ['마이크론'],
    'ARM': ['암'], 'VST': ['비스트라', 'VST'], 'CEG': ['콘스텔레이션', 'CEG'],
    'GEV': ['지이베르노바', 'GEV'], 'ETN': ['이튼', 'ETN'],
    '005930.KS': ['삼성전자', '삼전'], '000660.KS': ['SK하이닉스', '하이닉스'],
    '012450.KS': ['한화에어로스페이스', '한화에어로']
}

POS_KEYWORDS = [
    '추천', '좋게', '좋다', '좋은', '계속 보고', '관심', '주목', '담아', '모아',
    '눌림목', '기회', '매수', '순환매', '사이클', '넘어왔', '수급', '바닥', '지지',
    '실적', '성장', '반등', '타점', '긍정', '우상향', '돌파', '강세', '주도'
]

NEG_KEYWORDS = [
    '조심', '위험', '빠진', '빠지', '떨어', '하락', '폭락', '붕괴', '던져', '매도',
    '손절', '축소', '악재', '물린', '박살', '안 좋', '우려', '리스크', '부담',
    '어렵', '힘들', '경고', '이탈', '꺾'
]

analysis_results = {}

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
            start = max(0, m.start() - 100)
            end = min(len(full_transcript), m.end() + 100)
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
            intent = 'BULLISH_RECOMMENDED (적극 추천/관심)'
        elif neg_score > pos_score + 1:
            intent = 'BEARISH_CAUTION (우려/하락 경고)'
        else:
            intent = 'NEUTRAL_WATCH (단순 언급/관망)'
            
        analysis_results[ticker] = {
            'mentions': mentions,
            'pos_score': pos_score,
            'neg_score': neg_score,
            'net_score': net_score,
            'intent': intent,
            'pos_words': list(matched_pos_words)[:4],
            'neg_words': list(matched_neg_words)[:4],
            'snippet': sample_snippets[0] if sample_snippets else ''
        }

print('=== Contextual Sentiment & Intent Analysis on Live Stream ===')
for t, r in sorted(analysis_results.items(), key=lambda x: x[1]['net_score'], reverse=True):
    print(f"[{t}] {r['intent']} | Mention: {r['mentions']}회 | Pos: +{r['pos_score']} {r['pos_words']} | Neg: -{r['neg_score']} {r['neg_words']}")
    if r['snippet']:
        print(f"   Context: ... {r['snippet'][:90]} ...")
