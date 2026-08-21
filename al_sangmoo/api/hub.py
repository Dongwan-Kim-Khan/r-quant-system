"""
WebSocket Broadcast Gateway & Real-Time Event Hub.
"""
import asyncio
import time
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

class WebSocketBroadcastHub:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        print(f"[WebSocket Hub] Client connected. Active clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        print(f"[WebSocket Hub] Client disconnected. Active clients: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, data: Any = None) -> None:
        """
        Broadcasts an event message to all connected clients.
        Automatically prunes disconnected clients.
        """
        message = {
            "event": event_type,
            "data": data or {},
            "timestamp": time.time()
        }
        
        async with self._lock:
            dead_connections = []
            for ws in self.active_connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead_connections.append(ws)
            for dead in dead_connections:
                if dead in self.active_connections:
                    self.active_connections.remove(dead)

hub = WebSocketBroadcastHub()
