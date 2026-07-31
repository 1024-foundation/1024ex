#!/usr/bin/env python3
"""1024 Exchange public-API client — stdlib only, no dependencies.

Signing contract (see /skills/raw/00-quickstart/sign-requests.md):
    X-SIGNATURE = hex(HMAC_SHA256(secret, "{ts_ms}{METHOD}{path}{body}"))
`path` excludes the query string; `body` is signed byte-for-byte as sent —
this script sends your body argument verbatim, never re-serialised.

Env:
    API_1024_KEY     1024_<64-hex> API key (omit for public endpoints)
    API_1024_SECRET  64-hex secret, used as raw ASCII (never hex-decode)
    API_1024_BASE    override base URL (default mainnet; --testnet/--base= win)

Usage:
    api.py GET  /api/v1/system/time
    api.py GET  '/api/v1/perp/orders?market=BTC-USDC'
    api.py POST /api/v1/perp/orders '{"market":"BTC-USDC","side":"buy","type":"limit","price":"65000","size":"0.01","leverage":5}'
    api.py --testnet DELETE /api/v1/prediction/orders/cancel '{"marketId":"1998","orderId":"312001"}'
"""
import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.request

MAINNET = "https://api-mainnet.1024ex.com"
TESTNET = "https://api-testnet-stable.1024ex.com"


def sign(secret: str, ts_ms: int, method: str, path: str, body: str) -> str:
    payload = f"{ts_ms}{method.upper()}{path}{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        sys.stderr.write(__doc__)
        return 2
    method, path = args[0].upper(), args[1]
    body = args[2] if len(args) > 2 else ""

    # Explicit flags beat the env var — a stale API_1024_BASE must never
    # silently redirect a --testnet call to mainnet.
    base = os.environ.get("API_1024_BASE") or MAINNET
    if "--testnet" in flags:
        base = TESTNET
    for f in flags:
        if f.startswith("--base="):
            base = f.split("=", 1)[1]

    headers = {}
    if body:
        headers["Content-Type"] = "application/json"
    key = os.environ.get("API_1024_KEY")
    secret = os.environ.get("API_1024_SECRET")
    if key and secret:
        ts = int(time.time() * 1000)
        headers["X-API-KEY"] = key
        headers["X-TIMESTAMP"] = str(ts)
        headers["X-SIGNATURE"] = sign(secret, ts, method, path.split("?", 1)[0], body)

    req = urllib.request.Request(
        base + path, data=body.encode() if body else None, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as e:
        out, status = e.read().decode(), e.code
    except urllib.error.URLError as e:
        sys.stderr.write(f"transport error: {e.reason}\n")
        return 3
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if not 200 <= status < 300:
        sys.stderr.write(f"HTTP {status}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
