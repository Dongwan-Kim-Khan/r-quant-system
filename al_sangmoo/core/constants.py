"""
Centralized Constants & Single Source of Truth for R-Sangmoo Quant Trading Platform.
"""

WATCHLIST = [
    # Mega Tech & AI Platforms (10)
    "NVDA", "MSFT", "AMZN", "AAPL", "GOOGL", "META", "TSLA", "PLTR", "ORCL", "CRM",
    # Semiconductors Design & Foundry (10)
    "TSM", "AVGO", "AMD", "QCOM", "ARM", "MU", "INTC", "TXN", "005930.KS", "000660.KS",
    # Semiconductor Equipment & AI Infrastructure (6)
    "ASML", "AMAT", "LRCX", "KLAC", "SMCI", "MRVL",
    # Power, Energy & Nuclear (8)
    "VST", "CEG", "GEV", "ETN", "CCJ", "OKLO", "XOM", "CVX",
    # Cybersecurity, SaaS, Space & AdTech (8)
    "CRWD", "PANW", "NOW", "RKLB", "IONQ", "SNOW", "NET", "APP",
    # Financials, Fintech & Crypto (6)
    "JPM", "V", "MA", "COIN", "HOOD", "MSTR",
    # Global Defense & Aerospace (4)
    "LMT", "RTX", "NOC", "GE",
    # Healthcare & Global Consumers (8)
    "LLY", "UNH", "JNJ", "ISRG", "COST", "WMT", "NFLX", "DIS"
]

STOCK_DICT = {
    # Mega Tech & AI
    "NVDA": ["엔비디아", "엔비디", "NVIDIA", "NVDA"],
    "MSFT": ["마이크로소프트", "마이크로 소프트", "마소", "MSFT", "MICROSOFT"],
    "AMZN": ["아마존", "AMZN", "AMAZON"],
    "AAPL": ["애플", "AAPL", "APPLE"],
    "GOOGL": ["구글", "알파벳", "GOOGL", "GOOGLE", "ALPHABET"],
    "META": ["메타", "페이스북", "META", "FACEBOOK"],
    "TSLA": ["테슬라", "TSLA", "TESLA"],
    "PLTR": ["팔란티어", "PLTR", "PALANTIR"],
    "ORCL": ["오라클", "ORCL", "ORACLE"],
    "CRM": ["세일즈포스", "CRM", "SALESFORCE"],

    # Semiconductors
    "TSM": ["대만반도체", "티에스엠", "TSMC", "TSM"],
    "AVGO": ["브로드컴", "AVGO", "BROADCOM"],
    "AMD": ["에이엠디", "AMD"],
    "QCOM": ["퀄컴", "QCOM", "QUALCOMM"],
    "ARM": ["암", "ARM", "ARM HOLDINGS"],
    "MU": ["마이크론", "마이크론테크", "MU", "MICRON"],
    "INTC": ["인텔", "INTC", "INTEL"],
    "TXN": ["텍사스인스트루먼트", "TXN", "TEXAS INSTRUMENTS"],
    "ASML": ["에이에스엠엘", "ASML"],
    "AMAT": ["어플라이드", "어플라이드머티어리얼즈", "AMAT", "APPLIED MATERIALS"],
    "LRCX": ["램리서치", "LRCX", "LAM RESEARCH"],
    "KLAC": ["케이엘에이", "KLAC", "KLA"],
    "SMCI": ["슈퍼마이크로", "SMCI", "SUPERMICRO"],
    "MRVL": ["마벨", "마벨테크놀로지", "MRVL", "MARVELL"],

    # Power & Energy
    "VST": ["비스트라", "비스트라에너지", "VST", "VISTRA"],
    "CEG": ["콘스텔레이션", "CEG", "CONSTELLATION"],
    "GEV": ["지이베르노바", "베르노바", "GEV", "VERNOVA"],
    "ETN": ["이튼", "ETN", "EATON"],
    "CCJ": ["카메코", "CCJ", "CAMECO"],
    "OKLO": ["오클로", "OKLO"],
    "XOM": ["엑슨모빌", "XOM", "EXXON"],
    "CVX": ["셰브론", "CVX", "CHEVRON"],

    # Cybersecurity, SaaS & Space & AdTech
    "CRWD": ["크라우드스트라이크", "크라우드", "CRWD", "CROWDSTRIKE"],
    "PANW": ["팔로알토", "팔로알토네트웍스", "PANW", "PALO ALTO"],
    "NOW": ["서비스나우", "NOW", "SERVICENOW"],
    "RKLB": ["로켓랩", "RKLB", "ROCKET LAB"],
    "IONQ": ["아이온큐", "IONQ"],
    "SNOW": ["스노우플레이크", "스노우", "SNOW", "SNOWFLAKE"],
    "NET": ["클라우드플레어", "NET", "CLOUDFLARE"],
    "APP": ["앱러빈", "앱러빈코퍼레이션", "APP", "APPLOVIN"],

    # Financials & Crypto
    "JPM": ["제이피모건", "JP모건", "JPM", "JPMORGAN"],
    "V": ["비자", "V", "VISA"],
    "MA": ["마스터카드", "마스터", "MA", "MASTERCARD"],
    "COIN": ["코인베이스", "COIN", "COINBASE"],
    "HOOD": ["로빈후드", "HOOD", "ROBINHOOD"],
    "MSTR": ["마이크로스트래티지", "마스", "MSTR", "MICROSTRATEGY"],

    # Global Defense & Aerospace
    "LMT": ["록히드마틴", "록히드", "LMT", "LOCKHEED MARTIN"],
    "RTX": ["알티엑스", "레이시온", "RTX"],
    "NOC": ["노스롭그루먼", "노스롭", "NOC", "NORTHROP GRUMMAN"],
    "GE": ["GE에어로스페이스", "제너럴일렉트릭", "GE", "GE AEROSPACE"],

    # Healthcare & Consumers
    "LLY": ["일라이릴리", "일라이 릴리", "릴리", "LLY", "LILLY"],
    "UNH": ["유나이티드헬스", "유나이티드", "UNH", "UNITEDHEALTH"],
    "JNJ": ["존슨앤존슨", "JNJ", "JOHNSON"],
    "ISRG": ["인튜이티브서지컬", "다빈치로봇", "ISRG", "INTUITIVE SURGICAL"],
    "COST": ["코스트코", "COST", "COSTCO"],
    "WMT": ["월마트", "WMT", "WALMART"],
    "NFLX": ["넷플릭스", "넷플", "NFLX", "NETFLIX"],
    "DIS": ["디즈니", "월트디즈니", "DIS", "DISNEY"],

    # Korean Global Leaders
    "005930.KS": ["삼성전자", "삼전", "005930"],
    "000660.KS": ["SK하이닉스", "하이닉스", "000660"]
}

POS_KEYWORDS = [
    '추천', '좋게 보고', '좋게 본다', '좋게', '좋다', '좋은', '계속 보고', '계속 본다',
    '관심', '주목', '담아', '모아', '눌림목', '기회', '매수', '순환매', '사이클',
    '넘어왔', '수급', '바닥', '지지', '실적', '성장', '반등', '타점', '긍정',
    '우상향', '돌파', '강세', '주도', '유망', '담아야', '모아가야', '선점'
]

NEG_KEYWORDS = [
    '조심', '위험', '빠진', '빠지', '떨어', '하락', '폭락', '붕괴', '던져', '매도',
    '손절', '축소', '악재', '물린', '박살', '안 좋', '우려', '리스크', '부담',
    '어렵', '힘들', '경고', '이탈', '꺾', '비중 축소', '조정', '매수 금지'
]

MACRO_DEFENSE_KEYWORDS = [
    '이번 주는', '이번주는', '이번 주', '사지 말자', '사지말자', '사지 마', '관망하자',
    '관망', '쉬어가자', '쉬어가', '현금 확보', '현금 비중', '차익실현', '차익 실현',
    '소나기', '피하자', '전쟁', '지정학', '유가 급등', '금리 부담', '금리부담',
    '리스크 관리', '몸 사리자', '신규 매수 자제', '보류', '조심하자'
]

EXTERNAL_SHOCK_KEYWORDS = {
    "지정학적 분쟁 및 전쟁 리스크": ["전쟁", "이란", "이스라엘", "중동", "지정학", "호르무즈", "미사일", "하마스", "러시아", "우크라이나"],
    "인플레이션 및 원자재 변동성": ["유가", "WTI", "원자재", "인플레이션", "물가", "CPI", "PPI", "관세"],
    "금리 경로 및 통화정책 영향권": ["금리", "국채", "10년물", "연준", "FOMC", "파월", "매파", "긴축", "인하 지연", "빅스텝", "베이비스텝"],
    "무역 분쟁 및 관세 불확실성": ["관세", "보복", "수출 규제", "통상", "트럼프", "무역", "환율", "달러 강세"]
}
