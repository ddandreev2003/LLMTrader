"""REST and WebSocket routes for simulation control."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.simulation_session import SimulationSession, list_config_presets
from web.schemas import ConfigUpdate, PlayRequest, StateResponse

router = APIRouter()

_session: SimulationSession | None = None
_ws_clients: set[WebSocket] = set()
_broadcast_lock = asyncio.Lock()


def get_session() -> SimulationSession:
    global _session
    if _session is None:
        _session = SimulationSession(on_state_change=_schedule_broadcast)
    return _session


def _schedule_broadcast(state: dict):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_state(state))
    except RuntimeError:
        pass


async def broadcast_state(state: dict | None = None):
    async with _broadcast_lock:
        if not _ws_clients:
            return
        payload = state if state is not None else get_session().get_state()
        message = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in _ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _ws_clients.discard(ws)


@router.get("/api/state", response_model=StateResponse)
async def api_state():
    return get_session().get_state()


@router.post("/api/sim/start")
async def api_start():
    session = get_session()
    await session.start()
    state = session.get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"]}


@router.post("/api/sim/pause")
async def api_pause():
    await get_session().pause()
    state = get_session().get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"]}


@router.post("/api/sim/step")
async def api_step():
    session = get_session()
    ok = await session.step()
    if not ok:
        raise HTTPException(409, "Cannot step in current state")
    state = session.get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"], "tick": state["tick"]}


@router.post("/api/sim/play")
async def api_play(body: PlayRequest):
    session = get_session()
    await session.play(body.interval_ms)
    state = session.get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"], "interval_ms": body.interval_ms}


@router.post("/api/sim/stop")
async def api_stop():
    await get_session().stop()
    state = get_session().get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"]}


@router.post("/api/sim/reset")
async def api_reset():
    await get_session().reset()
    state = get_session().get_state()
    await broadcast_state(state)
    return {"ok": True, "status": state["status"]}


@router.get("/api/config")
async def api_get_config():
    return get_session().get_config()


@router.put("/api/config")
async def api_put_config(body: ConfigUpdate):
    session = get_session()
    if not session.can_edit_config():
        raise HTTPException(409, "Config can only be changed in idle or paused at tick 0")
    try:
        cfg = session.update_config(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.reset()
    return {"ok": True, "config": cfg}


@router.get("/api/config/presets")
async def api_config_presets():
    return {"presets": list_config_presets(), "strategies": [
        {"name": "strategies_ru.yaml", "path": "config/strategies_ru.yaml"},
        {"name": "strategies_portfolio_manual.yaml", "path": "config/strategies_portfolio_manual.yaml"},
    ]}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps(get_session().get_state(), ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
