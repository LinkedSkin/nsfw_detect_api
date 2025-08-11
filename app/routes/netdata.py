import os
import asyncio
import time
import logging
from typing import Any, Dict, Optional
try:
    import fcntl
except Exception:
    fcntl = None

import httpx

# --------------------------------------------------------------------------------------
# Logging / diagnostics
# --------------------------------------------------------------------------------------
logger = logging.getLogger("netdata_proxy")
if not logger.handlers:
    # Use DEBUG by setting NETDATA_DEBUG=1 in .env
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
                continue

def mount_monitor(app) -> None:
    """Attach the background monitor to a FastAPI app on startup.

    Will only start if NETDATA_MONITOR=1 and PUSHCUT_URL is set.
    """
    @app.on_event("startup")
    async def _start_monitor():  # type: ignore[unused-variable]
        if NETDATA_MONITOR and PUSHCUT_URL:
            logger.info("[monitor] starting background monitor loop")
            asyncio.create_task(monitor_loop())