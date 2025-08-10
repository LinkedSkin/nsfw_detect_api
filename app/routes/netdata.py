from __future__ import annotations

import os
from typing import Dict

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, Request, Response, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
import httpx
import re
from .auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# Base URL where Netdata is listening locally
NETDATA_BASE = os.getenv("NETDATA_BASE", "http://127.0.0.1:19999")

# Additional monitoring configuration
PUSHCUT_URL = os.getenv("PUSHCUT_URL", "")
NETDATA_MONITOR = os.getenv("NETDATA_MONITOR", "0") == "1"
NETDATA_POLL_SEC = int(os.getenv("NETDATA_POLL_SEC", "5"))
STRESS_CPU_PCT = float(os.getenv("STRESS_CPU_PCT", "85"))
STRESS_MEM_PCT = float(os.getenv("STRESS_MEM_PCT", "90"))
STRESS_LOAD_MULT = float(os.getenv("STRESS_LOAD_MULT", "1.5"))
STRESS_SUSTAIN_SECS = int(os.getenv("STRESS_SUSTAIN_SECS", "120"))

# Hop-by-hop headers must not be forwarded by proxies per RFC 7230 §6.1
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

CSS = '<link rel="stylesheet" href="https://unpkg.com/mvp.css" />'
META = '<meta name="robots" content="noindex, nofollow">'


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
  <head>
    <title>{title}</title>
    {META}
    {CSS}
    <style>html,body,iframe{{height:100%;width:100%;margin:0;border:0}}</style>
  </head>
  <body>
    {body}
  </body>
</html>"""


@router.get("/netdata", response_class=HTMLResponse)
async def netdata_home():
    # Load Netdata's index via our proxy so we can rewrite paths; fallback to a minimal link
    try:
        # This will trigger the generic proxy below with path="index.html"
        return await netdata_asset(Request({"type": "http"}), path="index.html")  # type: ignore[arg-type]
    except Exception:
        body = '<iframe src="/netdata/index.html"></iframe>'
        return _page("Netdata", body)


@router.api_route("/netdata/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def netdata_api(request: Request, path: str):
    # Proxy Netdata API (e.g. /api/v1/info)
    url = f"{NETDATA_BASE}/api/{path}"
    return await _proxy(request, url)


@router.api_route("/netdata/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def netdata_asset(request: Request, path: str):
    # Proxy any non-API path directly to Netdata root
    upstream_path = path or "index.html"
    url = f"{NETDATA_BASE}/{upstream_path}"
    return await _proxy(request, url)


async def _proxy(request: Request, upstream_url: str) -> Response:
    # Forward method, querystring, and headers (minus hop-by-hop)
    fwd_headers: Dict[str, str] = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }

    # Avoid compressed upstream so we can safely rewrite HTML
    fwd_headers.pop("accept-encoding", None)

    # Some proxies depend on Host; Netdata doesn't, but it's fine to pass
    # fwd_headers.setdefault("Host", request.headers.get("host", ""))

    body = None
    if request.method not in ("GET", "HEAD"):
        body = await request.body()

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        upstream = await client.request(
            request.method,
            upstream_url,
            params=dict(request.query_params),
            headers=fwd_headers,
            content=body,
        )

    # Strip hop-by-hop headers from upstream response
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    ct = upstream.headers.get("content-type", "").lower()
    if "text/html" in ct and upstream.content:
        try:
            html = upstream.text
            # Inject a <base> so "/..." paths resolve under /netdata/
            if "<head" in html and "<base" not in html:
                html = re.sub(r"(<head[^>]*>)", r"\1\n  <base href=\"/netdata/\">", html, count=1, flags=re.IGNORECASE)
            # Rewrite absolute-root references to live under /netdata/
            html = html.replace("href=\"/", "href=\"/netdata/")
            html = html.replace("src=\"/", "src=\"/netdata/")
            html = html.replace("action=\"/", "action=\"/netdata/")
            # Update response headers since body length changed
            resp_headers.pop("content-length", None)
            return HTMLResponse(content=html, status_code=upstream.status_code, headers=resp_headers)
        except Exception:
            pass

    # If content-length exists, return a normal Response, else stream
    if "content-length" in {k.lower(): v for k, v in resp_headers.items()}:
        return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers)

    return StreamingResponse(iter([upstream.content]), status_code=upstream.status_code, headers=resp_headers)


# ---------------------------
# Background monitor (optional)
# ---------------------------
async def _fetch_json(http: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        r = await http.get(f"{NETDATA_BASE}{path}", params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
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
    # Fallback: best-effort summary if available
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
    except Exception:
        pass

async def monitor_loop() -> None:
    if not (NETDATA_MONITOR and PUSHCUT_URL):
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
            except Exception:
                # swallow and continue
                continue


def mount_monitor(app) -> None:
    """Attach the background monitor to a FastAPI app on startup.

    Will only start if NETDATA_MONITOR=1 and PUSHCUT_URL is set.
    """
    @app.on_event("startup")
    async def _start_monitor():  # type: ignore[unused-variable]
        if NETDATA_MONITOR and PUSHCUT_URL:
            asyncio.create_task(monitor_loop())