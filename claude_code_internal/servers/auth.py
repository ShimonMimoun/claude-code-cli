"""Authentication server — Entra ID token validation and internal JWT issuance.

Provides two endpoints:
- ``POST /auth/verify`` — validate an Entra ID token and issue an internal JWT pair.
- ``POST /auth/refresh`` — refresh an internal JWT pair.
- ``GET /health`` — health check.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from claude_code_internal.config import (
    ENTRA_AUTHORITY,
    ENTRA_CLIENT_ID,
    ENTRA_TENANT_ID,
    INTERNAL_JWT_ALG,
    INTERNAL_JWT_SECRET,
    INTERNAL_JWT_TTL_HOURS,
    INTERNAL_REFRESH_TTL_DAYS,
    OPENID_CACHE_TTL_SECONDS,
)
from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)

# ── Pydantic models ────────────────────────────────────────────────────────


class VerifyRequest(BaseModel):
    """Incoming request to verify an Entra ID token."""

    id_token: str
    device_id: Optional[str] = None


class TokenResponse(BaseModel):
    """Internal JWT pair returned to the client."""

    access_token: str
    refresh_token: str
    expires_in: int


class RefreshRequest(BaseModel):
    """Incoming request to refresh an internal token."""

    refresh_token: str


# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Claude Code Auth Server")

# ── Cache with TTL ──────────────────────────────────────────────────────────

_openid_config_cache: Dict[str, Tuple[dict, float]] = {}
_jwks_cache: Dict[str, Tuple[dict, float]] = {}


def _is_cache_valid(cache: Dict[str, Tuple[dict, float]], key: str) -> bool:
    """Return ``True`` if the cache entry exists and has not expired."""
    if key not in cache:
        return False
    _, ts = cache[key]
    return (time.monotonic() - ts) < OPENID_CACHE_TTL_SECONDS


def _get_openid_config() -> dict:
    """Fetch and cache the OpenID Connect configuration."""
    if ENTRA_TENANT_ID in ("", "YOUR_TENANT_ID") or ENTRA_CLIENT_ID in (
        "",
        "YOUR_APP_REGISTRATION_CLIENT_ID",
    ):
        raise HTTPException(
            status_code=500,
            detail="Auth server is not configured (ENTRA_TENANT_ID/ENTRA_CLIENT_ID)",
        )
    if INTERNAL_JWT_SECRET in ("", "CHANGE_ME_INTERNAL_JWT_SECRET"):
        raise HTTPException(
            status_code=500,
            detail="Auth server is not configured (INTERNAL_JWT_SECRET)",
        )

    if _is_cache_valid(_openid_config_cache, "config"):
        return _openid_config_cache["config"][0]

    url = f"{ENTRA_AUTHORITY}/v2.0/.well-known/openid-configuration"
    logger.info("Fetching OpenID configuration from %s", url)
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    data = resp.json()
    _openid_config_cache["config"] = (data, time.monotonic())
    return data


def _get_jwks() -> dict:
    """Fetch and cache the JSON Web Key Set."""
    if _is_cache_valid(_jwks_cache, "jwks"):
        return _jwks_cache["jwks"][0]

    config = _get_openid_config()
    jwks_uri = config["jwks_uri"]
    logger.info("Fetching JWKS from %s", jwks_uri)
    resp = httpx.get(jwks_uri, timeout=5.0)
    resp.raise_for_status()
    data = resp.json()
    _jwks_cache["jwks"] = (data, time.monotonic())
    return data


# ── Token validation ───────────────────────────────────────────────────────


def _validate_entra_token(id_token: str) -> dict:
    """Validate an Entra ID token and return its claims."""
    jwks = _get_jwks()
    try:
        unverified_header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token header: {exc}") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="Missing kid in token header")

    key = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
            break
    if key is None:
        raise HTTPException(status_code=401, detail="Signing key not found for token")

    issuer_expected = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/v2.0"
    try:
        return jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=ENTRA_CLIENT_ID,
            issuer=issuer_expected,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Entra token: {exc}") from exc


# ── Internal JWT issuance ──────────────────────────────────────────────────


def _issue_internal_tokens(
    subject: str, device_id: Optional[str] = None
) -> TokenResponse:
    """Create a new internal access + refresh token pair."""
    now = datetime.now(timezone.utc)
    access_payload = {
        "sub": subject,
        "device_id": device_id,
        "type": "access",
        "exp": now + timedelta(hours=INTERNAL_JWT_TTL_HOURS),
    }
    refresh_payload = {
        "sub": subject,
        "type": "refresh",
        "exp": now + timedelta(days=INTERNAL_REFRESH_TTL_DAYS),
    }
    access_token = jwt.encode(access_payload, INTERNAL_JWT_SECRET, algorithm=INTERNAL_JWT_ALG)
    refresh_token = jwt.encode(refresh_payload, INTERNAL_JWT_SECRET, algorithm=INTERNAL_JWT_ALG)
    logger.info("Issued internal tokens for subject=%s", subject)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=INTERNAL_JWT_TTL_HOURS * 3600,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/auth/verify", response_model=TokenResponse)
def verify(req: VerifyRequest):
    """Validate an Entra ID token and issue internal JWT tokens."""
    claims = _validate_entra_token(req.id_token)
    subject = claims.get("oid") or claims.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Token has no subject")
    return _issue_internal_tokens(subject=subject, device_id=req.device_id)


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest):
    """Refresh an internal JWT pair."""
    try:
        payload = jwt.decode(
            req.refresh_token, INTERNAL_JWT_SECRET, algorithms=[INTERNAL_JWT_ALG]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail=f"Invalid refresh token: {exc}"
        ) from exc
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=400, detail="Token is not a refresh token")
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=400, detail="Refresh token has no subject")
    return _issue_internal_tokens(subject=subject)
