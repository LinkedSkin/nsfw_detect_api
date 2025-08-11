#!/usr/bin/env python3
import argparse, asyncio, time, os
import httpx

async def worker(idx, client, url, img_bytes, n, api_key=None):
    ok = 0; r429 = 0; other = 0
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    for _ in range(n):
        files = {"file": ("upload.jpg", img_bytes, "image/jpeg")}
        try:
            r = await client.post(url, headers=headers, files=files, timeout=60)
            if r.status_code == 200:
                ok += 1
            elif r.status_code == 429:
                r429 += 1
            else:
                other += 1
        except Exception:
            other += 1
    return ok, r429, other

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--img", required=True)
    p.add_argument("--concurrency", "-c", type=int, default=10)
    p.add_argument("--requests", "-n", type=int, default=100, help="total requests")
    p.add_argument("--api-key", default=None)
    args = p.parse_args()

    img = open(args.img, "rb").read()
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
        results = await asyncio.gather(*tasks)
        dt = time.perf_counter() - t0

    ok = sum(r[0] for r in results)
    r429 = sum(r[1] for r in results)
    other = sum(r[2] for r in results)
    qps = args.requests / dt if dt > 0 else 0.0
    print(f"done in {dt:.2f}s  qps={qps:.2f}  200={ok}  429={r429}  other={other}")

if __name__ == "__main__":
    asyncio.run(main())