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
    api.py status  [--testnet]                    am I connected? five lines you can read out
    api.py connect [--label=Name] [--testnet] [--force] [--wait=SECONDS]
    api.py disconnect [--testnet]                 revoke this key on 1024 and forget it locally
    api.py deposit [--amount=100] [--chain=base] [--wait[=SECONDS]]
                                                  a funding link made for this account;
                                                  --wait watches until the money is credited
    api.py deposit --token=USDT --chain=TRC20 --amount=300 [--qr=dark|light|none]
                                                  a deposit address made for this account,
                                                  printed ready to relay: the address, a QR,
                                                  the facts, a link to verify it. --chain is
                                                  the user's words (1024 resolves them);
                                                  --amount is required. It never waits
    api.py deposit --status=REQUEST_ID [--qr=dark|light|none]
                                                  where that deposit is; --qr shows the
                                                  address again, while nothing is sent yet
    api.py deposit --status=REQUEST_ID --wait[=SECONDS] [--finish-sent]
                                                  follows it into 1024 (540 s by default,
                                                  requests included; the first line is the
                                                  resume command). It stops at `delivered`
                                                  with the one-tap finish link, unless
                                                  --finish-sent says that link is handed over
    api.py deposit --routes                       the chains, tokens and minimum it takes
    api.py GET  /api/v1/system/time
    api.py GET  '/api/v1/perp/orders?market=BTC-USDC'
    api.py POST /api/v1/perp/orders '{"market":"BTC-USDC","side":"buy","type":"limit","price":"65000","size":"0.01","leverage":5}'
    api.py --testnet DELETE /api/v1/prediction/orders/cancel '{"marketId":"1998","orderId":"312001"}'

Exit codes:
    status      0 connected · 2 credential rejected (revoked / rotated) · 3 not connected · 1 error
    connect     0 authorized+saved, or already connected · 4 still pending (re-run to resume
                the same link) · 1 error
    disconnect  0 revoked+forgotten · 3 nothing to disconnect · 1 error
    deposit     0 link printed (with --wait: credited) · 4 --wait ran out, still pending ·
                3 --wait without a connection · 2 bad --amount · 1 error
    deposit --token / --status / --routes (address mode)
                0 address or routes printed, or credited · 4 not in 1024 yet: relay
                any link it printed (at `delivered`, the one-tap finish link), then
                run the resume line it printed · 5 refunded, failed, or not this
                account's address · 2 a flag missing, or 1024 refused the input (its
                reason is printed) · 3 not connected, key refused, no answer, or the
                service is off (never loop a create: each run makes a new address) ·
                1 error. On a deployment without these endpoints (404/405) or with
                them off (503), a create or --routes prints the web link instead
                (link-mode codes); --status prints it and exits 1.
    requests    0 2xx · 1 non-2xx · 3 transport error
"""
import hashlib
import hmac
import http.client
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# scripts/qr.py sits next to this file under public/ on the web; never leave a
# __pycache__ beside it (and no stray bytecode in the user's skill folder).
sys.dont_write_bytecode = True

MAINNET = "https://api-mainnet.1024ex.com"
TESTNET = "https://api-testnet-stable.1024ex.com"
# The web app for each API host — where the user signs in, approves an
# agent, and sees every AI connected to the account.
WEB_ORIGIN = {MAINNET: "https://www.1024ex.com", TESTNET: "https://testnet.1024ex.com"}
NET_NAME = {MAINNET: "mainnet", TESTNET: "testnet"}
CRED_DIR = os.path.expanduser("~/.1024ex")
CRED_FILE = os.path.join(CRED_DIR, "credentials.json")
PENDING_FILE = os.path.join(CRED_DIR, "pending.json")
VERSION_CACHE = os.path.join(CRED_DIR, "skill-index-cache.json")
SKILL_INDEX_URL = "https://www.1024ex.com/.well-known/agent-skills/index.json"
SKILL_NAME = "1024ex"
INSTALL_CMD = "npx skills add 1024-foundation/1024ex"
INTROSPECT = "/api/v1/accounts/me/api-key/introspect"

# Which agent is running us — becomes the name the user sees on 1024's
# "Connected AIs" page. `--label` always wins; this is the fallback.
HOST_MARKERS = (
    ("CLAUDECODE", "Claude Code"),
    ("CLAUDE_CODE_ENTRYPOINT", "Claude Code"),
    ("CURSOR_TRACE_ID", "Cursor"),
    ("CODEX_SANDBOX", "Codex"),
    ("GEMINI_CLI", "Gemini CLI"),
    ("WINDSURF_SESSION_ID", "Windsurf"),
)


def sign(secret: str, ts_ms: int, method: str, path: str, body: str) -> str:
    payload = f"{ts_ms}{method.upper()}{path}{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def request(base, method, path, body="", key=None, secret=None, timeout=30):
    """One HTTP round-trip. Returns (status, text); status 0 = transport error.

    Never raises for network trouble: refused connections, DNS failures, a
    socket timeout mid-read and a truncated body all come back as status 0.
    """
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            return e.code, ""
    except urllib.error.URLError as e:
        return 0, f"transport error: {e.reason}"
    except (OSError, http.client.HTTPException, ValueError) as e:
        # socket.timeout while reading, connection reset, IncompleteRead, bad URL
        return 0, f"transport error: {e.__class__.__name__}: {e}"


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


def web_origin(base):
    return WEB_ORIGIN.get(base, base)


def net_name(base):
    return NET_NAME.get(base, base)


def resolve_credentials(base):
    """(key, secret, source) — source is 'env', 'file' or None."""
    key, secret = os.environ.get("API_1024_KEY"), os.environ.get("API_1024_SECRET")
    if key and secret:
        return key, secret, "env"
    cred = load_json(CRED_FILE).get(base) or {}
    key, secret = cred.get("apiKey"), cred.get("secretKey")
    return key, secret, ("file" if key and secret else None)


def detect_host_label():
    for var, name in HOST_MARKERS:
        if os.environ.get(var):
            return name
    term = os.environ.get("TERM_PROGRAM", "").lower()
    if "cursor" in term:
        return "Cursor"
    if "vscode" in term:
        return "VS Code"
    return None


def short(s, head=8, tail=4):
    s = s or ""
    return s if len(s) <= head + tail + 1 else f"{s[:head]}…{s[-tail:]}"


def fmt_ts(ms):
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ms) / 1000))
    except (TypeError, ValueError, OverflowError):
        return str(ms)


def local_skill_version():
    """`version:` from the SKILL.md next to this script (frontmatter only)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "SKILL.md")
    try:
        with open(path) as f:
            fences = 0
            for line in f:
                if line.strip() == "---":
                    fences += 1
                    if fences == 2:
                        break
                    continue
                if line.strip().startswith("version:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def latest_skill_version():
    """(version, reason) from the well-known index, cached for a day.

    reason: "ok" | "offline" (fetch failed) | "unavailable" (index carries no
    version). Only a real version is cached — a null must never be served
    for 24 h as if it were an answer, which is exactly what happened when the
    index predated the `version` field and the wording blamed the network.
    """
    cache = load_json(VERSION_CACHE)
    now = time.time()
    if cache.get("fetchedAt", 0) > now - 86_400 and cache.get("version"):
        return cache["version"], "ok"
    try:
        req = urllib.request.Request(SKILL_INDEX_URL, headers={"User-Agent": "1024ex-skill"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            idx = json.loads(resp.read().decode())
    except Exception:  # offline / blocked — never fail status over a version check
        return (cache.get("version") or None), "offline"
    version = None
    for entry in idx.get("skills", []) if isinstance(idx, dict) else []:
        if isinstance(entry, dict) and entry.get("name") == SKILL_NAME:
            version = entry.get("version")
    if version:
        try:
            save_json(VERSION_CACHE, {"fetchedAt": now, "version": version})
        except OSError:
            pass
    return version, ("ok" if version else "unavailable")


def _version_key(v):
    """'2026.09.11.10' → (2026, 9, 11, 10); None when it is not dotted integers."""
    try:
        return tuple(int(p) for p in str(v).strip().split("."))
    except ValueError:
        return None


def _is_newer(latest, local):
    # Compare as integer tuples: as strings '2026.09.11.10' < '2026.09.11.9',
    # so a tenth same-day release would never be offered as an update.
    lt, lc = _version_key(latest), _version_key(local)
    return lt > lc if (lt and lc) else latest > local


def version_line(out):
    local = local_skill_version()
    if not local:
        return
    latest, reason = latest_skill_version()
    if latest and _is_newer(latest, local):
        out(f"skill     v{local} · latest v{latest} — update: {INSTALL_CMD}\n")
    elif latest:
        out(f"skill     v{local} · latest v{latest} ✓\n")
    elif reason == "offline":
        out(f"skill     v{local} · latest unknown (could not reach {SKILL_INDEX_URL})\n")
    else:
        out(f"skill     v{local} · latest unknown (index carries no version yet)\n")


# ---------------------------------------------------------------------------
# status — the one call to make before anything that needs a key
# ---------------------------------------------------------------------------


def status(base, flags):
    out = sys.stdout.write
    net, origin = net_name(base), web_origin(base)
    key, secret, source = resolve_credentials(base)

    if not (key and secret):
        out(f"1024ex · {net} · not connected\n")
        out("No credentials (env API_1024_KEY / ~/.1024ex/credentials.json).\n")
        pend = load_json(PENDING_FILE).get(base) or {}
        now_ms = int(time.time() * 1000)
        if pend.get("expiresAt", 0) > now_ms + 10_000 and pend.get("loginUrl"):
            mins = (pend["expiresAt"] - now_ms) // 60_000
            out(
                "Authorization pending — the user has not approved yet. Same link, "
                f"valid ~{mins} min:\n\n  {pend['loginUrl']}\n\n"
                "Re-run `connect` to resume waiting; do not mint a new link.\n"
            )
        else:
            out(
                "Not connected yet: ask the user first, then run "
                '`python3 scripts/api.py connect --label="<your product name>"`.\n'
            )
        out(f"manage    {origin}/connect  — the user can see every connected AI there\n")
        version_line(out)
        return 3

    st, txt = request(base, "GET", INTROSPECT, key=key, secret=secret)
    if st == 0:
        sys.stderr.write(txt + "\n")
        return 1
    if st in (401, 403):
        out(f"1024ex · {net} · credential rejected (HTTP {st})\n")
        out(
            f"key       {key[:12]}… (from {source}) — revoked, rotated or expired; "
            "most likely revoked by the user on the web.\n"
        )
        out(
            "Say that plainly. Reconnect with `connect` if they want to; "
            "`disconnect` clears the stale local entry.\n"
        )
        out(f"manage    {origin}/connect\n")
        return 2
    d = unwrap(txt) or {}
    if st != 200 or not isinstance(d, dict) or d.get("valid") is False:
        out(f"1024ex · {net} · introspect HTTP {st}: {txt[:200]}\n")
        return 1

    perms = d.get("permissions") or {}
    ov_st, ov_txt = request(base, "GET", "/api/v1/accounts/me/overview", key=key, secret=secret)
    ov = (unwrap(ov_txt) or {}) if ov_st == 200 else {}

    def tick(v):
        return "✓" if v else "✗"

    last, exp, created = d.get("lastUsedAt"), d.get("expiresAt"), d.get("createdAt")
    out(f"1024ex · {net} · connected\n")
    out(
        f"account   {d.get('accountId')} ({d.get('accountType', 'main')}) · "
        f"wallet {short(d.get('walletAddress'))}\n"
    )
    # The 12-char prefix is the key's public identifier — the same mask the web
    # shows on /connect and /settings so the user can match the row — never the secret.
    out(
        f"key       {d.get('label') or '(no label)'} · {key[:12]}… (public id, not the secret) · "
        f"read {tick(perms.get('canRead', True))} trade {tick(perms.get('canTrade'))} "
        "withdraw ✗ (never grantable to a key)\n"
    )
    # "Connected since when, from where" — without this a fresh agent cannot tell
    # "I just connected" from "this laptop was connected months ago".
    out(
        f"since     {fmt_ts(created) if created else 'unknown'} · credentials from "
        f"{'env API_1024_KEY' if source == 'env' else CRED_FILE}\n"
    )
    out(
        f"last used {fmt_ts(last) if last else 'never'} · {d.get('usageCount', 0)} calls · "
        f"{('expires ' + fmt_ts(exp)) if exp else 'no expiry'}\n"
    )
    if ov:
        out(
            f"balance   equity {ov.get('totalEquity')} USDC · available {ov.get('availableBalance')} · "
            f"risk {ov.get('riskLevel')}\n"
        )
    out(f"manage    {origin}/connect   fund: {origin}/deposit   portfolio: {origin}/portfolio\n")
    version_line(out)
    return 0


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


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
    status_code, text = request(base, "GET", INTROSPECT, key=api_key, secret=secret)
    if status_code == 200:
        sys.stdout.write("introspect: OK — credential verified\n")
    else:
        sys.stdout.write(f"introspect HTTP {status_code} — key saved but unverified: {text}\n")
    sys.stdout.write(
        f"Tell the user it worked, and that {web_origin(base)}/connect shows this "
        "connection (and lets them revoke it) any time.\n"
    )
    return 0


def connect(base, flags):
    force = "--force" in flags
    label, wait = None, 100
    for f in flags:
        if f.startswith("--label="):
            label = f.split("=", 1)[1]
        elif f.startswith("--wait="):
            try:
                wait = max(5, int(f.split("=", 1)[1]))
            except ValueError:
                pass
    label = label or detect_host_label() or "1024ex skill"

    # Idempotent: a working credential means there is nothing to mint. Each
    # extra `connect` used to leave one more live trade-capable key behind.
    if not force:
        key, secret, source = resolve_credentials(base)
        if key and secret:
            st, txt = request(base, "GET", INTROSPECT, key=key, secret=secret)
            if st == 200:
                d = unwrap(txt) or {}
                perms = d.get("permissions") or {}
                sys.stdout.write(
                    f"Already connected as {d.get('accountId')} "
                    f"(key {key[:12]}…, label {d.get('label')!r}, "
                    f"trade {'✓' if perms.get('canTrade') else '✗'}).\n"
                    "Nothing to do — tell the user. `status` has the details; "
                    "--force mints a second key; `disconnect` revokes this one.\n"
                )
                return 0
            if st in (401, 403):
                sys.stdout.write(
                    f"Stored key {key[:12]}… is no longer accepted (HTTP {st}) — "
                    "probably revoked on the web. Connecting again.\n"
                )
                if source == "env":
                    sys.stdout.write(
                        "Note: env API_1024_KEY/API_1024_SECRET override the file — "
                        "unset them once the new key is saved.\n"
                    )

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
        st, text = request(base, "POST", "/api/v1/oauth/sessions", body)
        if st == 429:
            sys.stderr.write("rate-limited creating session — wait ~1 min, re-run connect\n")
            return 1
        data = unwrap(text) or {}
        sess = {k: data.get(k) for k in ("sessionId", "statusUrl", "loginUrl", "expiresAt")}
        if not 200 <= st < 300 or not all(sess.values()):
            sys.stderr.write(f"{text}\nHTTP {st}\n")
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
        f"(valid ~{mins} min):\n\n  {sess['loginUrl']}\n\n"
        "In their words: open it — one click on Authorize if already signed in to 1024, "
        "otherwise sign in right there (wallet, Google, X or email). You wait here; on "
        "approval the key is saved to disk and the secret never appears in the chat.\n"
        "Waiting for authorization"
    )
    sys.stdout.flush()

    deadline = min(sess["expiresAt"] / 1000, time.time() + wait)
    while time.time() < deadline:
        st, text = request(base, "GET", sess["statusUrl"])
        if st == 401:
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
    left = max(0, (sess["expiresAt"] - int(time.time() * 1000)) // 60_000)
    sys.stdout.write(
        f"\nstill pending — the user has not approved yet ({left} min left on the same link). "
        "Repeat the link to them, then re-run connect to resume; `status` shows it too.\n"
    )
    return 4


def disconnect(base, flags):
    key, secret, source = resolve_credentials(base)
    if not (key and secret):
        sys.stdout.write(f"Nothing to disconnect — no credentials for {net_name(base)}.\n")
        return 3
    revoked = False
    st, txt = request(base, "GET", INTROSPECT, key=key, secret=secret)
    if st == 0:
        sys.stderr.write(txt + "\n")
        return 1
    if st == 200:
        key_id = (unwrap(txt) or {}).get("apiKeyId")
        if key_id:
            st2, txt2 = request(
                base, "DELETE", f"/api/v1/accounts/me/api-keys/{key_id}", key=key, secret=secret
            )
            revoked = st2 == 200 and bool((unwrap(txt2) or {}).get("revoked"))
    creds = load_json(CRED_FILE)
    if base in creds:
        creds.pop(base)
        save_json(CRED_FILE, creds)
    pending = load_json(PENDING_FILE)
    if base in pending:
        pending.pop(base)
        save_json(PENDING_FILE, pending)
    sys.stdout.write(
        f"Disconnected: key {key[:12]}… "
        f"{'revoked on 1024' if revoked else 'was already invalid'}; local credentials removed.\n"
        f"Reconnect any time with `connect`. The user can confirm at {web_origin(base)}/connect.\n"
    )
    if source == "env":
        sys.stdout.write("Note: env API_1024_KEY/API_1024_SECRET are still set in this shell.\n")
    return 0


# ---------------------------------------------------------------------------
# deposit — a link made for this account, or (--token) a deposit address made
# for it right in the chat; optionally watch either one land
# ---------------------------------------------------------------------------

DEPOSIT_MIN_USDC = 5

# Deposit addresses. 1024's cross-chain deposit service owns every rule behind
# them: which chains and tokens, what a user's "TRC20" or "BNB Chain" means,
# the minimum, refunds, where the USDC lands and each deposit's `stage`. The
# public API forwards to it; this code only prints what it answers.
XC_PATH = "/api/v1/accounts/deposits/crosschain"
# Every bare --wait (link mode and address mode) returns within this, its
# requests included: under the 10 minutes an AI host allows one tool call at
# most. (Claude Code's default is 2 minutes, which is why SKILL.md says to raise
# it or to pass --wait=<seconds>.)
WATCH_WAIT = 540
ADDRESS_POLL = 10
ADDRESS_OFF_AFTER = 3  # 503s in a row: the deposit service is off or down, not restarting
AMOUNT_WAIT_TICKS = 3  # checks a watch gives the delivered amount before printing the finish link
# The address is relayed verbatim on its own line and drawn as a QR, so it must
# be one plain token no chat renders as markup. Printing safety only: what an
# address is, is 1024's call.
PLAIN_TOKEN = re.compile(r"[A-Za-z0-9:._-]{8,200}")
# How the AI relays the block, for a new address and for showing it again.
RELAY_RULE = (
    "Copy the address, the QR and the link exactly as printed — never retyped, shortened or "
    "reformatted, and never an address from earlier in the chat. For a user writing in another "
    "language, put the heading and the facts in their language, keeping the token, network and "
    "numbers as they are."
)
# The server's stages in words. Nothing here classifies a raw status.
STAGE_TEXT = {
    "waiting": "nothing received yet",
    "converting": "received — Relay is converting it to USDC",
    "delivered": "the USDC is in their own wallet",
    "crediting": "moving into their 1024 balance",
    "credited": "in their 1024 balance",
    "refunded": "sent back to the sending address",
    "failed": "Relay could not complete it",
}
SUPPORT_EMAIL = "support@1024ex.com"


def _link_label(key_label):
    """The name the deposit page shows as "Requested by …"."""
    label = detect_host_label()
    if not label and key_label:
        label = key_label.replace(" via 1024 OAuth", "").strip()
    return label or None


def _deposit_url(origin, params):
    """{origin}/deposit plus the non-empty params — every link the deposit page takes."""
    query = urllib.parse.urlencode([(k, v) for k, v in params if v], quote_via=urllib.parse.quote)
    return f"{origin}/deposit" + (f"?{query}" if query else "")


def _account_context(base, timeout=30):
    """(key, secret, introspect fields, problem) — problem is None when the key works.

    problem: "missing" (no credentials), "rejected" (401/403, most likely
    revoked on the web) or "transport:<detail>". Any other answer leaves the
    fields empty and the key in place.
    """
    key, secret, _source = resolve_credentials(base)
    if not (key and secret):
        return None, None, {}, "missing"
    st, txt = request(base, "GET", INTROSPECT, key=key, secret=secret, timeout=timeout)
    if st == 0:
        return key, secret, {}, "transport:" + txt
    if st in (401, 403):
        return key, secret, {}, "rejected"
    d = unwrap(txt) if st == 200 else None
    return key, secret, _obj(d), None


def _cash_and_deposits(base, key, secret, timeout=30):
    """(cash balance, fingerprint of the newest deposit rows) — None where unreadable."""
    cash = None
    st, txt = request(base, "GET", "/api/v1/accounts/me/overview", key=key, secret=secret, timeout=timeout)
    if st == 200:
        ov = unwrap(txt) or {}
        try:
            cash = float(ov.get("balance") if ov.get("balance") is not None else ov.get("availableBalance"))
        except (TypeError, ValueError):
            cash = None
    fingerprint = None
    st, txt = request(base, "GET", "/api/v1/accounts/deposits?limit=5", key=key, secret=secret, timeout=timeout)
    if st == 200:
        rows = unwrap(txt)
        if isinstance(rows, list):
            fingerprint = json.dumps(rows[:1], sort_keys=True) + f"#{len(rows)}"
    return cash, fingerprint


def _arrived(base):
    """The one sentence for money that is in the 1024 balance."""
    return f"Tell the user it arrived; {web_origin(base)}/portfolio shows it.\n"


def _credit_since(base, key, secret, baseline, timeout=30):
    """The "Credited" line once the balance rose by 1 USDC or more, or a new
    deposit row appeared, since `baseline` (a _cash_and_deposits pair); None
    until then. Link mode and the address watch share this one check."""
    base_cash, base_fp = baseline
    cash, fp = _cash_and_deposits(base, key, secret, timeout)
    rose = cash is not None and base_cash is not None and cash - base_cash >= 1
    new_row = fp is not None and base_fp is not None and fp != base_fp
    if not (rose or new_row):
        return None
    gained = f"+{cash - base_cash:.2f} USDC, " if rose else ""
    now = f"{cash:.2f}" if cash is not None else "?"
    return f"Credited: {gained}balance {now} USDC. " + _arrived(base)


def deposit(base, flags):
    amount = chain = token = status_id = qr_theme = None
    wait_flag = None  # None: absent · "default": bare --wait · int: --wait=SECONDS
    routes = status_mode = finish_sent = False
    for f in flags:
        if f.startswith("--amount="):
            amount = f.split("=", 1)[1].strip()
        elif f.startswith("--chain="):
            chain = f.split("=", 1)[1].strip()
        elif f == "--wait":
            wait_flag = "default"
        elif f.startswith("--wait="):
            try:
                wait_flag = max(10, int(f.split("=", 1)[1]))
            except ValueError:
                pass
        elif f.startswith("--token="):
            token = f.split("=", 1)[1].strip()
        elif f == "--status":
            status_mode = True
        elif f.startswith("--status="):
            status_mode, status_id = True, (f.split("=", 1)[1].strip() or None)
        elif f == "--routes":
            routes = True
        elif f.startswith("--qr="):
            qr_theme = f.split("=", 1)[1].strip().lower()
        elif f == "--finish-sent":
            finish_sent = True

    # Address mode: --token makes one, --status follows one, --routes lists what
    # they take. Beyond the flags being there, 1024 checks everything and says why.
    if routes or status_mode or token is not None:
        if qr_theme not in (None, "dark", "light", "none"):
            sys.stderr.write(f"--qr takes dark, light or none, got {qr_theme!r}\n")
            return 2
        if routes:
            return _deposit_routes(base)
        if status_mode:
            wait = WATCH_WAIT if wait_flag == "default" else (wait_flag or 0)
            return _deposit_status(base, status_id, wait, qr_theme, finish_sent)
        return _deposit_address(base, token, chain, amount, qr_theme or "dark", wait_flag is not None)

    # Link mode: the amount may carry thousands separators; the chain is lower-cased.
    wait = WATCH_WAIT if wait_flag == "default" else (wait_flag or 0)
    amount = amount.replace(",", "") if amount is not None else None
    chain = (chain.lower() or None) if chain is not None else None
    if amount is not None:
        try:
            if not float(amount) > 0:
                raise ValueError
        except ValueError:
            sys.stderr.write(f"--amount must be a positive USDC number, got {amount!r}\n")
            return 2
    return _deposit_link(base, amount, chain, wait)


def _deposit_link(base, amount, chain, wait, note=None, ctx=None):
    """Link mode: a /deposit link made for this account; `wait` > 0 watches the credit.

    `note` leads the output when address mode falls back to this; `ctx` is its
    _account_context, so the key is not introspected twice.
    """
    out = sys.stdout.write
    if note:
        out(note + "\n\n")
    origin, net = web_origin(base), net_name(base)
    key, secret, info, problem = ctx or _account_context(base)
    if problem == "rejected":
        key = secret = None  # revoked on the web: still hand out a plain link
    wallet, key_label, account = info.get("walletAddress"), info.get("label"), info.get("accountId")

    # `to` is the wallet's tail, not the whole address: enough for the page to
    # warn "you are signed in to a different account", nothing more in a URL.
    params = [("amount", amount), ("chain", chain), ("from", _link_label(key_label))]
    if wallet:
        params.append(("to", wallet[-6:]))
    link = _deposit_url(origin, params)

    who = f"account {account} (wallet {short(wallet)})" if wallet else "whichever account they sign in with"
    out(f"Deposit link ({net}), made for {who}:\n\n  {link}\n\n")
    out(
        "In their words: open it and sign in if asked — they land straight back on the page. "
        "Then pay with USDC they hold on Base, Ethereum or Solana, other crypto from any chain, "
        f"or a card or bank transfer. Minimum {DEPOSIT_MIN_USDC} USDC; fees are shown before they confirm.\n"
    )
    if wallet:
        out("Signed in to a different account? The page warns them before they pay.\n")
    if base == TESTNET:
        out(
            "Testnet: the page has a one-click test-USDC button, or credit it yourself with "
            "`--testnet POST /api/v1/testnet/faucet/claim '{}'`.\n"
        )

    if not wait:
        if key and secret:
            # As an address-mode fallback, "same flags" would re-run that mode: say it plainly.
            out("Run `deposit --wait` to watch until it is credited.\n" if note else
                "Run `deposit --wait` (same flags) to watch until it is credited.\n")
        return 0
    if not (key and secret):
        out("Not connected, so I cannot watch for the credit — `connect` first to confirm arrival.\n")
        return 3

    baseline = _cash_and_deposits(base, key, secret)
    base_cash, base_fp = baseline
    if base_cash is None and base_fp is None:
        sys.stderr.write("could not read the balance to watch for the deposit\n")
        return 1
    shown = f"{base_cash:.2f}" if base_cash is not None else "?"
    out(f"Watching for the credit (balance now {shown} USDC)")
    sys.stdout.flush()
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(10)
        credited = _credit_since(base, key, secret, baseline)
        if credited:
            out("\n" + credited)
            return 0
        out(".")
        sys.stdout.flush()
    out(
        "\nNot credited yet. Bridge deposits usually land within a few minutes, sometimes longer. "
        "Re-run `deposit --wait` to keep watching; `status` shows the balance any time.\n"
    )
    return 4


# ---- deposit addresses: printing the server's answers ---------------------


def _obj(v):
    return v if isinstance(v, dict) else {}


def _num(v):
    """A JSON number as a person writes it: 20 → "20", 20.5 → "20.5"."""
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _server_reason(text):
    """The server's own words in an error body: details.reason, else message."""
    try:
        obj = json.loads(text)
    except ValueError:
        return (text or "").strip()[:200]
    err = _obj(obj.get("error")) if isinstance(obj, dict) else {}
    return str(_obj(err.get("details")).get("reason") or err.get("message") or "")


def _transient(st):
    """No answer, rate-limited or a server hiccup: worth another look later."""
    return st == 0 or st == 429 or st >= 500


def _not_deployed(st):
    """No such endpoint here: 404, or 405 (the path answers another method only)."""
    return st in (404, 405)


def _until(deadline, cap):
    """A request timeout that never runs past `deadline` (None: just `cap`)."""
    return cap if deadline is None else max(1.0, min(cap, deadline - time.time()))


def _mmss(seconds):
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _net_flags(base):
    if base == MAINNET:
        return ""
    return " --testnet" if base == TESTNET else f" --base={shlex.quote(base)}"


def _status_cmd(base, rid, extra=" --wait"):
    return f"python3 scripts/api.py deposit --status={shlex.quote(rid)}{extra}{_net_flags(base)}"


def _verify_link(base, rid, d, label):
    """/deposit?verify=… — signed in, 1024 shows the address it has on file for THIS user."""
    return _deposit_url(web_origin(base), [
        ("verify", rid), ("chain", _obj(d.get("chain")).get("key")),
        ("token", _obj(d.get("token")).get("symbol")), ("from", label),
    ])


def _finish_link(base, rid, d, label):
    """/deposit?verify=…&finish=1 — once the USDC is in their wallet, one tap moves it in.

    `chain` is the server's delivery.chainName ("base", "solana"): the page
    resolves it like any chain hint, so no chain table lives here.
    """
    return _deposit_url(web_origin(base), [
        ("verify", rid), ("finish", "1"), ("amount", d.get("deliveredUsdc")),
        ("chain", str(_obj(d.get("delivery")).get("chainName") or "").lower()), ("from", label),
    ])


def _render_qr(text, theme):
    """`text` as a half-block QR via the vendored scripts/qr.py; None when unavailable."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qr.py")
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_1024ex_qr", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.render_half_blocks(text, theme)
    except Exception:  # missing file, Python < 3.9, anything — the address still prints
        return None


def _delivery_text(dl, whose="their"):
    """Where the USDC lands, from the server's `delivery`: "USDC in their wallet 0xAbC1…b2c3 on Base"."""
    dl = _obj(dl)
    if not dl.get("address"):
        return None
    on = f" on {dl['chainName']}" if dl.get("chainName") else ""
    return f"{dl.get('token') or 'USDC'} in {whose} wallet {short(dl['address'], 6, 4)}{on}"


def _address_fallback(base, st, ctx):
    """No address from this deployment (404/405 not deployed · 503 off or down): the web link."""
    why = "aren't enabled on this deployment yet" if _not_deployed(st) else "are unavailable right now"
    return _deposit_link(base, None, None, 0, ctx=ctx, note=(
        f"In-chat deposit addresses {why} — here is the web link instead; the page makes "
        "the address once they sign in."))


def _address_problem(base, ctx, with_link=True):
    """Exit 3 for a missing, rejected or unreachable key, after saying what to do."""
    problem = ctx[3]
    if problem.startswith("transport:"):
        sys.stderr.write(problem.split(":", 1)[1] + "\n")
        return 3
    if problem == "rejected":
        note = (
            "The stored key is no longer accepted — most likely revoked on the web. A deposit "
            "address is made for one account, so reconnect first (`connect`, with their OK)."
        )
    else:
        note = (
            "Not connected. A deposit address is made for one account, so offer to connect "
            "first (`connect`)."
        )
    if with_link:
        _deposit_link(base, None, None, 0, ctx=ctx, note=note + " Meanwhile the web link works — "
                      "the page makes the same kind of address once they sign in:")
    else:
        sys.stdout.write(note + "\n")
    return 3


def _key_refused(st, txt):
    sys.stderr.write(
        f"HTTP {st}: {_server_reason(txt) or 'refused'}. A deposit address needs this account's "
        "main-account key; if it was revoked on the web, reconnect with `connect` (with their OK).\n"
    )
    return 3


def _address_block(d, token, chain, qr, verify):
    """The markdown to relay: heading, the address alone, the QR, the facts, the verify link.

    Every fact is a server field; `token` and `chain` (what the user asked
    for) only stand in for a name the server left out.
    """
    ch, tok = _obj(d.get("chain")), _obj(d.get("token"))
    sym = tok.get("symbol") or token
    cname = ch.get("name") or ch.get("key") or chain
    lines = [f"**Your {sym} deposit address — {cname}**", "", d["depositAddress"], ""]
    if qr:
        lines += ["```text", qr, "```", ""]
    what = f"{d['amount']} {sym}" if d.get("amount") else f"only {sym}"
    lines.append(f"- Send **{what} on {cname}** — no other token, no other network.")
    # 1024 checked the amount above against a minimum that counts what Relay delivers.
    rule = []
    if d.get("minUsd") is not None:
        rule.append(f"What arrives must come to at least {_num(d['minUsd'])} USD after conversion")
    if d.get("autoRefund") is False:
        rule.append(f"{cname} has no auto-refund, so less, or the wrong token, is lost")
    elif d.get("autoRefund") is True:
        rule.append("if it can't be converted, Relay refunds the sending address where it can — "
                    "an exchange withdrawal may not come back on its own")
    if rule:
        text = "; ".join(rule)
        lines.append(f"- {text[0].upper()}{text[1:]}.")
    fee = f" (Relay fee ≈{_num(d['feePercent'])}%)" if d.get("feePercent") is not None else ""
    if d.get("expectedUsdc"):
        amount = f"{d['amount']} {sym}" if d.get("amount") else "It"
        lines.append(f"- {amount} comes to about {d['expectedUsdc']} USDC{fee}.")
    else:
        lines.append(f"- Relay converts it to USDC{fee}.")
    where = _delivery_text(d.get("delivery"), "your")
    if where:
        lines.append(f"- It arrives as {where}; one tap on 1024 then moves it into your balance.")
    lines += ["", "Check it is yours before sending — sign in and 1024 shows this same address:", verify, ""]
    return "\n".join(lines) + "\n"


def _relay_block(base, rid, d, token, chain, theme, label, lead):
    """Print the block the AI relays and how to relay it — one renderer for a new
    address and for showing it again. True when a QR was drawn."""
    out = sys.stdout.write
    qr = _render_qr(d["depositAddress"], theme) if theme != "none" else None
    fence = ", and the QR stays inside its ```text fence" if qr else ""
    out(f"{lead}Relay the block between the two ✂ lines — it is markdown{fence}. {RELAY_RULE}\n\n")
    out("✂ ──────────── relay from here ────────────\n")
    out(_address_block(d, token, chain, qr, _verify_link(base, rid, d, label)))
    out("✂ ──────────── to here ────────────\n\n")
    return bool(qr)


def _deposit_address(base, token, chain, amount, theme, wait_asked):
    """--token: make a deposit address for this account and print it ready to relay."""
    out, err = sys.stdout.write, sys.stderr.write
    if not token:
        err("--token takes the symbol they will send, e.g. --token=USDT\n")
        return 2
    if not chain:
        err(
            "--chain is required: ask the user which network they will send on (the exchange's "
            "withdrawal screen names it) and pass their words, e.g. --chain=TRC20. "
            "`deposit --routes` lists every chain.\n"
        )
        return 2
    if not amount:
        err(
            f"--amount is required: ask the user how much {token} they will send — 1024 checks it "
            "against the minimum before they send, and below it a deposit can be lost.\n"
        )
        return 2
    ctx = _account_context(base)
    key, secret, info, problem = ctx
    if problem:
        return _address_problem(base, ctx)

    body = json.dumps({"chain": chain, "token": token, "amount": amount}, separators=(",", ":"))
    st, txt = request(base, "POST", XC_PATH + "/addresses", body, key=key, secret=secret, timeout=60)
    if st == 0:
        err(
            f"{txt}\nNo answer from 1024, so there is no address to hand over. Try once more; if "
            "that fails too, give them the web link (`deposit`). Never loop this: each run makes "
            "a new address.\n"
        )
        return 3
    if _not_deployed(st) or st == 503:
        return _address_fallback(base, st, ctx)
    if st == 400:
        err(f"No address made: {_server_reason(txt) or 'the request was refused'}\n")
        return 2
    if st in (401, 403):
        return _key_refused(st, txt)
    if st == 429:
        err("Rate-limited — wait a minute before asking again. Never loop this: each run makes a new address.\n")
        return 1
    d = unwrap(txt) if 200 <= st < 300 else None
    if not isinstance(d, dict):
        err(f"HTTP {st}: {_server_reason(txt) or txt[:200].strip() or 'no details'}\n")
        return 1
    rid = str(d.get("requestId") or "")
    if not rid or not PLAIN_TOKEN.fullmatch(str(d.get("depositAddress") or "")):
        err(f"Unexpected answer with no usable address in it — nothing to hand over: {txt[:300]}\n")
        return 1

    qr = _relay_block(
        base, rid, d, token, chain, theme, _link_label(info.get("label")),
        f"Deposit address made for account {info.get('accountId') or '(this key)'} ({net_name(base)}). ",
    )
    out(f"Next: after relaying, run `{_status_cmd(base, rid)}` while the user sends.\n")
    if qr and theme == "dark":
        out(
            "(The QR is drawn for a dark chat; on a light one show it again, same address: "
            f"`{_status_cmd(base, rid, ' --qr=light')}`.)\n"
        )
    elif theme != "none" and not qr:
        out("(No QR here — scripts/qr.py is missing or Python is older than 3.9; the verify page shows one.)\n")
    if wait_asked:
        out("(--wait does nothing on a create: nobody can send before you relay the address.)\n")
    return 0


def _fetch_address(base, key, secret, rid, timeout=20):
    path = f"{XC_PATH}/addresses/{urllib.parse.quote(rid, safe='')}"
    return request(base, "GET", path, key=key, secret=secret, timeout=timeout)


def _status_problem(base, ctx, st, txt, deadline=None):
    """Exit code for a status lookup that returned no deposit, after saying why."""
    key, secret = ctx[0], ctx[1]
    if st == 0:
        sys.stderr.write(txt + "\n")
        return 3
    if st == 400:
        sys.stderr.write(f"{_server_reason(txt) or 'Refused'} — pass the requestId printed with the address.\n")
        return 2
    if st in (401, 403):
        return _key_refused(st, txt)
    if _not_deployed(st):
        # An id that is not this account's, or no such endpoint on this
        # deployment at all? Only a routes answer tells the two apart.
        probe, probe_txt = request(base, "GET", XC_PATH + "/routes", key=key, secret=secret,
                                   timeout=_until(deadline, 30))
        if st == 405 or _not_deployed(probe):
            _address_fallback(base, 404, ctx)
            return 1
        if not 200 <= probe < 300:
            why = "no answer" if probe == 0 else f"HTTP {probe}: {_server_reason(probe_txt) or 'no details'}"
            sys.stderr.write(
                f"1024 found no such deposit for this account and could not confirm why just now ({why}). "
                "Don't send to this address until the same line, run again in a minute, says it is theirs.\n"
            )
            return 3
        sys.stdout.write(
            "This address is not linked to your 1024 account — don't send to it. Tell the user "
            "plainly: only an address `deposit --token` printed for this account, with its verify "
            "link, is theirs.\n"
        )
        return 5
    sys.stderr.write(
        f"HTTP {st}: {_server_reason(txt) or 'no details'} — 1024 could not look it up just now. "
        "Nothing is lost; run the same line again in a minute.\n"
    )
    return 3


def _stage_text(d):
    """One line saying where the money is, from the server's `stage` and fields."""
    stage = str(d.get("stage") or "unknown")
    where = _delivery_text(d.get("delivery")) if stage == "delivered" else None
    if where:
        return (f"{d['deliveredUsdc']} " if d.get("deliveredUsdc") else "") + where
    return STAGE_TEXT.get(stage, stage)


def _outcome(base, rid, d, label):
    """(text, exit code) once the deposit is over — credited 0, refunded or failed 5 —
    else None. A look and a watch both end through here."""
    stage = d.get("stage")
    if stage == "credited":
        return "Credited — it is in their 1024 balance. " + _arrived(base), 0
    verify = _verify_link(base, rid, d, label)
    if stage == "refunded":
        tx = f" (refund tx {d['refundTxHash']})" if d.get("refundTxHash") else ""
        return (
            f"Refunded: Relay sent it back to the sending address{tx}. Sent from an exchange? It "
            "may not be credited back on its own — keep the transaction hash and contact "
            f"{SUPPORT_EMAIL}. The verify page has the details:\n\n  {verify}\n"
        ), 5
    if stage == "failed":
        return (
            "Relay could not complete this deposit. Tell the user plainly — do not guess where the "
            f"money is. Keep the sending transaction hash and contact {SUPPORT_EMAIL}; the verify page "
            f"shows what 1024 knows:\n\n  {verify}\n"
        ), 5
    return None


def _pending_text(base, rid, d, label, wait_arg=" --wait", finish_sent=False):
    """What comes next for a deposit still on its way, by the server's `stage` —
    one text for a look, a watch that stops at `delivered`, and a timeout."""
    stage = str(d.get("stage") or "unknown")
    cmd = _status_cmd(base, rid, wait_arg)
    if stage == "delivered":
        lead = (
            "It reaches 1024 once they open the finish link and confirm — here it is again, if they need it:"
            if finish_sent else "One tap moves it into 1024 — hand them this link:"
        )
        how = ("open it and confirm; the amount is filled in" if d.get("deliveredUsdc")
               else "open it, enter the USDC that arrived and confirm")
        follow = "This keeps following the move-in" if finish_sent else \
            "Once it is relayed, this follows the move-in into 1024"
        return (
            f"{lead}\n\n  {_finish_link(base, rid, d, label)}\n\n"
            f"In their words: {how}. Nothing moves until they approve it in their wallet.\n"
            f"{follow}:\n\n  {_status_cmd(base, rid, wait_arg + ' --finish-sent')}\n"
        )
    if stage == "waiting":
        tok, ch = _obj(d.get("token")).get("symbol"), _obj(d.get("chain")).get("name")
        check = f"If they already sent it, check it was {tok} on {ch} — an" if tok and ch else "An"
        return (
            f"Nothing has arrived at this address yet. {check} exchange withdrawal can take a "
            f"while. This watches for it:\n\n  {cmd}\n"
        )
    if stage == "converting":
        return f"Relay has it and is converting it to USDC — nothing is lost. This follows it in:\n\n  {cmd}\n"
    if stage == "crediting":
        return f"It is moving into their 1024 balance — nothing is lost. This confirms it:\n\n  {cmd}\n"
    return f"This follows it:\n\n  {cmd}\n"


def _deposit_status(base, rid, wait, qr_theme, finish_sent):
    """--status=<requestId>: where that deposit is; --wait follows it into 1024."""
    if not rid:
        sys.stderr.write("--status takes the requestId printed with the address: --status=<requestId>\n")
        return 2
    deadline = time.time() + wait if wait else None  # the whole call, introspect included
    ctx = _account_context(base, timeout=_until(deadline, 30))
    key, secret, info, problem = ctx
    if problem:
        return _address_problem(base, ctx, with_link=False)
    label = _link_label(info.get("label"))
    if wait:
        if qr_theme:
            sys.stdout.write("(--qr is ignored while watching: `--status=<id> --qr=…` without --wait shows the address again.)\n")
        return _watch_address(base, ctx, rid, wait, deadline, label, finish_sent)
    st, txt = _fetch_address(base, key, secret, rid)
    d = unwrap(txt) if st == 200 else None
    if st == 200 and not isinstance(d, dict):
        sys.stderr.write(f"Unexpected answer: {txt[:300]}\n")
        return 1
    if st != 200:
        return _status_problem(base, ctx, st, txt)
    return _print_status(base, rid, d, label, qr_theme)


def _print_status(base, rid, d, label, qr_theme):
    """One look: where the deposit is, then what comes next. With --qr, first the
    address again — the create's own relay block — while nothing has been sent.
    0 credited · 5 over · 4 not yet."""
    out = sys.stdout.write
    stage = str(d.get("stage") or "unknown")
    ch, tok = _obj(d.get("chain")), _obj(d.get("token"))
    shown = False
    if qr_theme:
        if stage != "waiting":
            out("(Not shown again: this address already has its deposit — make a new one for another.)\n\n")
        elif not (tok.get("symbol") and ch.get("name") and PLAIN_TOKEN.fullmatch(str(d.get("depositAddress") or ""))):
            out("(Not shown again: 1024 did not name its token and chain just now — try again in a minute.)\n\n")
        else:
            drawn = f", QR drawn for a {qr_theme} chat" if qr_theme != "none" else ""
            _relay_block(base, rid, d, None, None, qr_theme, label, f"The same deposit address again{drawn}. ")
            shown = True
    out(
        f"Deposit {short(rid, 10, 6)} · {tok.get('symbol') or '?'} on "
        f"{ch.get('name') or ch.get('key') or '?'} → {_delivery_text(d.get('delivery')) or 'USDC'}\n"
    )
    out(f"stage     {stage} — {_stage_text(d)}\n")
    for name, field in (("expected", "expectedUsdc"), ("delivered", "deliveredUsdc")):
        if d.get(field):
            out(f"{name:<9} {d[field]} USDC\n")
    if d.get("originTxHash"):
        out(f"sent tx   {d['originTxHash']}\n")
    if d.get("refundTxHash"):
        out(f"refund tx {d['refundTxHash']}\n")
    if d.get("createdAt"):
        out(f"made      {fmt_ts(d['createdAt'])}\n")
    done = _outcome(base, rid, d, label)
    if done:
        out("\n" + done[0])
        return done[1]
    out("\n" + _pending_text(base, rid, d, label))
    if not shown:
        out(f"\nSigned in, the verify page shows this deposit any time:\n\n  {_verify_link(base, rid, d, label)}\n")
    return 4


def _watch_address(base, ctx, rid, wait, deadline, label, finish_sent):
    """Follow one deposit, a line per change of the server's `stage`, until
    0 credited · 5 refunded or failed · 3 no answer, or the service is off ·
    4 time is up — or `delivered`: the USDC is in their wallet and only the
    finish link moves it on, which the AI can hand over only once this call
    returns. So it stops there (4, with the link) unless --finish-sent says
    the link is already out; then it follows the move-in."""
    key, secret = ctx[0], ctx[1]
    out = sys.stdout.write
    start = time.time()
    wait_arg = " --wait" if wait == WATCH_WAIT else f" --wait={wait}"
    resume = _status_cmd(base, rid, wait_arg + (" --finish-sent" if finish_sent else ""))
    out(
        f"Watching deposit {short(rid, 10, 6)} for up to {wait} s (a check every {ADDRESS_POLL} s). "
        f"If this call is cut off, resume with: {resume}\n"
    )
    sys.stdout.flush()
    d = last = baseline = None
    dotted = False
    looks = off = held = 0
    last_err = ""
    while True:
        st, txt = _fetch_address(base, key, secret, rid, timeout=_until(deadline, 20))
        looks += 1
        got = unwrap(txt) if st == 200 else None
        if isinstance(got, dict):
            d, off = got, 0
        elif st == 0 and looks == 1:
            sys.stderr.write(txt + "\n")  # no network at all: say so now, not in 9 minutes
            return 3
        elif st == 200 or _transient(st):
            off = off + 1 if st == 503 else 0
            last_err = "no answer" if st == 0 else f"HTTP {st}: {_server_reason(txt) or 'no details'}"
            if off >= ADDRESS_OFF_AFTER:
                out(
                    ("\n" if dotted else "")
                    + f"1024 answered {last_err} ({off} times in a row), so the chat cannot follow "
                    "this deposit right now. Nothing is lost — signed in, the verify page shows "
                    f"where it is:\n\n  {_verify_link(base, rid, d or {}, label)}\n\n"
                    f"Or run this again later:\n\n  {resume}\n"
                )
                return 3
        else:
            out("\n" if dotted else "")
            return _status_problem(base, ctx, st, txt, deadline)
        if isinstance(got, dict):
            stage = str(d.get("stage") or "unknown")
            if stage != last:
                out(("\n" if dotted else "") + f"  {_mmss(time.time() - start)}  {stage:<10} {_stage_text(d)}\n")
                sys.stdout.flush()
                dotted, last = False, stage
            done = _outcome(base, rid, d, label)
            if done:
                out("\n" + done[0])
                return done[1]
            if stage == "delivered" and not finish_sent:
                # Hand the link over now — with the amount, once 1024 knows it.
                if d.get("deliveredUsdc") or held >= AMOUNT_WAIT_TICKS:
                    out(("\n" if dotted else "") + "\n" + _pending_text(base, rid, d, label, wait_arg))
                    return 4
                held += 1
            elif stage in ("delivered", "crediting"):
                # The move-in is a wallet deposit into 1024: spot it the way link mode does.
                left = deadline - time.time()
                if left > 2:  # room for its two reads
                    if baseline is None:
                        baseline = _cash_and_deposits(base, key, secret, min(30, left / 2))
                    else:
                        credited = _credit_since(base, key, secret, baseline, min(30, left / 2))
                        if credited:
                            out(("\n" if dotted else "") + credited)
                            return 0
        if time.time() + ADDRESS_POLL > deadline:
            break
        time.sleep(ADDRESS_POLL)
        out(".")
        dotted = True
        sys.stdout.flush()
    out("\n" if dotted else "")
    if d is None:
        sys.stderr.write(
            f"Could not read this deposit from 1024 in {wait} s (last: {last_err or 'no answer'}). "
            f"Nothing is lost; run the same line again:\n\n  {resume}\n"
        )
        return 3
    out(f"\nStopped watching after {wait} s. " + _pending_text(base, rid, d, label, wait_arg, finish_sent))
    return 4


def _deposit_routes(base):
    """--routes: the chains, tokens and minimum an address takes, as the server lists them."""
    out, err = sys.stdout.write, sys.stderr.write
    ctx = _account_context(base)
    key, secret, info, problem = ctx
    if problem:
        return _address_problem(base, ctx)
    st, txt = request(base, "GET", XC_PATH + "/routes", key=key, secret=secret)
    if st == 0:
        err(txt + "\n")
        return 3
    if _not_deployed(st) or st == 503:
        return _address_fallback(base, st, ctx)
    if st in (401, 403):
        return _key_refused(st, txt)
    d = unwrap(txt) if st == 200 else None
    if not isinstance(d, dict):
        err(f"HTTP {st}: {_server_reason(txt) or txt[:200].strip() or 'no details'}\n")
        return 1
    if d.get("enabled") is False:
        return _address_fallback(base, 503, ctx)

    out(f"Deposit-address routes for account {info.get('accountId') or '(this key)'} ({net_name(base)})\n")
    if d.get("minUsd") is not None:
        out(f"Minimum {_num(d['minUsd'])} USD per deposit, counted after conversion.\n")
    where = _delivery_text(d.get("delivery"))
    if where:
        out(f"Converted to {where}; one tap on 1024 then moves it into the balance.\n")
    rows = [("chain", "--chain=", "--token=", "refund", "--chain also takes")]
    for s in d.get("sources") or []:
        s = _obj(s)
        rows.append((
            str(s.get("name") or "?"),
            str(s.get("key") or s.get("chainId") or "?"),
            " ".join(str(_obj(t).get("symbol") or "?") for t in s.get("tokens") or []),
            {True: "auto", False: "none — lost"}.get(s.get("autoRefund"), "?"),
            ", ".join(str(a) for a in s.get("aliases") or []),
        ))
    if len(rows) == 1:
        out("\n  (no routes offered right now)\n")
        return 0
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    out("\n")
    for r in rows:
        out(("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[4]).rstrip() + "\n")
    out(
        f"\nMake one: `python3 scripts/api.py deposit --token=<SYM> --chain=<what they said> "
        f"--amount=<N>{_net_flags(base)}`\n"
    )
    return 0


def main(argv):
    # A Windows pipe defaults to the ANSI code page, which cannot encode ✓, … or
    # the QR's block glyphs; an encoding error must never swallow a minted address.
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

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

    if args:
        verb = args[0].lower()
        if verb == "status":
            return status(base, flags)
        if verb == "connect":
            return connect(base, flags)
        if verb == "disconnect":
            return disconnect(base, flags)
        if verb == "deposit":
            return deposit(base, flags)

    if len(args) < 2:
        sys.stderr.write(__doc__)
        return 2
    method, path = args[0].upper(), args[1]
    body = args[2] if len(args) > 2 else ""

    key, secret, _source = resolve_credentials(base)

    st, out = request(base, method, path, body, key=key, secret=secret)
    if st == 0:
        sys.stderr.write(out + "\n")
        return 3
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if not 200 <= st < 300:
        sys.stderr.write(f"HTTP {st}\n")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        # Ctrl-C, or the host stopping a call: no traceback. A deposit watch
        # printed its resume line first thing.
        sys.stderr.write("\nStopped.\n")
        sys.exit(130)
