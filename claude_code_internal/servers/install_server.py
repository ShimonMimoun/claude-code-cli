"""Install server — serves Claude Code binaries as static files.

Mounts the artifacts directory at ``/claude-code/install`` so that
the client agent can download platform-specific installers.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="Claude Code Install Server")


def _artifacts_dir() -> str:
    """Return the path to the directory containing installer artifacts.

    Configurable via the ``CLAUDE_CODE_INSTALL_ARTIFACTS_DIR`` environment
    variable.  Falls back to ``<repo-root>/install_artifacts``.
    """
    env = os.environ.get("CLAUDE_CODE_INSTALL_ARTIFACTS_DIR")
    if env:
        return env
    here = Path(__file__).resolve()
    root = here.parents[2]
    return str(root / "install_artifacts")


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


artifacts_path = _artifacts_dir()
if Path(artifacts_path).is_dir():
    app.mount("/claude-code/install", StaticFiles(directory=artifacts_path), name="install")
    logger.info("Serving install artifacts from %s", artifacts_path)
else:
    logger.warning(
        "Artifacts directory '%s' does not exist — /claude-code/install will not be available",
        artifacts_path,
    )
