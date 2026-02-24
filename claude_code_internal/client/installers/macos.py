"""macOS installer — LaunchAgents and managed settings.

Creates two LaunchAgents:
- **com.company.claudecode.autostart**: runs ``claude-agent setup-env && claude-code`` at login.
- **com.company.claudecode.refresh**: runs ``claude-agent refresh-loop`` every 3 hours.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from claude_code_internal.config import LLM_GATEWAY_URL, build_managed_settings_data
from claude_code_internal.logging_config import get_logger

from ._base import base_dir, cleanup_claude_dir, cleanup_managed_settings, write_managed_settings

logger = get_logger(__name__)

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
MANAGED_DIR = Path("/Library/Application Support/ClaudeCode")
MANAGED_SETTINGS = MANAGED_DIR / "managed-settings.json"


def _write_plist(
    path: Path,
    label: str,
    program_args: list,
    run_at_load: bool = False,
    interval: Optional[int] = None,
) -> None:
    """Write a macOS LaunchAgent plist file."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        "<dict>",
        f"  <key>Label</key><string>{label}</string>",
        "  <key>ProgramArguments</key>",
        "  <array>",
    ]
    for arg in program_args:
        parts.append(f"    <string>{arg}</string>")
    parts.append("  </array>")
    if run_at_load:
        parts.append("  <key>RunAtLoad</key><true/>")
    if interval is not None:
        parts.append(f"  <key>StartInterval</key><integer>{interval}</integer>")
    parts.extend(["</dict>", "</plist>"])
    path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("Wrote plist %s", path)


def create_launch_agents() -> None:
    """Create LaunchAgents and write managed settings."""
    agent_path = os.environ.get("CLAUDE_AGENT_PATH", str(base_dir() / "claude-agent"))
    llm_gateway = os.environ.get("LLM_GATEWAY_URL", LLM_GATEWAY_URL)

    data = build_managed_settings_data(llm_gateway)
    write_managed_settings(MANAGED_SETTINGS, data)

    claude_bin = "/usr/local/bin/claude-code"

    autostart_plist = LAUNCH_AGENTS_DIR / "com.company.claudecode.autostart.plist"
    autostart_args = ["/bin/sh", "-c", f'"{agent_path}" setup-env && "{claude_bin}"']
    _write_plist(
        autostart_plist, "com.company.claudecode.autostart", autostart_args, run_at_load=True
    )

    refresh_plist = LAUNCH_AGENTS_DIR / "com.company.claudecode.refresh.plist"
    refresh_args = [agent_path, "refresh-loop"]
    _write_plist(refresh_plist, "com.company.claudecode.refresh", refresh_args, interval=10800)


def delete_launch_agents() -> None:
    """Remove LaunchAgents and clean up files."""
    for name in (
        "com.company.claudecode.autostart.plist",
        "com.company.claudecode.refresh.plist",
    ):
        path = LAUNCH_AGENTS_DIR / name
        if path.exists():
            path.unlink()
            logger.info("Removed %s", path)

    cleanup_managed_settings(MANAGED_SETTINGS)
    cleanup_claude_dir()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: ``installer-macos [install|uninstall]``."""
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        logger.error("Usage: installer-macos [install|uninstall]")
        return 1
    cmd = argv[0]
    if cmd == "install":
        create_launch_agents()
        return 0
    if cmd == "uninstall":
        delete_launch_agents()
        return 0
    logger.error("Unknown command: %s", cmd)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
