"""
Rate limiting utilities for FastAPI using the `limits` library.

Behavior:
- If a **valid API token** is provided: apply a **higher per-token** limit.
- Otherwise (public/anonymous): apply a **lower per-IP** limit.

Fixes vs previous version:
- Storage is configurable via RATE_LIMIT_STORAGE_URL (default: memory://).
  Use redis for multi-worker: RATE_LIMIT_STORAGE_URL=redis://localhost:6379/0
- Rates are computed from env at **call time** so .env changes (or late load) apply.
"""

import os
import time
import json
import fcntl
import tempfile
from typing import Optional, Tuple

from fastapi import HTTPException, Request, Header
from sqlalchemy import create_engine, text

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter

# -----------------------
# Custom file-based rate limiter for multi-worker support
# -----------------------
class FileRateLimiter:
    """File-based rate limiter that works across multiple workers."""
    
    def __init__(self, filepath):
        self.filepath = filepath
    
    def is_allowed(self, key: str, limit: int, window: int) -> bool:
        """Check if request is allowed and record it."""
        now = time.time()
        cutoff = now - window
        
        # Use file locking for thread/process safety
        try:
            with open(self.filepath, 'r+') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    data = {}
                
                # Clean old entries and count recent ones
                if key not in data:
                    data[key] = []
                
                # Remove old timestamps
                data[key] = [ts for ts in data[key] if ts > cutoff]
                
                # Check if we're within limit
                if len(data[key]) >= limit:
                    return False
                
                # Record this request
                data[key].append(now)
                
                # Write back
                f.seek(0)
                json.dump(data, f)
                f.truncate()
                
                return True
                
        except FileNotFoundError:
            # Create file if it doesn't exist
            with open(self.filepath, 'w') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                json.dump({key: [now]}, f)
            return True

# Storage backend (configurable)
def _get_rate_limiter():
    """Get rate limiter. Use file-based for multi-worker support without external deps."""
    storage_url = os.getenv("RATE_LIMIT_STORAGE_URL")
    
    if storage_url and storage_url != "memory://":
        # Use limits library with specified storage
        storage = storage_from_string(storage_url)
        return MovingWindowRateLimiter(storage), False
    else:
        # Use custom file-based limiter
        rate_limit_file = os.path.join(tempfile.gettempdir(), "nsfw_api_rate_limits.json")
        return FileRateLimiter(rate_limit_file), True

_limiter, _is_file_limiter = _get_rate_limiter()

# API tokens DB URL (must match admin UI)
TOKENS_DB_URL = os.getenv("TOKENS_DB_URL", "sqlite:///./api_tokens.db")
_tokens_engine = create_engine(TOKENS_DB_URL, connect_args={"check_same_thread": False})


def _current_rates() -> Tuple[object, object]:
    """Read limits from env each call and return parsed rate objects.
    Env knobs:
      RATE_LIMIT_IP_PER_MIN (default 30)
      RATE_LIMIT_TOKEN_PER_MIN (default 300)
      RATE_LIMIT_WINDOW_SEC (default 60)
    """
    ip_limit = int(os.getenv("RATE_LIMIT_IP_PER_MIN", "30"))
    token_limit = int(os.getenv("RATE_LIMIT_TOKEN_PER_MIN", "300"))
    window = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))
    ip_rate = parse(f"{ip_limit}/{window} seconds")
    token_rate = parse(f"{token_limit}/{window} seconds")
    return ip_rate, token_rate


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
    if _is_file_limiter:
        # Extract limit and window from rate_item string representation
        rate_str = str(rate_item)  # e.g., "2 per 60 second"
        parts = rate_str.split()
        limit = int(parts[0])
        window = int(parts[2])
        
        if not _limiter.is_allowed(key, limit, window):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    else:
        # Use limits library limiter
        if not _limiter.hit(rate_item, key):
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

    - If a valid API token is present → apply TOKEN rate per token.
    - Otherwise → apply IP rate per IP.
    """
    ip_rate, token_rate = _current_rates()

    token = _extract_token(x_api_key, authorization)
    if token and _is_valid_token(token):
        _hit_or_429(token_rate, f"tok:{token}")
        return
    # Anonymous path: limit by IP
    ip = request.client.host if request.client else "unknown"
    _hit_or_429(ip_rate, f"ip:{ip}")


async def limit_by_ip(request: Request) -> None:
    ip_rate, _ = _current_rates()
    ip = request.client.host if request.client else "unknown"
    _hit_or_429(ip_rate, f"ip:{ip}")


async def limit_by_token(
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
) -> None:
    _, token_rate = _current_rates()
    token = _extract_token(x_api_key, authorization)
    if not token or not _is_valid_token(token):
        raise HTTPException(status_code=401, detail="Valid API token required")
    _hit_or_429(token_rate, f"tok:{token}")