"""Centralized configuration for claude_code_internal.

All environment variables and shared constants are defined here to avoid
scattering them across multiple modules.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Claude local directories ────────────────────────────────────────────────
CLAUDE_SETTINGS_DIR = Path.home() / ".claude"
CLAUDE_SETTINGS_FILE = CLAUDE_SETTINGS_DIR / "settings.json"
TOKEN_STORE_FILE = CLAUDE_SETTINGS_DIR / "token.json"

HELPER_WIN = CLAUDE_SETTINGS_DIR / "get-token.cmd"
HELPER_SH = CLAUDE_SETTINGS_DIR / "get-token.sh"
LOCAL_AGENT_WIN = CLAUDE_SETTINGS_DIR / "claude-agent.exe"
LOCAL_AGENT_NIX = CLAUDE_SETTINGS_DIR / "claude-agent"

# ── External service URLs ───────────────────────────────────────────────────
AUTH_SERVER_URL: str = os.environ.get("AUTH_SERVER_URL", "https://my-auth-server.company.com")
INSTALL_SERVER_URL: str = os.environ.get("INSTALL_SERVER_URL", "https://my-server.com/claude-code")
LLM_GATEWAY_URL: str = os.environ.get("LLM_GATEWAY_URL", "https://my-llm-gateway.company.com")

# ── JWT settings ────────────────────────────────────────────────────────────
INTERNAL_JWT_SECRET: str = os.environ.get("INTERNAL_JWT_SECRET", "CHANGE_ME_INTERNAL_JWT_SECRET")
INTERNAL_JWT_ALG: str = "HS256"
INTERNAL_JWT_TTL_HOURS: int = int(os.environ.get("INTERNAL_JWT_TTL_HOURS", "3"))
INTERNAL_REFRESH_TTL_DAYS: int = int(os.environ.get("INTERNAL_REFRESH_TTL_DAYS", "30"))

JWT_REFRESH_MARGIN_SECONDS: int = 300
JWT_TTL_SECONDS: int = 10800

# ── Entra ID (Azure AD) ────────────────────────────────────────────────────
ENTRA_TENANT_ID: str = os.environ.get("ENTRA_TENANT_ID", "")
ENTRA_CLIENT_ID: str = os.environ.get("ENTRA_CLIENT_ID", "")
ENTRA_SCOPES: list[str] = os.environ.get("ENTRA_SCOPES", "openid profile email").split()
ENTRA_AUTHORITY: str = (
    os.environ.get("ENTRA_AUTHORITY") or f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"
)

# ── Bedrock ─────────────────────────────────────────────────────────────────
BEDROCK_REGION: str = os.environ.get("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID: str = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v1:0"
)
ANTHROPIC_VERSION: str = os.environ.get("ANTHROPIC_VERSION", "bedrock-2023-05-31")

# ── Managed settings (shared across all platform installers) ────────────────
MANAGED_SETTINGS_SCHEMA = "https://json.schemastore.org/claude-code-settings.json"


def build_managed_settings_data(llm_gateway_url: str | None = None) -> dict:
    """Return the managed-settings dict used by every platform installer."""
    gateway = llm_gateway_url or LLM_GATEWAY_URL
    return {
        "$schema": MANAGED_SETTINGS_SCHEMA,
        "env": {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1",
            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "10800000",
            "ANTHROPIC_BEDROCK_BASE_URL": gateway,
        },
    }


# ── OpenID / JWKS cache TTL ────────────────────────────────────────────────
OPENID_CACHE_TTL_SECONDS: int = int(os.environ.get("OPENID_CACHE_TTL_SECONDS", "3600"))
