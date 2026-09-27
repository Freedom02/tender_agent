from __future__ import annotations

import os
from pathlib import Path
import sys


def _restart_with_project_python() -> None:
    """Use the project environment when run from a system Python shell."""
    if os.name != "nt":
        return
    project_python = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if not project_python.is_file():
        return
    if Path(sys.executable).resolve() == project_python.resolve():
        return
    os.execv(str(project_python), [str(project_python), *sys.argv])


_restart_with_project_python()

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("TENDER_AGENT_PORT", "8797"))
    reload_enabled = os.getenv("TENDER_AGENT_RELOAD", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        reload=reload_enabled,
    )
