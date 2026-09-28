# 알상무 퀀트 트레이딩 시스템 v2: 추천 5대 오픈소스 도입 계획서
> **문서 버전**: 1.0.0  
> **대상 시스템**: `al-sangmoo-quant-bot` (FastAPI + SQLite WAL + KIS OpenAPI + 일목균형표/MSI 2.0 스윙 봇)  
> **작성일**: 2026-08-27  

---

## 📌 Executive Summary (개요)

본 문서는 **알상무 퀀트 트레이딩 봇(Al-Sangmoo Institutional Quant Platform)**의 성능, 안정성, 데이터 파이프라인 및 전략 다변화를 위해 선별된 **5대 오픈소스 퀀트 레포지토리**의 심층 분석과 구체적인 시스템 이식 계획을 정의합니다.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Al-Sangmoo Quant Bot Architecture 2.0                           │
├────────────────────────┬──────────────────────────────────┬────────────────────────────┤
│  1. Riskfolio-Lib      │  2. Nautilus Trader              │  3. Sunday Quant Scientist │
│  [동적 포지션 사이징]  │  [Pre-Trade 가드레일/상태머신]   │  [모멘텀/변동성 팩터 고도화]│
│  - HRP 포트폴리오 배분 │  - 멱등성 보장 Order State Machine│  - 잔차 모멘텀 / 레짐 필터 │
├────────────────────────┼──────────────────────────────────┼────────────────────────────┤
│  4. Howtrader          │  5. QuantMuse                    │                            │
│  [웹훅 & 실행 인터페이스]│  [비동기 AI 데이터 파이프라인]   │                            │
│  - TradingView 웹훅 수신│  - 유튜브/뉴스 비동기 감성 분석  │                            │
└────────────────────────┴──────────────────────────────────┴────────────────────────────┘
```

---

## 1. [dcajasn/Riskfolio-Lib] 포트폴리오 최적화 및 동적 포지션 사이징

### 1.1 레포지토리 세부 분석
* **핵심 기술**: Python, `cvxpy`, Convex Optimization, SciPy
* **도입 필요성**: 현재 시스템의 `position_sizer.py`는 고정 3슬롯(종목당 1,000만원 또는 $7,500) 균등 분할 방식을 채택하고 있어 종목별 변동성 차이 및 상관관계에 따른 포트폴리오 꼬리 위험(Tail Risk) 관리가 부재함.
* **적용 모델**:
  1. **HRP (Hierarchical Risk Parity)**: 공분산 행렬의 역행렬을 구하지 않고 머신러닝 클러스터링(트리 구조)을 통해 자산을 배분하여 안정성이 극대화됨.
  2. **Mean-CVaR (조건부 위험가치 최적화)**: 극단적 하락장에서의 예상 손실을 최소화하는 비중 배분.

### 1.2 이식 대상 파일 및 아키텍처
* **대상 파일**:
  * `al_sangmoo/domain/risk/position_sizer.py`
  * `al_sangmoo/domain/quant/conviction_engine.py`

### 1.3 구체적 구현 코드 예시
```python
# al_sangmoo/domain/risk/riskfolio_sizer.py
from typing import Dict, List
import pandas as pd
import numpy as np
import riskfolio as rp

class RiskfolioPositionSizer:
    def __init__(self, total_capital: float, max_weight_per_asset: float = 0.40):
        self.total_capital = total_capital
        self.max_weight_per_asset = max_weight_per_asset

    def calculate_hrp_weights(self, price_history_df: pd.DataFrame) -> Dict[str, float]:
        """
        종목별 일간 종가 시계열 데이터를 기반으로 HRP 최적 가중치를 산출합니다.
        :param price_history_df: DataFrame (Index: Date, Columns: Tickers, Values: Close Prices)
        :return: Dict[ticker, allocated_capital]
        """
        returns = price_history_df.pct_change().dropna()
        if returns.empty or len(returns.columns) < 2:
            # 단일 종목이거나 데이터 부족 시 균등 분할 Fallback
            n = len(price_history_df.columns)
            return {ticker: self.total_capital / max(1, n) for ticker in price_history_df.columns}

        # 1. 포트폴리오 객체 생성
        port = rp.HCPortfolio(returns=returns)

        # 2. HRP 최적화 실행 (분산 최적화 + 링크 클러스터링)
        weights = port.optimization(
            model='HRP',
            codependence='pearson',
            rm='MV', # Variance
            rf=0.035 / 252, # 무위험수익률
            linkage='ward',
            max_k=10,
            leaf_order=True
        )

        # 3. 비중 상한선(Cap) 적용 및 자본금 환산
        weight_dict = weights.to_dict()['weights']
        allocated_capital = {}
        for ticker, weight in weight_dict.items():
            capped_weight = min(weight, self.max_weight_per_asset)
            allocated_capital[ticker] = round(self.total_capital * capped_weight, 2)

        return allocated_capital
```

---

## 2. [nautechsystems/nautilus_trader] Pre-Trade 리스크 엔진 & 주문 상태 머신

### 2.1 레포지토리 세부 분석
* **핵심 기술**: Rust Core, Event-Driven Architecture, Strict Finite State Machine (FSM), Pre-trade Guardrails
* **도입 필요성**: API 타임아웃, 중복 주문 요청, 급격한 스프레드 확대 시 발생할 수 있는 오작동을 차단하기 위해 기관급 멱등성(Idempotency) 및 상태 전이 검증이 필수적임.
* **적용 모델**:
  1. **Order FSM (주문 상태 머신)**: `PENDING_SUBMIT` $\to$ `SUBMITTED` $\to$ `ACCEPTED` $\to$ `FILLED` / `CANCELED`
  2. **Pre-Trade Risk Engine**: 잔고 검증, 슬리피지 허용치 초과 검사, 호가 스프레드 검사를 단일 파이프라인화.

### 2.2 이식 대상 파일 및 아키텍처
* **대상 파일**:
  * `al_sangmoo/domain/risk/order_guardrail.py`
  * `al_sangmoo/infrastructure/kis_broker.py`
  * `al_sangmoo/domain/risk/portfolio_guardian.py`

### 2.3 구체적 구현 코드 예시
```python
# al_sangmoo/domain/risk/order_state_machine.py
from enum import Enum, auto
import asyncio
import time
from typing import Dict, Optional

class OrderStatus(Enum):
    CREATED = auto()
    PRE_CHECK_PASSED = auto()
    SUBMITTING = auto()
    ACCEPTED = auto()
    FILLED = auto()
    REJECTED = auto()
    CANCELED = auto()
    EXPIRED = auto()

class NautilusInspiredOrderGuardrail:
    def __init__(self, max_spread_bps: float = 50.0, order_timeout_sec: float = 5.0):
        self.max_spread_bps = max_spread_bps
        self.order_timeout_sec = order_timeout_sec
        self._inflight_orders: Dict[str, OrderStatus] = {}
        self._mutex = asyncio.Lock()

    async def pre_trade_risk_check(self, ticker: str, side: str, ask_price: float, bid_price: float) -> bool:
        """스프레드 및 주문 중복 여부를 원자적으로 검사"""
        async with self._mutex:
            if ticker in self._inflight_orders:
                raise RuntimeError(f"중복 주문 거부: {ticker}에 대해 이미 진행 중인 주문({self._inflight_orders[ticker].name})이 존재합니다.")
            
            # 1. 호가 스프레드(BPS) 검사
            mid_price = (ask_price + bid_price) / 2.0
            spread_bps = ((ask_price - bid_price) / mid_price) * 10000.0
            if spread_bps > self.max_spread_bps:
                raise ValueError(f"리스크 거부: {ticker} 스프레드({spread_bps:.1f} bps)가 허용치({self.max_spread_bps} bps)를 초과했습니다.")
            
            self._inflight_orders[ticker] = OrderStatus.PRE_CHECK_PASSED
            return True

    def transition_state(self, ticker: str, next_state: OrderStatus):
        """주문 상태 전이 및 인플라이트 해제"""
        if next_state in [OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.EXPIRED]:
            self._inflight_orders.pop(ticker, None)
        else:
            self._inflight_orders[ticker] = next_state
```

---

## 3. [quant-science/sunday-quant-scientist] 모멘텀/변동성 및 레짐 팩터 보강

### 3.1 레포지토리 세부 분석
* **핵심 기술**: Python, Pandas, Statsmodels, Cointegration, Factor Analytics
* **도입 필요성**: 현재 일목균형표와 단순 모멘텀에 의존하는 진입 조건을 **시장 대비 초과수익(잔차 모멘텀, Residual Momentum)** 및 **변동성 역가중 지표**로 고도화하여 가짜 돌파(False Breakout) 필터링.
* **적용 모델**:
  1. **Residual Momentum (잔차 모멘텀)**: 시장 지수(S&P500/KOSPI) 베타를 제거한 순수 개별 종목의 알파 모멘텀 산출.
  2. **Vol-Adjusted Breakout**: 변동성 대비 돌파 강도 측정 (Z-Score 기반).

### 3.2 이식 대상 파일 및 아키텍처
* **대상 파일**:
  * `al_sangmoo/domain/quant/scoring.py`
  * `al_sangmoo/domain/quant/ichimoku.py`

### 3.3 구체적 구현 코드 예시
```python
# al_sangmoo/domain/quant/factor_extensions.py
import numpy as np
import pandas as pd
import statsmodels.api as sm

def calculate_residual_momentum_score(asset_prices: pd.Series, benchmark_prices: pd.Series, window: int = 60) -> float:
    """
    시장(벤치마크) 지수 대비 개별 자산의 순수 잔차 모멘텀(Alpha)을 계산합니다.
    :return: -100 ~ +100 정규화 스코어
    """
    if len(asset_prices) < window or len(benchmark_prices) < window:
        return 0.0

    asset_ret = asset_prices.pct_change().tail(window).dropna()
    bench_ret = benchmark_prices.pct_change().tail(window).dropna()
    
    # 회귀분석: Asset = alpha + beta * Benchmark + epsilon
    X = sm.add_constant(bench_ret)
    model = sm.OLS(asset_ret, X).fit()
    residuals = model.resid
    
    # 잔차의 평균을 잔차 표준편차로 나눈 샤프형 잔차 모멘텀 산출
    res_score = residuals.mean() / (residuals.std() + 1e-8)
    # Sigmoid 변환으로 -100 ~ 100 범위 매핑
    normalized_score = float(np.tanh(res_score * 15.0) * 100.0)
    return round(normalized_score, 2)
```

---

## 4. [51bitquant/howtrader] 트레이딩뷰 웹훅 게이트웨이 & 실시간 알림

### 4.1 레포지토리 세부 분석
* **핵심 기술**: FastAPI, Webhook Signature Verification, Telegram Alert Dispatcher
* **도입 필요성**: 트레이딩뷰(TradingView) 얼러트 지표나 외부 신호를 수신하여 봇의 `AutopilotTrader`와 유기적으로 연동하고, 체결 내역을 텔레그램/디스코드로 실시간 전송.
* **적용 모델**:
  1. **HMAC-SHA256 웹훅 서명 검증**: 외부 요청의 무결성 및 인가 보장.
  2. **비동기 알림 큐**: 체결 시 I/O 블로킹 없이 백그라운드 브로드캐스트.

### 4.2 이식 대상 파일 및 아키텍처
* **대상 파일**:
  * `server.py`
  * `al_sangmoo/interfaces/api/routers/autopilot.py`
  * `al_sangmoo/infrastructure/notifier.py` (신규 생성)

### 4.3 구체적 구현 코드 예시
```python
# al_sangmoo/interfaces/api/routers/webhook.py
from fastapi import APIRouter, Header, HTTPException, Request, BackgroundTasks
import hmac
import hashlib
import json

router = APIRouter(prefix="/api/v2/webhook", tags=["Webhook"])
WEBHOOK_SECRET = "YOUR_CONFIGURED_SIGNING_SECRET"

def verify_tradingview_signature(payload: bytes, signature: str) -> bool:
    expected_sig = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, signature)

@router.post("/tradingview")
async def handle_tradingview_signal(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str = Header(None)
):
    body = await request.body()
    if WEBHOOK_SECRET and (not x_signature or not verify_tradingview_signature(body, x_signature)):
        raise HTTPException(status_code=401, detail="Invalid Webhook Signature")
    
    payload = json.loads(body.decode())
    ticker = payload.get("ticker")
    action = payload.get("action") # BUY / SELL
    
    # 봇의 오토파일럿 큐에 주문 이벤트 디스패치
    # background_tasks.add_task(autopilot_service.process_webhook_signal, ticker, action)
    return {"status": "SUCCESS", "ticker": ticker, "action": action}
```

---

## 5. [0xemmkty/QuantMuse] 비동기 데이터 스트림 & AI/LLM 감성 분석 파이프라인

### 5.1 레포지토리 세부 분석
* **핵심 기술**: Asyncio Pipeline, LLM Text Embedding / Sentiment Parsing, Non-blocking Queue
* **도입 필요성**: 현재 `youtube_stream_scanner.py` 및 유튜브 자막 스트림(`live_sub_*.vtt`)에서 시황 키워드를 파싱하여 매크로 지수(`MSI 2.0`)에 반영할 때, 비동기 파이프라인 구조를 도입하여 응답 지연을 제거.
* **적용 모델**:
  1. **Async Token Buffer**: 스트리밍 자막 텍스트의 청크화 및 비동기 감성 점수화.
  2. **MSI 연동 가중치**: 감성 점수(-1.0 ~ +1.0)를 MSI(0~100)의 뉴스 센티먼트 서브 팩터로 자동 주입.

### 5.2 이식 대상 파일 및 아키텍처
* **대상 파일**:
  * `youtube_stream_scanner.py`
  * `al_sangmoo/domain/quant/macro.py`
  * `generate_dashboard_feed.py`

### 5.3 구체적 구현 코드 예시
```python
# al_sangmoo/domain/quant/stream_sentiment_pipeline.py
import asyncio
from typing import AsyncGenerator, Dict

class StreamSentimentPipeline:
    def __init__(self, macro_engine):
        self.macro_engine = macro_engine
        self._queue = asyncio.Queue()

    async def ingest_subtitles_stream(self, text_chunk: str):
        """자막 스트림 청크 수신 후 비동기 큐 삽입"""
        await self._queue.put(text_chunk)

    async def run_worker(self):
        """백그라운드에서 실시간 키워드 및 감성 스코어를 파싱하여 MSI에 반영"""
        while True:
            chunk = await self._queue.get()
            sentiment_delta = self._evaluate_sentiment(chunk)
            
            # MSI 2.0에 센티먼트 피드백 반영
            self.macro_engine.update_stream_sentiment(sentiment_delta)
            self._queue.task_done()

    def _evaluate_sentiment(self, text: str) -> float:
        bullish_keywords = ["바닥 확인", "돌파", "반등", "외인 순매수", "주도주", "골든크로스"]
        bearish_keywords = ["하락 전환", "이탈", "리스크", "환율 급등", "투매", "데드크로스"]
        
        score = 0.0
        for kw in bullish_keywords:
            if kw in text: score += 1.0
        for kw in bearish_keywords:
            if kw in text: score -= 1.0
            
        return max(-5.0, min(5.0, score))
```

---

## 6. 통합 로드맵 및 단계별 실행 계획 (Milestones)

| 마일스톤 | 소요 기간 | 주요 작업 내용 | 연관 레포지토리 |
| :--- | :---: | :--- | :--- |
| **Phase 1: 리스크 & 자산배분 강화** | 1주차 | • `position_sizer.py`에 `Riskfolio-Lib` HRP 엔진 결합<br>• `order_guardrail.py`에 `Nautilus` 주문 상태 머신 및 멱등성 Mutex 적용 | `Riskfolio-Lib`, `Nautilus Trader` |
| **Phase 2: 팩터 및 웹훅 확장** | 2주차 | • `scoring.py`에 `Sunday-Quant` 잔차 모멘텀 팩터 추가<br>• `server.py`에 `Howtrader` TradingView 웹훅 라우터 구현 | `Sunday-Quant`, `Howtrader` |
| **Phase 3: 비동기 AI 파이프라인** | 3주차 | • `youtube_stream_scanner.py` 비동기 파이프라인화 (`QuantMuse`)<br>• 실시간 MSI 2.0 자동 갱신 및 프론트엔드 WS 동기화 | `QuantMuse` |
| **Phase 4: 통합 백테스트 및 검증** | 4주차 | • 모의투자(Paper Trading) 환경 100% 회귀 테스트<br>• 포트폴리오 MDD 및 샤프 지수 개선치 정량 검증 | 전체 |

---

## 7. 검증 및 롤백 계획 (Verification & Safety)

1. **단위 테스트 (Unit Tests)**:
   * `pytest tools_and_tests/test_riskfolio_sizer.py` (HRP 가중치 합이 1.0이고 슬롯 상한을 준수하는지 검증)
   * `pytest tools_and_tests/test_order_guardrail_fsm.py` (중복 주문 및 타임아웃 멱등성 검증)
2. **Feature Toggle (기능 플래그)**:
   * `.env` 설정에 `USE_RISKFOLIO_HRP=True/False`, `USE_WEBHOOK_ROUTER=True/False` 플래그를 두어 언제든 기존 고정 슬롯 방식으로 원클릭 롤백이 가능하도록 보장.

---
*본 계획서는 알상무 퀀트 봇 v2 프로젝트 표준 규격을 준수하여 작성되었습니다.*
