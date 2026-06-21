#!/usr/bin/env python3
"""Launch the LLMSimTrade web control console."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    import uvicorn

    try:
        import websockets  # noqa: F401 — required for /ws
    except ImportError:
        print("Ошибка: для WebSocket нужен пакет websockets.")
        print("  pip install 'uvicorn[standard]'   # или: pip install websockets")
        sys.exit(1)

    port = int(os.environ.get("WEB_PORT", os.environ.get("VIZ_PORT", "8765")))
    os.environ.setdefault("WEB_UI", "true")
    os.environ.setdefault("SHOCK_BACKEND", "legacy")
    print(f"LLMSimTrade web console → http://127.0.0.1:{port}/")
    uvicorn.run("web.server:app", host="127.0.0.1", port=port, reload=False)
