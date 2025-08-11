"""
Rate limiting utilities for FastAPI using the `limits` library.

Goal:
- If a **valid API token** is provided: use a **higher per-token** limit.
- Otherwise (public/anonymous): use a **lower per-IP** limit.

This module is process-local (good for a single Raspberry Pi / single Uvicorn worker).
If you scale across multiple processes or hosts, switch to a shared store (e.g., Redis)
by replacing MemoryStorage with RedisStorage.
"""
import os
from typing import Optional

from fastapi import HTTPException, Request, Header
from sqlalchemy import create_engine, text

# limits imports
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

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

# Build rate strings consumable by `limits.parse` (e.g., "5/10 seconds")
IP_RATE = parse(f"{IP_LIMIT}/{WINDOW} seconds")
TOKEN_RATE = parse(f"{TOKEN_LIMIT}/{WINDOW} seconds")

# Storage/strategy: in-memory moving window (per-process)
_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)

_tokens_engine = create_engine(TOKENS_DB_URL, connect_args={"check_same_thread": False})


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


def _hit_or_429(rate_item, key: str) -> None:
    """Consume one request for `key` against `rate_item`; raise 429 if exceeded."""
    # `hit` returns True when within the limit, False when exceeded
    allowed = _limiter.hit(rate_item, key)
    if not allowed:
        # Optionally you could compute retry-after via `get_window_stats` if desired
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


# -----------------------
# Dependencies
# -----------------------
async def limit_token_or_ip(
    request: Request,
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
) -> None:
    """Conditional limiter for public endpoints.

    - If a valid API token is present → apply TOKEN_RATE per token.
    - Otherwise → apply IP_RATE per IP.
    """
    token = _extract_token(x_api_key, authorization)
    if token and _is_valid_token(token):
        _hit_or_429(TOKEN_RATE, f"tok:{token}")
        return
    # Anonymous path: limit by IP
    ip = request.client.host if request.client else "unknown"
    _hit_or_429(IP_RATE, f"ip:{ip}")


async def limit_by_ip(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _hit_or_429(IP_RATE, f"ip:{ip}")


async def limit_by_token(
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
) -> None:
    token = _extract_token(x_api_key, authorization)
    if not token or not _is_valid_token(token):
        raise HTTPException(status_code=401, detail="Valid API token required")
    _hit_or_429(TOKEN_RATE, f"tok:{token}")