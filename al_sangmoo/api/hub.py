"""
WebSocket Broadcast Gateway & Real-Time Event Hub.
"""
import asyncio
import time
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

MAX_CONNECTIONS = 50

class WebSocketBroadcastHub:
    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        self.max_connections = max_connections
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self.active_connections) >= self.max_connections:
                if hasattr(websocket, "close"):
                    try:
                        await websocket.close(code=1008, reason="Connection limit exceeded")
                    except Exception:
                        pass
                return False

            if hasattr(websocket, "accept"):
                try:
                    await websocket.accept()
                except Exception:
                    return False
            self.active_connections.append(websocket)
            print(f"[WebSocket Hub] Client connected. Active clients: {len(self.active_connections)}")
            return True

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        print(f"[WebSocket Hub] Client disconnected. Active clients: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, data: Any = None) -> None:
        """
        Broadcasts an event message to all connected clients using non-blocking dispatch with timeouts.
        Automatically prunes disconnected clients.
        """
        message = {
            "event": event_type,
            "data": data or {},
            "timestamp": time.time()
        }
        
        async with self._lock:
            sockets = list(self.active_connections)
            
        if not sockets:
            return

        async def send_to_client(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(send_to_client(ws) for ws in sockets), return_exceptions=True)
        dead_connections = [ws for ws in results if ws is not None and not isinstance(ws, Exception)]

        if dead_connections:
            async with self._lock:
                for dead in dead_connections:
                    if dead in self.active_connections:
                        self.active_connections.remove(dead)

hub = WebSocketBroadcastHub()
