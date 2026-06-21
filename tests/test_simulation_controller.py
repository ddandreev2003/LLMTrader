import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from core.simulation_controller import SimulationController, SimStatus


@pytest.mark.asyncio
async def test_controller_pause_step_play():
    ctrl = SimulationController(auto_interval_ms=0)
    ctrl.configure(total_ticks=5)

    await ctrl.start()
    assert ctrl.status == SimStatus.PAUSED

    await ctrl.step()
    assert await ctrl.wait_for_next_tick() is True
    ctrl.tick_completed(0)
    assert ctrl.current_tick == 1

    await ctrl.pause()
    assert ctrl.status == SimStatus.PAUSED

    await ctrl.play(100)
    assert ctrl.status == SimStatus.RUNNING
    assert await ctrl.wait_for_next_tick() is True

    await ctrl.stop()
    assert ctrl.status == SimStatus.STOPPED
    assert await ctrl.wait_for_next_tick() is False


@pytest.mark.asyncio
async def test_controller_reset():
    ctrl = SimulationController()
    ctrl.configure(total_ticks=10)
    ctrl.tick_completed(5)
    await ctrl.reset()
    assert ctrl.status == SimStatus.IDLE
    assert ctrl.current_tick == 0


@pytest.mark.asyncio
async def test_web_api_start_step_pause():
    pytest.importorskip("fastapi")
    import web.routes as routes
    from web.server import app

    routes._session = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.put("/api/config", json={
            "market_mode": "synthetic",
            "shock_backend": "legacy",
            "sim_n_ticks": 5,
            "shock_interval": 100,
        })
        assert r.status_code == 200

        r = await client.post("/api/sim/start")
        assert r.status_code == 200

        for _ in range(3):
            r = await client.post("/api/sim/step")
            assert r.status_code == 200
            for _ in range(50):
                await asyncio.sleep(0.02)
                state_r = await client.get("/api/state")
                if state_r.json().get("current_tick", 0) >= _ + 1:
                    break

        r = await client.get("/api/state")
        assert r.status_code == 200
        state = r.json()
        assert state["current_tick"] >= 1

        r = await client.post("/api/sim/pause")
        assert r.status_code == 200

        r = await client.post("/api/sim/stop")
        assert r.status_code == 200

        r = await client.post("/api/sim/reset")
        assert r.status_code == 200
