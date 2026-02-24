"""Shared helpers for platform-specific installers.

This module extracts the code that was previously duplicated across
``linux.py``, ``macos.py``, and ``windows.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)

# Files cleaned up from ~/.claude during uninstall (common to all platforms).
_CLAUDE_DIR_FILES_TO_CLEAN = (
    "settings.json",
    "token.json",
    "get-token.cmd",
    "get-token.sh",
    "claude-agent.exe",
    "claude-agent",
)


def base_dir() -> Path:
    """Return the directory containing the current executable or source file.

    Works both when running from a PyInstaller bundle (``sys.frozen``)
    and during normal development.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Callers can override by passing __file__ if needed, but in the
    # context of the installers all three files used the same logic.
    return Path(__file__).resolve().parent


def write_managed_settings(path: Path, data: dict) -> None:
    """Write the managed-settings JSON file, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Wrote managed settings to %s", path)


def cleanup_claude_dir(extra_files: Iterable[str] = ()) -> None:
    """Remove well-known Claude files from ``~/.claude``.

    Parameters
    ----------
    extra_files:
        Additional filenames to remove (platform-specific).
    """
    claude_dir = Path.home() / ".claude"
    targets = list(_CLAUDE_DIR_FILES_TO_CLEAN) + list(extra_files)
    for filename in targets:
        fp = claude_dir / filename
        try:
            if fp.exists():
                fp.unlink()
                logger.info("Removed %s", fp)
        except OSError:
            logger.warning("Failed to remove %s", fp, exc_info=True)


def cleanup_managed_settings(path: Path) -> None:
    """Remove the managed-settings file if it exists."""
    try:
        if path.exists():
            path.unlink()
            logger.info("Removed managed settings %s", path)
    except OSError:
        logger.warning("Failed to remove %s", path, exc_info=True)
