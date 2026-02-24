"""LLM Gateway — proxy internal JWT-authenticated requests to Amazon Bedrock.

Endpoints:
- ``POST /invoke`` — raw Bedrock model invocation.
- ``POST /invoke-with-response-stream`` — streaming (not yet implemented).
- ``POST /v1/chat/completions`` — OpenAI-compatible chat completions via Bedrock.
- ``GET /health`` — health check.
"""

from __future__ import annotations

import json
from typing import List, Optional

import boto3
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from claude_code_internal.config import (
    ANTHROPIC_VERSION,
    BEDROCK_MODEL_ID,
    BEDROCK_REGION,
    INTERNAL_JWT_ALG,
    INTERNAL_JWT_SECRET,
)
from claude_code_internal.logging_config import get_logger

logger = get_logger(__name__)

# ── Pydantic models ────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Incoming chat completion request."""

    messages: List[ChatMessage]


class ChatChoice(BaseModel):
    """A single completion choice."""

    index: int
    message: ChatMessage


class ChatResponse(BaseModel):
    """Response with one or more chat completion choices."""

    choices: List[ChatChoice]


# ── Bedrock client & app ───────────────────────────────────────────────────

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
app = FastAPI(title="Claude Code LLM Gateway")

# ── Auth dependency ─────────────────────────────────────────────────────────


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> str:
    """Extract the bearer or API key token from headers."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    if x_api_key:
        return x_api_key
    raise HTTPException(status_code=401, detail="Missing auth (Bearer or X-Api-Key)")


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> dict:
    """Validate the internal JWT and return its payload."""
    if INTERNAL_JWT_SECRET in ("", "CHANGE_ME_INTERNAL_JWT_SECRET"):
        raise HTTPException(
            status_code=500, detail="Gateway is not configured (INTERNAL_JWT_SECRET)"
        )
    token = _extract_token(authorization, x_api_key)
    try:
        payload = jwt.decode(token, INTERNAL_JWT_SECRET, algorithms=[INTERNAL_JWT_ALG])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail=f"Invalid internal token: {exc}"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token is not an access token")
    return payload


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/invoke")
async def bedrock_invoke(request: Request, user: dict = Depends(get_current_user)):
    """Forward a raw invoke request to Bedrock."""
    raw = await request.body()
    model_id = request.query_params.get("modelId") or BEDROCK_MODEL_ID
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing modelId")

    logger.info("Invoking Bedrock model=%s for user=%s", model_id, user.get("sub"))
    resp = bedrock.invoke_model(
        modelId=model_id,
        body=raw,
        contentType=request.headers.get("content-type", "application/json"),
        accept=request.headers.get("accept", "application/json"),
    )
    data = resp["body"].read()
    return Response(content=data, media_type="application/json")


@app.post("/invoke-with-response-stream")
async def bedrock_invoke_stream(
    request: Request, user: dict = Depends(get_current_user)
):
    """Stream a Bedrock model response (not yet implemented)."""
    raise HTTPException(status_code=501, detail="Streaming not implemented")


@app.post("/v1/chat/completions", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    """OpenAI-compatible chat completions routed through Bedrock."""
    system_prompt = ""
    messages_payload: list[dict] = []
    for m in req.messages:
        if m.role == "system":
            system_prompt += m.content + "\n"
        else:
            messages_payload.append(
                {"role": m.role, "content": [{"type": "text", "text": m.content}]}
            )

    body: dict = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": 4096,
        "messages": messages_payload,
    }
    if system_prompt:
        body["system"] = system_prompt

    logger.info("Chat completion request for user=%s", user.get("sub"))
    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())

    # ── Parse Bedrock response ──────────────────────────────────────────
    text = ""
    content_list = None
    if isinstance(payload, dict):
        if isinstance(payload.get("content"), list):
            content_list = payload["content"]
        elif (
            isinstance(payload.get("output"), dict)
            and isinstance(payload["output"].get("message"), dict)
            and isinstance(payload["output"]["message"].get("content"), list)
        ):
            content_list = payload["output"]["message"]["content"]

    if isinstance(content_list, list):
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                text += item.get("text", "")
    if not text:
        logger.warning("Bedrock response could not be parsed: %s", type(payload))
        text = "Bedrock response received but could not be parsed (unexpected format)."

    return ChatResponse(
        choices=[ChatChoice(index=0, message=ChatMessage(role="assistant", content=text))]
    )
