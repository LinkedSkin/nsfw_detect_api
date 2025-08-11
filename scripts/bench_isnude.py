#!/usr/bin/env python3
import argparse
import asyncio
import time
from collections import Counter, defaultdict
from typing import Optional
import httpx

EXC_KEY = "EXC"  # bucket key for generic exceptions

async def worker(
    idx: int,
    client: httpx.AsyncClient,
    url: str,
    img_bytes: bytes,
    n: int,
    api_key: Optional[str] = None,
    verbose: bool = False,
    exc_types: Optional[Counter] = None,
    exc_samples: Optional[dict] = None,
) -> Counter:
    counts: Counter[str] = Counter()
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    files = {"file": ("upload.jpg", img_bytes, "image/jpeg")}

    for _ in range(n):
        try:
            r = await client.post(url, headers=headers, files=files)
            counts[str(r.status_code)] += 1
        except Exception as e:
            counts[EXC_KEY] += 1
            if exc_types is not None:
                exc_types[type(e).__name__] += 1
            if verbose and exc_samples is not None and len(exc_samples) < 10:
                exc_samples.setdefault(type(e).__name__, str(e))
    return counts

async def main() -> None:
    p = argparse.ArgumentParser(description="Concurrent bench for multipart file upload endpoint (e.g. /api/isnude)")
    p.add_argument("--url", required=True, help="Endpoint URL")
    p.add_argument("--img", required=True, help="Path to image file to upload")
    p.add_argument("--concurrency", "-c", type=int, default=10, help="Concurrent workers")
    p.add_argument("--requests", "-n", type=int, default=100, help="Total requests across all workers")
    p.add_argument("--api-key", default=None, help="Optional API key header value")
    p.add_argument("--timeout", type=float, default=300.0, help="Per-request read/write timeout seconds (default: 300)")
    p.add_argument("--http2", dest="http2", action="store_true", help="Use HTTP/2 (default)")
    p.add_argument("--no-http2", dest="http2", action="store_false", help="Disable HTTP/2 and use HTTP/1.1")
    p.set_defaults(http2=True)
    p.add_argument("--insecure", action="store_true", help="Skip TLS verification")
    p.add_argument("--verbose", "-v", action="store_true", help="Print sample exception messages")
    args = p.parse_args()

    with open(args.img, "rb") as f:
        img = f.read()

    per = max(1, args.requests // args.concurrency)
    extra = args.requests - per * args.concurrency

    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(connect=30.0, read=args.timeout, write=args.timeout, pool=args.timeout)

    exc_types: Counter[str] = Counter()
    exc_samples: dict[str, str] = {}

    async with httpx.AsyncClient(
        http2=args.http2,
        limits=limits,
        timeout=timeout,
        verify=not args.insecure,
    ) as client:
        t0 = time.perf_counter()
        tasks = []
        for i in range(args.concurrency):
            count = per + (1 if i < extra else 0)
            if count:
                tasks.append(
                    worker(i, client, args.url, img, count, args.api_key, args.verbose, exc_types, exc_samples)
                )
        counters = await asyncio.gather(*tasks)
        dt = time.perf_counter() - t0

    total = Counter()
    for c in counters:
        total.update(c)

    total_requests = sum(total.values())
    qps = (total_requests / dt) if dt > 0 else 0.0
    print(f"done in {dt:.2f}s  qps={qps:.2f}  total={total_requests}")

    # Print histogram of all codes (numeric first), then exceptions
    numeric = sorted((k for k in total if k.isdigit()), key=lambda x: int(x))
    for k in numeric:
        print(f"{k} -> {total[k]}")
    if total.get(EXC_KEY):
        print(f"{EXC_KEY} -> {total[EXC_KEY]}")
        if exc_types:
            for name, cnt in exc_types.most_common():
                print(f"  {name} -> {cnt}")
        if args.verbose and exc_samples:
            print("Sample exception messages:")
            for name, msg in exc_samples.items():
                print(f"  [{name}] {msg}")

if __name__ == "__main__":
    asyncio.run(main())