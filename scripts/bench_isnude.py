#!/usr/bin/env python3
import argparse
import asyncio
import time
from collections import Counter
import httpx

async def worker(idx: int, client: httpx.AsyncClient, url: str, img_bytes: bytes, n: int, api_key: str | None = None) -> Counter:
    counts: Counter[str] = Counter()
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    files = {"file": ("upload.jpg", img_bytes, "image/jpeg")}

    for _ in range(n):
        try:
            r = await client.post(url, headers=headers, files=files, timeout=60)
            counts[str(r.status_code)] += 1
        except Exception:
            counts["EXC"] += 1  # network/timeout/other client exceptions
    return counts

async def main() -> None:
    p = argparse.ArgumentParser(description="Concurrent bench for /isnude (multipart file upload)")
    p.add_argument("--url", required=True, help="Endpoint URL (e.g. https://host/api/isnude)")
    p.add_argument("--img", required=True, help="Path to image file to upload")
    p.add_argument("--concurrency", "-c", type=int, default=10, help="Concurrent workers")
    p.add_argument("--requests", "-n", type=int, default=100, help="Total requests across all workers")
    p.add_argument("--api-key", default=None, help="Optional API key header value")
    args = p.parse_args()

    with open(args.img, "rb") as f:
        img = f.read()

    per = max(1, args.requests // args.concurrency)
    extra = args.requests - per * args.concurrency

    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)

    async with httpx.AsyncClient(http2=True, limits=limits, timeout=60.0, verify=True) as client:
        t0 = time.perf_counter()
        tasks = []
        for i in range(args.concurrency):
            count = per + (1 if i < extra else 0)
            if count:
                tasks.append(worker(i, client, args.url, img, count, args.api_key))
        counters = await asyncio.gather(*tasks)
        dt = time.perf_counter() - t0

    total = Counter()
    for c in counters:
        total.update(c)

    total_requests = sum(total.values())
    qps = (total_requests / dt) if dt > 0 else 0.0
    print(f"done in {dt:.2f}s  qps={qps:.2f}  total={total_requests}")

    # Print histogram of all codes (numeric first, then EXC)
    numeric = sorted((k for k in total if k.isdigit()), key=lambda x: int(x))
    nonnum = sorted([k for k in total if not k.isdigit() and k != "EXC"]) + (["EXC"] if "EXC" in total else [])
    for k in numeric + nonnum:
        print(f"{k} -> {total[k]}")

if __name__ == "__main__":
    asyncio.run(main())