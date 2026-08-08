#!/usr/bin/env python3
"""1024 Exchange public-API client — stdlib only, no dependencies.

Signing contract (see /skills/raw/00-quickstart/sign-requests.md):
    X-SIGNATURE = hex(HMAC_SHA256(secret, "{ts_ms}{METHOD}{path}{body}"))
`path` excludes the query string; `body` is signed byte-for-byte as sent —
this script sends your body argument verbatim, never re-serialised.

Credentials (first match wins):
    1. env: API_1024_KEY (1024_<64-hex>) + API_1024_SECRET (64-hex, raw ASCII)
    2. file: ~/.1024ex/credentials.json, keyed by base URL — written by
       `connect`, which mints a key via OAuth wallet-link: it prints a login
       URL for the user to open and approve, polls until authorized, then stores
       key+secret on disk. The secret is never printed.
    API_1024_BASE overrides the base URL (default mainnet; --testnet/--base= win).

Usage:
    api.py connect [--label=YourAgentName] [--testnet]
    api.py GET  /api/v1/system/time
    api.py GET  '/api/v1/perp/orders?market=BTC-USDC'
    api.py POST /api/v1/perp/orders '{"market":"BTC-USDC","side":"buy","type":"limit","price":"65000","size":"0.01","leverage":5}'
    api.py --testnet DELETE /api/v1/prediction/orders/cancel '{"marketId":"1998","orderId":"312001"}'

`connect` exit codes: 0 authorized+saved, 4 still pending (re-run to resume
the same session), 1 error.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

MAINNET = "https://api-mainnet.1024ex.com"
TESTNET = "https://api-testnet-stable.1024ex.com"
CRED_DIR = os.path.expanduser("~/.1024ex")
CRED_FILE = os.path.join(CRED_DIR, "credentials.json")
PENDING_FILE = os.path.join(CRED_DIR, "pending.json")


def sign(secret: str, ts_ms: int, method: str, path: str, body: str) -> str:
    payload = f"{ts_ms}{method.upper()}{path}{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def request(base, method, path, body="", key=None, secret=None):
    """One HTTP round-trip. Returns (status, text); status 0 = transport error."""
    headers = {}
    if body:
        headers["Content-Type"] = "application/json"
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
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except urllib.error.URLError as e:
        return 0, f"transport error: {e.reason}"


def unwrap(text):
    """Parse a JSON body, unwrapping the {success,data,…} envelope if present."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj.get("data", obj) if isinstance(obj, dict) else obj


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_json(path, obj):
    os.makedirs(CRED_DIR, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def store_bundle(base, data, pending):
    # StatusResponse fields are snake_case on the wire; stay tolerant of both.
    def g(*names):
        for n in names:
            if data.get(n) is not None:
                return data[n]
        return None

    api_key, secret = g("api_key", "apiKey"), g("secret_key", "secretKey")
    if not api_key or not secret:
        sys.stderr.write("completed but no key in response — re-run connect\n")
        return 1
    creds = load_json(CRED_FILE)
    creds[base] = {
        "apiKey": api_key,
        "secretKey": secret,
        "accountId": g("account_id", "accountId"),
        "walletAddress": g("wallet_address", "walletAddress"),
        "keyLabel": g("key_label", "keyLabel"),
    }
    try:
        save_json(CRED_FILE, creds)
    except OSError as e:
        # Single-use delivery — surface the bundle rather than lose it.
        sys.stderr.write(
            f"cannot write {CRED_FILE} ({e}); persist these yourself:\n"
            f"export API_1024_KEY={api_key}\nexport API_1024_SECRET={secret}\n"
        )
        return 1
    pending.pop(base, None)
    save_json(PENDING_FILE, pending)
    perms = g("granted_permissions", "grantedPermissions") or {}
    sys.stdout.write(
        f"Authorized: account {creds[base]['accountId']} "
        f"wallet {creds[base]['walletAddress']}\n"
        f"key {api_key[:12]}… canTrade={perms.get('canTrade')} "
        f"saved to {CRED_FILE} (secret on disk only, never printed)\n"
    )
    status, text = request(
        base, "GET", "/api/v1/accounts/me/api-key/introspect", key=api_key, secret=secret
    )
    if status == 200:
        sys.stdout.write("introspect: OK — credential verified\n")
    else:
        sys.stdout.write(f"introspect HTTP {status} — key saved but unverified: {text}\n")
    return 0


def connect(base, flags):
    label = "1024ex skill"
    for f in flags:
        if f.startswith("--label="):
            label = f.split("=", 1)[1]

    pending = load_json(PENDING_FILE)
    sess = pending.get(base)
    now_ms = int(time.time() * 1000)
    if not sess or sess.get("expiresAt", 0) < now_ms + 10_000:
        body = json.dumps(
            {
                "applicationName": label,
                "partnerTag": "1024ex-skill",
                "ttlSeconds": 900,
                "permissions": {"canRead": True, "canTrade": True},
            }
        )
        status, text = request(base, "POST", "/api/v1/oauth/sessions", body)
        if status == 429:
            sys.stderr.write("rate-limited creating session — wait ~1 min, re-run connect\n")
            return 1
        data = unwrap(text) or {}
        sess = {k: data.get(k) for k in ("sessionId", "statusUrl", "loginUrl", "expiresAt")}
        if not 200 <= status < 300 or not all(sess.values()):
            sys.stderr.write(f"{text}\nHTTP {status}\n")
            return 1
        # Testnet PA currently advertises the mainnet /connect origin
        # (OAUTH_WALLET_LINK_PUBLIC_ORIGIN unset there); the mainnet page
        # cannot complete a testnet session, so repoint. No-op once fixed.
        if base == TESTNET:
            sess["loginUrl"] = sess["loginUrl"].replace(
                "https://www.1024ex.com/", "https://testnet.1024ex.com/", 1
            )
        pending[base] = sess
        save_json(PENDING_FILE, pending)

    mins = max(0, (sess["expiresAt"] - now_ms) // 60_000)
    sys.stdout.write(
        "Ask the user to open this link and approve "
        f"(valid ~{mins} min):\n\n  {sess['loginUrl']}\n\nWaiting for authorization"
    )
    sys.stdout.flush()

    deadline = min(sess["expiresAt"] / 1000, time.time() + 100)
    while time.time() < deadline:
        status, text = request(base, "GET", sess["statusUrl"])
        if status == 401:
            pending.pop(base, None)
            save_json(PENDING_FILE, pending)
            sys.stdout.write("\nsession expired or already consumed — re-run connect\n")
            return 1
        data = unwrap(text) or {}
        if data.get("status") == "completed":
            sys.stdout.write("\n")
            return store_bundle(base, data, pending)
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(3)
    sys.stdout.write("\nstill pending — re-run connect to resume this session (same link)\n")
    return 4


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]

    # Explicit flags beat the env var — a stale API_1024_BASE must never
    # silently redirect a --testnet call to mainnet.
    base = os.environ.get("API_1024_BASE") or MAINNET
    if "--testnet" in flags:
        base = TESTNET
    for f in flags:
        if f.startswith("--base="):
            base = f.split("=", 1)[1]

    if args and args[0].lower() == "connect":
        return connect(base, flags)

    if len(args) < 2:
        sys.stderr.write(__doc__)
        return 2
    method, path = args[0].upper(), args[1]
    body = args[2] if len(args) > 2 else ""

    key = os.environ.get("API_1024_KEY")
    secret = os.environ.get("API_1024_SECRET")
    if not (key and secret):
        cred = load_json(CRED_FILE).get(base) or {}
        key, secret = cred.get("apiKey"), cred.get("secretKey")

    status, out = request(base, method, path, body, key=key, secret=secret)
    if status == 0:
        sys.stderr.write(out + "\n")
        return 3
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if not 200 <= status < 300:
        sys.stderr.write(f"HTTP {status}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
