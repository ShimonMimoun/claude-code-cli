"""Windows installer — scheduled tasks and managed settings.

Creates two scheduled tasks:
- **ClaudeCodeAutoStart**: runs ``claude-agent setup-env && claude-code`` at logon.
- **ClaudeCodeTokenRefresh**: runs ``claude-agent refresh-loop`` every 3 hours.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from claude_code_internal.config import LLM_GATEWAY_URL, build_managed_settings_data
from claude_code_internal.logging_config import get_logger

from ._base import base_dir, cleanup_claude_dir, cleanup_managed_settings, write_managed_settings

logger = get_logger(__name__)


def _find_agent_exe() -> str:
    """Locate the ``claude-agent.exe`` binary."""
    env = os.environ.get("CLAUDE_AGENT_PATH")
    if env:
        return env
    return str(base_dir() / "claude-agent.exe")


def _managed_settings_path() -> Path:
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    return Path(program_files) / "ClaudeCode" / "managed-settings.json"


def create_scheduled_tasks() -> None:
    """Register Windows scheduled tasks for Claude Code."""
    llm_gateway = os.environ.get("LLM_GATEWAY_URL", LLM_GATEWAY_URL)
    data = build_managed_settings_data(llm_gateway)
    write_managed_settings(_managed_settings_path(), data)

    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    claude_exe = Path(program_files) / "ClaudeCode" / "claude-code.exe"
    agent_exe = _find_agent_exe()

    # ── Auto-start task ─────────────────────────────────────────────────
    autostart_cmd = f'cmd.exe /c "\\"{agent_exe}\\" setup-env && \\"{claude_exe}\\""'
    logger.info("Creating scheduled task ClaudeCodeAutoStart")
    subprocess.check_call(
        [
            "schtasks", "/Create",
            "/TN", "ClaudeCodeAutoStart",
            "/TR", autostart_cmd,
            "/SC", "ONLOGON",
            "/RL", "HIGHEST",
        ]
    )

    # ── Token refresh task ──────────────────────────────────────────────
    refresh_cmd = f'cmd.exe /c "\\"{agent_exe}\\" refresh-loop"'
    logger.info("Creating scheduled task ClaudeCodeTokenRefresh")
    subprocess.check_call(
        [
            "schtasks", "/Create",
            "/TN", "ClaudeCodeTokenRefresh",
            "/TR", refresh_cmd,
            "/SC", "HOURLY",
            "/MO", "3",
            "/RL", "HIGHEST",
        ]
    )


def delete_scheduled_tasks() -> None:
    """Remove Windows scheduled tasks and clean up files."""
    for name in ("ClaudeCodeAutoStart", "ClaudeCodeTokenRefresh"):
        logger.info("Deleting scheduled task %s", name)
        subprocess.call(["schtasks", "/Delete", "/TN", name, "/F"])

    cleanup_managed_settings(_managed_settings_path())
    cleanup_claude_dir()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: ``installer-windows [install|uninstall]``."""
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        logger.error("Usage: installer-windows [install|uninstall]")
        return 1
    cmd = argv[0]
    if cmd == "install":
        create_scheduled_tasks()
        return 0
    if cmd == "uninstall":
        delete_scheduled_tasks()
        return 0
    logger.error("Unknown command: %s", cmd)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
