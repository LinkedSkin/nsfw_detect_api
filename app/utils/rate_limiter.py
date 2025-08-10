

"""
Simple, in-memory rate limiting utilities for FastAPI.

Goal:
- If a **valid API token** is provided: use a **higher per-token** limit.
- Otherwise (public/anonymous): use a **lower per-IP** limit.

This module is process-local (perfect for a single Raspberry Pi / single Uvicorn worker).
If you scale horizontally, switch to a shared store (e.g., Redis) for counters.
"""
from __future__ import annotations

import os
import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, Tuple, Optional

from fastapi import HTTPException, Request, Header
from sqlalchemy import create_engine, text

# -----------------------
# Configuration (env vars)
# -----------------------
# Anonymous requests per window (by IP)
IP_LIMIT = int(os.getenv("RATE_LIMIT_IP_PER_MIN", "30"))
# Authenticated requests per window (by token)
TOKEN_LIMIT = int(os.getenv("RATE_LIMIT_TOKEN_PER_MIN", "300"))
# Window size in seconds
WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
# API tokens DB URL (must match admin UI)
TOKENS_DB_URL = os.getenv("TOKENS_DB_URL", "sqlite:///./api_tokens.db")

# Use a deque per key for O(1) appends and amortized O(1) cleanup
_Buckets: Dict[Tuple[str, str], Deque[float]] = {}
_LOCK = Lock()
_tokens_engine = create_engine(TOKENS_DB_URL, connect_args={"check_same_thread": False})


def _consume(key: Tuple[str, str], limit: int, window: int) -> None:
    """Consume one request from the bucket for `key`.

    Raises HTTPException(429) if limit exceeded.
    """
    now = time.time()
    cutoff = now - window
    with _LOCK:
        bucket = _Buckets.get(key)
        if bucket is None:
            bucket = deque()
            _Buckets[key] = bucket
        # drop timestamps older than window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        bucket.append(now)


def _extract_token(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return None


def _is_valid_token(token: str) -> bool:
    try:
        with _tokens_engine.connect() as conn:
            row = conn.execute(text("SELECT active FROM api_tokens WHERE token = :t"), {"t": token}).first()
    except Exception:
        # If the token DB is unavailable, treat as anonymous rather than 500
        return False
    if not row:
        return False
    return bool(row[0])


# -----------------------
# Dependencies
# -----------------------
async def limit_token_or_ip(
    request: Request,
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
) -> None:
    """Conditional limiter for public endpoints.

    - If a valid API token is present → apply TOKEN_LIMIT per token.
    - Otherwise → apply IP_LIMIT per IP.
    """
    token = _extract_token(x_api_key, authorization)
    if token and _is_valid_token(token):
        _consume(("tok", token), TOKEN_LIMIT, WINDOW)
        return
    # Anonymous path: limit by IP
    ip = request.client.host if request.client else "unknown"
    _consume(("ip", ip), IP_LIMIT, WINDOW)


# Optional strict helpers if you want to use them elsewhere
async def limit_by_ip(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _consume(("ip", ip), IP_LIMIT, WINDOW)


async def limit_by_token(
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
) -> None:
    token = _extract_token(x_api_key, authorization)
    if not token or not _is_valid_token(token):
        raise HTTPException(status_code=401, detail="Valid API token required")
    _consume(("tok", token), TOKEN_LIMIT, WINDOW)