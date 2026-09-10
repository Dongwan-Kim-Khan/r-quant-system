"""QuantMuse-inspired async subtitle sentiment pipeline for MSI 2.0."""
from __future__ import annotations

import asyncio
from typing import Optional


class StreamSentimentPipeline:
    def __init__(self, macro_engine=None):
        self.macro_engine = macro_engine
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.last_delta: float = 0.0

    async def ingest_subtitles_stream(self, text_chunk: str) -> None:
        await self._queue.put(str(text_chunk or ""))

    async def drain_once(self) -> float:
        chunk = await self._queue.get()
        try:
            delta = self._evaluate_sentiment(chunk)
            self.last_delta = delta
            if self.macro_engine is not None and hasattr(self.macro_engine, "update_stream_sentiment"):
                self.macro_engine.update_stream_sentiment(delta)
            return delta
        finally:
            self._queue.task_done()

    def _evaluate_sentiment(self, text: str) -> float:
        bullish = ["바닥 확인", "돌파", "반등", "외인 순매수", "주도주", "골든크로스"]
        bearish = ["하락 전환", "이탈", "리스크", "환율 급등", "투매", "데드크로스"]
        score = 0.0
        for kw in bullish:
            if kw in text:
                score += 1.0
        for kw in bearish:
            if kw in text:
                score -= 1.0
        return max(-5.0, min(5.0, score))


_SENTIMENT_OFFSET = 0.0


def update_stream_sentiment(delta: float) -> float:
    """Clamp-accumulate a sentiment offset used as an MSI sub-factor (-10..+10)."""
    global _SENTIMENT_OFFSET
    _SENTIMENT_OFFSET = max(-10.0, min(10.0, _SENTIMENT_OFFSET + float(delta)))
    return _SENTIMENT_OFFSET


def get_stream_sentiment_offset() -> float:
    return float(_SENTIMENT_OFFSET)


def reset_stream_sentiment() -> None:
    global _SENTIMENT_OFFSET
    _SENTIMENT_OFFSET = 0.0
