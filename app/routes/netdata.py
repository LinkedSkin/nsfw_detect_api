import os
import asyncio
import time
import logging
import contextlib
from typing import Any, Dict, Optional

try:
    import fcntl  # POSIX-only; used for cross-process leader lock
except Exception:  # pragma: no cover
    fcntl = None

import httpx

# Try to expose a minimal FastAPI router (optional; guarded)
try:  # Prevent hard import failures if FastAPI/auth aren’t available at import time
    from fastapi import APIRouter, Depends
    try:
        from .auth import require_admin  # adjust if module path differs
        router = APIRouter(dependencies=[Depends(require_admin)])
    except Exception:  # fallback: public router (unused, but keeps imports stable)
        router = APIRouter()
except Exception:  # if FastAPI is not importable in this context
    router = None  # type: ignore

# --------------------------------------------------------------------------------------
# Logging / diagnostics
# --------------------------------------------------------------------------------------
logger = logging.getLogger("netdata_monitor")
if not logger.handlers:
    logger.setLevel(logging.DEBUG if os.getenv("NETDATA_DEBUG", "0") == "1" else logging.INFO)

# --------------------------------------------------------------------------------------
# Configuration (with debug dump at import time)
# --------------------------------------------------------------------------------------
NETDATA_BASE = os.getenv("NETDATA_BASE", "http://127.0.0.1:19999").rstrip("/")
PUSHCUT_URL = os.getenv("PUSHCUT_URL", "")
NETDATA_MONITOR = os.getenv("NETDATA_MONITOR", "0") == "1"
NETDATA_POLL_SEC = int(os.getenv("NETDATA_POLL_SEC", "5"))
STRESS_CPU_PCT = float(os.getenv("STRESS_CPU_PCT", "85"))
STRESS_MEM_PCT = float(os.getenv("STRESS_MEM_PCT", "90"))
STRESS_LOAD_MULT = float(os.getenv("STRESS_LOAD_MULT", "1.5"))
STRESS_SUSTAIN_SECS = int(os.getenv("STRESS_SUSTAIN_SECS", "120"))

logger.info(
    "[NETDATA] base=%s monitor=%s poll=%ss cpu>=%.0f mem>=%.0f load*%.2f",
    NETDATA_BASE, NETDATA_MONITOR, NETDATA_POLL_SEC, STRESS_CPU_PCT, STRESS_MEM_PCT, STRESS_LOAD_MULT
)

# --------------------------------------------------------------------------------------
# Single-leader control for the monitor (avoid duplicate pushes across workers)
# --------------------------------------------------------------------------------------
_monitor_task: Optional[asyncio.Task] = None
_monitor_lock_fd: Optional[int] = None
_LEADER_LOCK_PATH = "/tmp/netdata_monitor.lock"


def _try_acquire_leader_lock(path: str = _LEADER_LOCK_PATH) -> bool:
    """Try to become the single monitor leader using an exclusive flock.
    Returns True if this process acquired the lock, False otherwise.
    On non-POSIX (no fcntl), fall back to per-process (return True).
    """
    global _monitor_lock_fd
    if fcntl is None:
        # Non-POSIX: best-effort single instance per process
        logger.info("[monitor] fcntl not available; proceeding without cross-process lock")
        return True
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _monitor_lock_fd = fd
        logger.info("[monitor] acquired leader lock at %s", path)
        return True
    except Exception:
        logger.info("[monitor] another process holds the leader lock at %s; skipping here", path)
        return False

# --------------------------------------------------------------------------------------
# Background monitor (optional)
# --------------------------------------------------------------------------------------
async def _fetch_json(http: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        r = await http.get(f"{NETDATA_BASE}{path}", params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug("[monitor] fetch %s failed: %s", path, e)
        return None
    return None

async def _get_cpu_pct(http: httpx.AsyncClient) -> Optional[float]:
    data = await _fetch_json(http, "/api/v1/data", {
        "chart": "system.cpu",
        "format": "json",
        "after": -1,
        "points": 1,
        "group": "average",
    })
    if not data:
        return None
    dims = data.get("labels", [])
    rows = data.get("data", [])
    if not dims or not rows or "idle" not in dims:
        return None
    last = rows[-1]
    values = {dims[i]: last[i] for i in range(1, min(len(dims), len(last))) if isinstance(last[i], (int, float))}
    idle = float(values.get("idle", 0.0))
    return max(0.0, 100.0 - idle)

async def _get_load1(http: httpx.AsyncClient) -> Optional[float]:
    data = await _fetch_json(http, "/api/v1/data", {
        "chart": "system.load",
        "format": "json",
        "after": -1,
        "points": 1,
        "group": "average",
    })
    if not data:
        return None
    dims = data.get("labels", [])
    rows = data.get("data", [])
    if not dims or not rows:
        return None
    last = rows[-1]
    values = {dims[i]: last[i] for i in range(1, min(len(dims), len(last))) if isinstance(last[i], (int, float))}
    return float(values.get("load1", 0.0))

async def _get_mem_pct(http: httpx.AsyncClient) -> Optional[float]:
    data = await _fetch_json(http, "/api/v1/data", {
        "chart": "system.ram",
        "format": "json",
        "after": -1,
        "points": 1,
        "group": "average",
    })
    if data:
        dims = data.get("labels", [])
        rows = data.get("data", [])
        if dims and rows:
            last = rows[-1]
            values = {dims[i]: last[i] for i in range(1, min(len(dims), len(last))) if isinstance(last[i], (int, float))}
            used = float(values.get("used", 0.0))
            free = float(values.get("free", 0.0))
            total = used + free
            if total > 0:
                return (used / total) * 100.0
    info = await _fetch_json(http, "/api/v1/info")
    if info:
        mem = info.get("memory") or {}
        total = float(mem.get("total", 0.0))
        used = float(mem.get("used", 0.0))
        if total > 0:
            return (used / total) * 100.0
    return None

async def _pushcut(http: httpx.AsyncClient, title: str, text: str) -> None:
    if not PUSHCUT_URL:
        return
    try:
        await http.post(PUSHCUT_URL, json={"title": title, "text": text}, timeout=10)
    except Exception as e:
        logger.debug("[pushcut] post failed: %s", e)

async def monitor_loop() -> None:
    if not (NETDATA_MONITOR and PUSHCUT_URL):
        logger.info("[monitor] disabled (NETDATA_MONITOR=%s, PUSHCUT_URL set=%s)", NETDATA_MONITOR, bool(PUSHCUT_URL))
        return
    hot_since: Optional[float] = None
    cool_down_until: float = 0.0
    async with httpx.AsyncClient() as http:
        while True:
            await asyncio.sleep(max(1, NETDATA_POLL_SEC))
            try:
                cpu = await _get_cpu_pct(http)
                mem = await _get_mem_pct(http)
                load1 = await _get_load1(http)
                cores = os.cpu_count() or 1

                hot = False
                parts = []
                if cpu is not None and cpu >= STRESS_CPU_PCT:
                    hot = True; parts.append(f"CPU {cpu:.0f}% ≥ {STRESS_CPU_PCT:.0f}%")
                if mem is not None and mem >= STRESS_MEM_PCT:
                    hot = True; parts.append(f"MEM {mem:.0f}% ≥ {STRESS_MEM_PCT:.0f}%")
                if load1 is not None and load1 >= cores * STRESS_LOAD_MULT:
                    hot = True; parts.append(f"LOAD1 {load1:.2f} ≥ {cores*STRESS_LOAD_MULT:.2f}")

                now = time.time()
                if not hot:
                    hot_since = None
                    continue
                if now < cool_down_until:
                    continue
                if hot_since is None:
                    hot_since = now
                    continue
                if (now - hot_since) >= STRESS_SUSTAIN_SECS:
                    await _pushcut(http, "Server under stress", ", ".join(parts) or "Thresholds exceeded")
                    cool_down_until = now + 300  # 5m cooldown
                    hot_since = now
            except Exception as e:
                logger.debug("[monitor] loop error: %s", e)
                await asyncio.sleep(1.0)
                continue


def mount_monitor(app) -> None:
    """Attach the background monitor to a FastAPI app on startup.

    Only one monitor loop will run across all worker processes via flock.
    """
    global _monitor_task, _monitor_lock_fd

    @app.on_event("startup")
    async def _start_monitor():  # type: ignore[unused-variable]
        global _monitor_task
        if not (NETDATA_MONITOR and PUSHCUT_URL):
            logger.info("[monitor] disabled (NETDATA_MONITOR=%s, PUSHCUT_URL set=%s)", NETDATA_MONITOR, bool(PUSHCUT_URL))
            return
        if _monitor_task and not _monitor_task.done():
            logger.debug("[monitor] already running in this process")
            return
        if _try_acquire_leader_lock():
            logger.info("[monitor] starting background monitor loop (leader)")
            _monitor_task = asyncio.create_task(monitor_loop())
        else:
            # Do not start the loop in this worker
            pass

    @app.on_event("shutdown")
    async def _stop_monitor():  # type: ignore[unused-variable]
        global _monitor_task, _monitor_lock_fd
        if _monitor_task and not _monitor_task.done():
            _monitor_task.cancel()
            with contextlib.suppress(Exception):
                await _monitor_task
            _monitor_task = None
        if _monitor_lock_fd is not None and fcntl is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(_monitor_lock_fd, fcntl.LOCK_UN)
                os.close(_monitor_lock_fd)
            _monitor_lock_fd = None