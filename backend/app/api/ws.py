import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime import hub

log = logging.getLogger(__name__)
router = APIRouter(tags=["Realtime"])


@router.websocket("/ws/quotes")
async def quotes_stream(websocket: WebSocket) -> None:
    """Поток котировок. Сразу отдаёт снимок кэша, дальше — обновления."""
    await websocket.accept()
    await hub.register(websocket)
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            if message == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws closed: %s", exc)
    finally:
        await hub.unregister(websocket)
        with contextlib.suppress(Exception):
            await websocket.close()
