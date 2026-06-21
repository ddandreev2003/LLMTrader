"""FastAPI application entry for the web control console."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web.routes import router

ROOT = Path(__file__).resolve().parent.parent
VIZ_DIR = ROOT / "viz"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("WEB_UI", "true")
    os.environ.setdefault("LIVE_VIZ", "true")
    yield


app = FastAPI(title="LLMSimTrade Web Console", lifespan=lifespan)
app.include_router(router)

if VIZ_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(VIZ_DIR)), name="static")


@app.get("/")
async def index():
    control = VIZ_DIR / "control.html"
    if control.exists():
        return FileResponse(control)
    return RedirectResponse("/static/dashboard.html")


@app.get("/dashboard.html")
async def dashboard_redirect():
    return RedirectResponse("/static/dashboard.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("WEB_PORT", os.environ.get("VIZ_PORT", "8765")))
    uvicorn.run("web.server:app", host="127.0.0.1", port=port, reload=False)
