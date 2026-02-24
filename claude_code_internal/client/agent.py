"""Claude Code agent — authentication, token management, and installer.

This module handles:
- Entra ID (Azure AD) authentication via device-code flow or ``az`` CLI.
- Internal JWT token lifecycle (acquire, cache, refresh).
- Cross-platform Claude Code binary installation.
- Helper script generation so Claude Code can retrieve tokens.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx
import msal

from claude_code_internal.config import (
    AUTH_SERVER_URL,
    CLAUDE_SETTINGS_DIR,
    CLAUDE_SETTINGS_FILE,
    ENTRA_CLIENT_ID,
    ENTRA_SCOPES,
    ENTRA_TENANT_ID,
    HELPER_SH,
    HELPER_WIN,
    INSTALL_SERVER_URL,
    JWT_REFRESH_MARGIN_SECONDS,
    JWT_TTL_SECONDS,
    LOCAL_AGENT_NIX,
    LOCAL_AGENT_WIN,
    TOKEN_STORE_FILE,
)
from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)


# ── Token data ──────────────────────────────────────────────────────────────


@dataclass
class TokenData:
    """In-memory representation of an internal JWT token pair."""

    access_token: str
    refresh_token: str
    expires_at: int

    @classmethod
    def from_json(cls, data: dict) -> TokenData:
        """Deserialize from a JSON-compatible dictionary."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )

    def to_json(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    def is_expiring_soon(self, margin_seconds: int = JWT_REFRESH_MARGIN_SECONDS) -> bool:
        """Return ``True`` if the token expires within *margin_seconds*."""
        return self.expires_at - int(time.time()) < margin_seconds


# ── Filesystem helpers ──────────────────────────────────────────────────────


def ensure_directories() -> None:
    """Create ``~/.claude`` if it does not exist."""
    CLAUDE_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def get_os() -> str:
    """Return a normalized OS identifier: ``windows``, ``macos``, or ``linux``."""
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def write_settings_json() -> None:
    """Write the Claude Code ``settings.json`` with the helper path."""
    ensure_directories()
    os_name = get_os()
    helper_path = str(HELPER_WIN if os_name == "windows" else HELPER_SH)
    data = {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "env": {
            "CLAUDE_CODE_API_KEY_HELPER": helper_path,
        },
    }
    with CLAUDE_SETTINGS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("Wrote settings to %s", CLAUDE_SETTINGS_FILE)


# ── Token persistence ──────────────────────────────────────────────────────


def save_token(token: TokenData) -> None:
    """Persist *token* to ``~/.claude/token.json``."""
    ensure_directories()
    with TOKEN_STORE_FILE.open("w", encoding="utf-8") as f:
        json.dump(token.to_json(), f)
    logger.debug("Token saved to %s", TOKEN_STORE_FILE)


def load_token() -> Optional[TokenData]:
    """Load a previously-saved token or return ``None``."""
    if not TOKEN_STORE_FILE.exists():
        return None
    with TOKEN_STORE_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return TokenData.from_json(data)


# ── Executable introspection ────────────────────────────────────────────────


def _is_frozen() -> bool:
    """Return ``True`` when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def _self_executable_path() -> Path:
    return Path(sys.executable)


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


# ── Device ID ───────────────────────────────────────────────────────────────


def get_device_id() -> Optional[str]:
    """Return a unique device identifier, or ``None`` on failure."""
    os_name = get_os()
    try:
        if os_name == "windows":
            out = subprocess.check_output(
                [
                    "powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
                ],
                text=True,
            ).strip()
            return out or None
        if os_name == "macos":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        return parts[3]
            return None
        # Linux
        mid = Path("/etc/machine-id")
        if mid.exists():
            return mid.read_text(encoding="utf-8").strip() or None
    except OSError:
        logger.warning("Failed to retrieve device ID", exc_info=True)
        return None
    return None


# ── Agent binary / helper ──────────────────────────────────────────────────


def ensure_local_agent_and_helper() -> None:
    """Copy the agent binary into ``~/.claude`` and write the helper script."""
    ensure_directories()
    os_name = get_os()

    if _is_frozen():
        src = _self_executable_path()
        dst = LOCAL_AGENT_WIN if os_name == "windows" else LOCAL_AGENT_NIX
        if src.exists():
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
                logger.info("Copied agent binary to %s", dst)
            if os_name != "windows":
                dst.chmod(0o755)

    if os_name == "windows":
        agent_path = str(LOCAL_AGENT_WIN if LOCAL_AGENT_WIN.exists() else _self_executable_path())
        HELPER_WIN.write_text(
            f'@echo off\r\n"{agent_path}" get-token\r\n', encoding="utf-8"
        )
    else:
        agent_path = str(LOCAL_AGENT_NIX if LOCAL_AGENT_NIX.exists() else _self_executable_path())
        HELPER_SH.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + f'"{agent_path}" get-token\n',
            encoding="utf-8",
        )
        HELPER_SH.chmod(0o755)
    logger.info("Helper script written for %s", os_name)


# ── Claude Code binary download ────────────────────────────────────────────


def install_claude_code() -> None:
    """Download and install the Claude Code binary for the current platform."""
    os_name = get_os()
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "x64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "x64"

    # Rosetta detection on macOS
    if os_name == "macos" and arch == "x64":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "sysctl.proc_translated"], text=True
            ).strip()
            if out == "1":
                arch = "arm64"
                logger.info("Rosetta detected — switching to arm64")
        except OSError:
            pass

    def is_musl_linux() -> bool:
        if os_name != "linux":
            return False
        try:
            out = subprocess.check_output(
                ["ldd", "/bin/ls"], stderr=subprocess.STDOUT, text=True
            )
            return "musl" in out.lower()
        except OSError:
            return (
                Path("/lib/libc.musl-x86_64.so.1").exists()
                or Path("/lib/libc.musl-aarch64.so.1").exists()
            )

    # Determine download URL and target path
    if os_name == "windows":
        platform_key = f"win32-{arch}"
        url = f"{INSTALL_SERVER_URL}/install/windows/{platform_key}/claude-code-setup.exe"
        target = (
            Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
            / "ClaudeCode"
            / "claude-code-setup.exe"
        )
    elif os_name == "macos":
        platform_key = f"darwin-{arch}"
        url = f"{INSTALL_SERVER_URL}/install/macos/{platform_key}/claude-code-setup.pkg"
        target = Path("/tmp/claude-code-setup.pkg")
    else:
        platform_key = f"linux-{arch}-musl" if is_musl_linux() else f"linux-{arch}"
        url = f"{INSTALL_SERVER_URL}/install/linux/{platform_key}/claude-code-setup.run"
        target = Path("/tmp/claude-code-setup.run")

    # Fallback URL without platform subdirectory
    ext = target.suffix[1:]  # exe / pkg / run
    fallback_url = f"{INSTALL_SERVER_URL}/install/{os_name}/claude-code-setup.{ext}"

    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading Claude Code from %s", url)
    try:
        with httpx.stream("GET", url, timeout=60.0) as r:
            r.raise_for_status()
            with target.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError:
        logger.warning("Primary download failed, trying fallback %s", fallback_url)
        with httpx.stream("GET", fallback_url, timeout=60.0) as r:
            r.raise_for_status()
            with target.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    if os_name != "windows":
        target.chmod(0o755)
    logger.info("Running installer %s", target)
    subprocess.check_call([str(target), "install"])


# ── Entra ID authentication ────────────────────────────────────────────────


def get_entra_token_via_azure_cli() -> Optional[str]:
    """Try to obtain an Entra ID token via the ``az`` CLI."""
    if not _which("az"):
        return None
    try:
        out = subprocess.check_output(
            ["az", "account", "get-access-token", "--output", "json"], text=True
        )
    except (OSError, subprocess.CalledProcessError):
        logger.debug("az CLI token acquisition failed", exc_info=True)
        return None
    data = json.loads(out)
    return data.get("accessToken")


def get_entra_id_token_via_msal_device_code() -> str:
    """Acquire an Entra ID token using the MSAL device-code flow.

    Raises
    ------
    RuntimeError
        If required environment variables are missing or the flow fails.
    """
    if not ENTRA_TENANT_ID or not ENTRA_CLIENT_ID:
        raise RuntimeError("Missing ENTRA_TENANT_ID / ENTRA_CLIENT_ID environment variables")
    authority = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"
    pca = msal.PublicClientApplication(client_id=ENTRA_CLIENT_ID, authority=authority)

    flow = pca.initiate_device_flow(scopes=ENTRA_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device code flow init failed: {flow}")

    logger.info("Device code flow initiated — waiting for user authentication")
    print(flow["message"], file=sys.stderr)
    result = pca.acquire_token_by_device_flow(flow)
    if "error" in result:
        raise RuntimeError(
            f"MSAL device flow failed: {result.get('error_description') or result['error']}"
        )

    return result.get("id_token") or result.get("access_token")


# ── Internal token exchange ─────────────────────────────────────────────────


def exchange_entra_for_internal_token(
    id_token: str, device_id: Optional[str] = None
) -> TokenData:
    """Exchange an Entra ID token for an internal JWT pair."""
    payload = {"id_token": id_token, "device_id": device_id}
    resp = httpx.post(f"{AUTH_SERVER_URL}/auth/verify", json=payload, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    now = int(time.time())
    expires_at = now + int(data.get("expires_in", JWT_TTL_SECONDS))
    return TokenData(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
    )


def refresh_internal_token(token: TokenData) -> TokenData:
    """Refresh an internal JWT using its refresh token."""
    payload = {"refresh_token": token.refresh_token}
    resp = httpx.post(f"{AUTH_SERVER_URL}/auth/refresh", json=payload, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    now = int(time.time())
    expires_at = now + int(data.get("expires_in", JWT_TTL_SECONDS))
    new_token = TokenData(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
    )
    save_token(new_token)
    logger.info("Internal token refreshed successfully")
    return new_token


def ensure_token() -> TokenData:
    """Return a valid internal token, acquiring or refreshing as needed."""
    token = load_token()
    if token is None:
        logger.info("No cached token — starting authentication flow")
        device_id = get_device_id()
        entra_token = get_entra_token_via_azure_cli() or get_entra_id_token_via_msal_device_code()
        token = exchange_entra_for_internal_token(entra_token, device_id=device_id)
        save_token(token)
        return token
    if token.is_expiring_soon():
        logger.info("Token expiring soon — refreshing")
        token = refresh_internal_token(token)
    return token


# ── CLI sub-commands ────────────────────────────────────────────────────────


def get_token_cli() -> None:
    """Print the current access token to stdout (consumed by helpers)."""
    token = ensure_token()
    print(token.access_token, end="")


def refresh_loop_cli() -> None:
    """Run an infinite loop that pro-actively refreshes the token."""
    logger.info("Starting token refresh loop")
    while True:
        token = ensure_token()
        sleep_for = max(60, token.expires_at - int(time.time()) - JWT_REFRESH_MARGIN_SECONDS)
        logger.debug("Sleeping %d seconds until next refresh", sleep_for)
        time.sleep(sleep_for)


def setup_env() -> None:
    """One-shot environment setup: deploy helper, write settings, get token."""
    ensure_local_agent_and_helper()
    write_settings_json()
    ensure_token()
    logger.info("Environment setup complete")


def uninstall_local() -> None:
    """Remove all Claude agent files from ``~/.claude``."""
    for p in (
        CLAUDE_SETTINGS_FILE,
        TOKEN_STORE_FILE,
        HELPER_WIN,
        HELPER_SH,
        LOCAL_AGENT_WIN,
        LOCAL_AGENT_NIX,
    ):
        try:
            if p.exists():
                p.unlink()
                logger.info("Removed %s", p)
        except OSError:
            logger.warning("Failed to remove %s", p, exc_info=True)


# ── Entry point ─────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point: ``agent [install|get-token|refresh-loop|setup-env|uninstall]``."""
    argv = argv or sys.argv[1:]
    if not argv:
        logger.error("Usage: agent [install|get-token|refresh-loop|setup-env|uninstall]")
        return 1
    cmd = argv[0]
    commands = {
        "install": lambda: (install_claude_code(), setup_env()),
        "get-token": get_token_cli,
        "refresh-loop": refresh_loop_cli,
        "setup-env": setup_env,
        "uninstall": uninstall_local,
    }
    handler = commands.get(cmd)
    if handler is None:
        logger.error("Unknown command: %s", cmd)
        return 1
    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
