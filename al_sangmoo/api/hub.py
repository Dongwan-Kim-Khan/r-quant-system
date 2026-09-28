"""
WebSocket Broadcast Gateway & Real-Time Event Hub.
"""
import asyncio
import time
import logging
from enum import Enum
from typing import List, Dict, Any, Union
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

MAX_CONNECTIONS = 50


class EventType(str, Enum):
    POSITION_DELTA = "POSITION_DELTA"
    PORTFOLIO_UPDATE = "portfolio_update"
    LIVE_FEED_UPDATE = "live_feed_update"
    PRICE_UPDATE = "price_update"
    GUARDIAN_ALERT = "guardian_alert"
    AUTOPILOT_BUY = "autopilot_buy_alert"
    ORDER_STATUS = "ORDER_STATUS"
    SYSTEM_STATUS = "SYSTEM_STATUS"
    SCAN_STATUS = "scan_status"
    CONNECTED = "connected"


class WebSocketBroadcastHub:
    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        self.max_connections = max_connections
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._seq = 0

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self.active_connections) >= self.max_connections:
                logger.warning("[WebSocket Hub] Connection limit (%d) reached. Rejecting client.", self.max_connections)
                if hasattr(websocket, "close"):
                    try:
                        await websocket.close(code=1008, reason="Connection limit exceeded")
                    except Exception:
                        pass
                return False

            if hasattr(websocket, "accept"):
                try:
                    await websocket.accept()
                except Exception as e:
                    logger.warning("[WebSocket Hub] Failed to accept websocket: %s", e)
                    return False
            self.active_connections.append(websocket)
            logger.info("[WebSocket Hub] Client connected. Active clients: %d", len(self.active_connections))
            return True

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info("[WebSocket Hub] Client disconnected. Active clients: %d", len(self.active_connections))

    async def _safe_close(self, ws: WebSocket, code: int = 1000, reason: str = "") -> None:
        if hasattr(ws, "close"):
            try:
                await ws.close(code=code, reason=reason)
            except Exception as e:
                logger.debug("[WebSocket Hub] Error closing dead websocket: %s", e)

    def _normalize_event(self, event_type: Union[EventType, str]) -> str:
        if isinstance(event_type, EventType):
            return event_type.value
        return str(event_type)

    async def broadcast(self, event_type: Union[EventType, str], data: Any = None) -> None:
        """
        Broadcasts an event message to all connected clients using non-blocking dispatch with timeouts.
        Automatically prunes disconnected clients.
        """
        event_name = self._normalize_event(event_type)
        async with self._lock:
            self._seq += 1
            seq = self._seq
            sockets = list(self.active_connections)

        message = {
            "event": event_name,
            "type": event_name,
            "data": data or {},
            "seq": seq,
            "timestamp": time.time(),
        }

        if not sockets:
            return

        async def send_to_client(ws: WebSocket) -> bool:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
                return True
            except Exception as e:
                logger.debug("[WebSocket Hub] send_json to client failed for event '%s': %s", event_name, e)
                return False

        results = await asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)
        dead_connections = [ws for ws, success in zip(sockets, results) if success is not True]

        if dead_connections:
            async with self._lock:
                for dead in dead_connections:
                    if dead in self.active_connections:
                        self.active_connections.remove(dead)

            for dead in dead_connections:
                asyncio.create_task(self._safe_close(dead, code=1011, reason="Broadcast timeout/error"))

    async def broadcast_delta(self, event_type: Union[EventType, str], data: Dict[str, Any]) -> None:
        """Send a field-level change without requiring clients to HTTP-refetch /api/dashboard."""
        await self.broadcast(event_type, data)


hub = WebSocketBroadcastHub()
