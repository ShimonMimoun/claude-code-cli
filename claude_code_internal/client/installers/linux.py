"""Linux installer — systemd user units and managed settings.

Creates two systemd user units:
- **claudecode-autostart.service**: runs ``claude-agent setup-env && claude-code`` at boot.
- **claudecode-refresh.timer**: triggers token refresh every 3 hours.
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

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
MANAGED_DIR = Path("/etc/claude-code")
MANAGED_SETTINGS = MANAGED_DIR / "managed-settings.json"


def _write_systemd_unit(path: Path, content: str) -> None:
    """Write a systemd unit file, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote systemd unit %s", path)


def create_systemd_units() -> None:
    """Create systemd units, write managed settings, and enable services."""
    agent_path = os.environ.get("CLAUDE_AGENT_PATH", str(base_dir() / "claude-agent"))
    llm_gateway = os.environ.get("LLM_GATEWAY_URL", LLM_GATEWAY_URL)

    data = build_managed_settings_data(llm_gateway)
    write_managed_settings(MANAGED_SETTINGS, data)

    # ── Refresh service ─────────────────────────────────────────────────
    refresh_service = SYSTEMD_USER_DIR / "claudecode-refresh.service"
    _write_systemd_unit(
        refresh_service,
        f"""\
[Unit]
Description=Claude Code token refresh

[Service]
Type=simple
ExecStart={agent_path} refresh-loop
Restart=always
""",
    )

    # ── Refresh timer ───────────────────────────────────────────────────
    refresh_timer = SYSTEMD_USER_DIR / "claudecode-refresh.timer"
    _write_systemd_unit(
        refresh_timer,
        """\
[Unit]
Description=Run Claude Code token refresh

[Timer]
OnBootSec=5min
OnUnitActiveSec=3h

[Install]
WantedBy=default.target
""",
    )

    # ── Auto-start service ──────────────────────────────────────────────
    claude_bin = "/usr/local/bin/claude-code"
    claude_service = SYSTEMD_USER_DIR / "claudecode-autostart.service"
    _write_systemd_unit(
        claude_service,
        f"""\
[Unit]
Description=Start Claude Code

[Service]
Type=simple
ExecStart=/bin/sh -c '{agent_path} setup-env && {claude_bin}'
Restart=on-failure

[Install]
WantedBy=default.target
""",
    )

    # ── Enable & start ──────────────────────────────────────────────────
    logger.info("Reloading systemd and enabling services")
    subprocess.check_call(["systemctl", "--user", "daemon-reload"])
    subprocess.check_call(["systemctl", "--user", "enable", "claudecode-autostart.service"])
    subprocess.check_call(["systemctl", "--user", "enable", "claudecode-refresh.timer"])
    subprocess.check_call(["systemctl", "--user", "start", "claudecode-autostart.service"])
    subprocess.check_call(["systemctl", "--user", "start", "claudecode-refresh.timer"])


def delete_systemd_units() -> None:
    """Stop, disable, and remove systemd units. Clean up files."""
    units = (
        "claudecode-autostart.service",
        "claudecode-refresh.service",
        "claudecode-refresh.timer",
    )
    for unit in units:
        logger.info("Stopping and disabling %s", unit)
        subprocess.call(["systemctl", "--user", "stop", unit])
        subprocess.call(["systemctl", "--user", "disable", unit])

    for name in units:
        path = SYSTEMD_USER_DIR / name
        if path.exists():
            path.unlink()
            logger.info("Removed %s", path)

    cleanup_managed_settings(MANAGED_SETTINGS)
    cleanup_claude_dir()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: ``installer-linux [install|uninstall]``."""
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        logger.error("Usage: installer-linux [install|uninstall]")
        return 1
    cmd = argv[0]
    if cmd == "install":
        create_systemd_units()
        return 0
    if cmd == "uninstall":
        delete_systemd_units()
        return 0
    logger.error("Unknown command: %s", cmd)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
