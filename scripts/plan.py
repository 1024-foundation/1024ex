#!/usr/bin/env python3
"""1024 order preview — validate and price a basket of perp entries, each with
its take-profit and stop-loss, publish it as one short page on 1024ex.com (the
basket's max profit, max loss and margin, one payoff curve, the exact
requests), and let the user send it with one click on that page — from any
device, with their own 1024 login. Stdlib only; every exchange call goes
through api.py.

    python3 scripts/plan.py preview plan.json [--testnet] [--wait=300] [--ttl=900]
    python3 scripts/plan.py status  pl_…      [--testnet] [--wait=0]
    python3 scripts/plan.py execute plan.json [--testnet]

`preview` prints the summary the agent relays, uploads the page
(`POST /api/v1/plans`, signed with this agent's key), prints its link and
waits up to --wait seconds for the user to click Place or Cancel there. The
page stays open for --ttl seconds (default 15 min) whether or not this
command is still waiting — `status` reads where it got to, any time. The
link is served by 1024ex.com, not by this machine: it opens on a phone, in
a sandboxed host, anywhere. Placing needs the user's own 1024 web login,
for the same account this key is connected to; this key never leaves here.
`execute` skips the page: for a user who confirmed the same summary in the
chat and has no browser at all.

plan.json — every number a decimal string in human units:

{
  "title": "Long semis",
  "thesis": "one line the page shows under the title (optional)",
  "legs": [
    {"market": "NVDA-USDC", "side": "buy", "size": "10", "leverage": 3,
     "entry": "market", "takeProfit": "235", "stopLoss": "200"},
    {"market": "AMD-USDC", "side": "buy", "size": "4", "leverage": 3,
     "entry": "limit", "price": "490", "takeProfit": "540", "stopLoss": "465"}
  ]
}

Each leg: side buy|sell · entry market|limit (+ price) · leverage,
takeProfit and stopLoss all required. A leg is sent as ONE bracket order —
entry, TP and SL together — so the position is never bare. clientOrderIds
derive from the file's content and the UTC date, so re-running the same
plan the same day retries rather than doubles.

Exit codes:
    preview   0 placed, every leg accepted · 6 placed, some leg refused (the
    status    output says which) · 5 cancelled on the page · 4 no decision
              yet — the page is still open until it expires, or it expired
              (the output says which; `status pl_…` picks it up again) ·
              2 plan invalid (reasons printed, nothing uploaded) · 3 not
              connected (nothing uploaded) · 1 error
    execute   0 / 6 / 2 / 3 / 1 as above
"""
import hashlib
import json
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402 — the bundled signed client; every call goes through it

MAX_LEGS = 12
PRICE_BAND = 0.15          # perp limit more than this from mark → TRADE_PRICE_DEVIATION
DEGRADED_LEV, DEGRADED_NOTIONAL = 2, 1000.0   # off-session caps on new perp risk
DEFAULT_WAIT = 300         # seconds `preview` waits for the click before handing over to `status`
DEFAULT_TTL = 900          # seconds the page accepts a click (server clamps to 60–3600)
POLL_S = 2                 # how often the plan is read back while waiting
PLANS = "/api/v1/plans"


class PlanError(Exception):
    pass


def out(s=""):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def dec(v, what):
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise PlanError(f"{what}: not a number ({v!r})")
    if not d.is_finite() or d <= 0:
        raise PlanError(f"{what}: must be a positive number ({v!r})")
    return d


def multiple_of(value, step):
    return step > 0 and (value % step) == 0


def fp(x):
    """Price for humans: 2 dp above 100, 4 dp above 1, else 6 dp — trimmed."""
    x = float(x)
    dp = 2 if abs(x) >= 100 else 4 if abs(x) >= 1 else 6
    s = f"{x:,.{dp}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def fm(x, sign=False):
    """Money: 2 dp, thousands, optional leading sign."""
    x = float(x)
    s = f"{abs(x):,.2f}"
    if sign:
        return ("+" if x >= 0 else "−") + s
    return ("−" if x < 0 else "") + s


def plan_id(raw):
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return "p" + hashlib.sha256(raw + day.encode()).hexdigest()[:8]


# ─── plan → legs ─────────────────────────────────────────────────────────────


def normalise(plan):
    if not isinstance(plan, dict):
        raise PlanError("plan must be a JSON object")
    legs_in = plan.get("legs")
    if not isinstance(legs_in, list) or not legs_in:
        raise PlanError("plan.legs must be a non-empty list")
    if len(legs_in) > MAX_LEGS:
        raise PlanError(f"plan.legs: at most {MAX_LEGS} legs")
    legs = []
    for i, raw in enumerate(legs_in, 1):
        tag = f"leg {i}"
        if not isinstance(raw, dict):
            raise PlanError(f"{tag}: must be an object")
        market = str(raw.get("market", "")).strip().upper()
        if not market:
            raise PlanError(f"{tag}: market is required")
        side = str(raw.get("side", "")).lower()
        side = {"long": "buy", "short": "sell"}.get(side, side)
        if side not in ("buy", "sell"):
            raise PlanError(f"{tag}: side must be buy or sell")
        entry = str(raw.get("entry", "market")).lower()
        if entry not in ("market", "limit"):
            raise PlanError(f"{tag}: entry must be market or limit")
        if raw.get("leverage") is None:
            raise PlanError(f"{tag}: leverage is required (set it, do not inherit a preference)")
        try:
            leverage = int(str(raw.get("leverage")))
        except ValueError:
            raise PlanError(f"{tag}: leverage must be an integer")
        if leverage < 1:
            raise PlanError(f"{tag}: leverage must be ≥ 1")
        if raw.get("takeProfit") is None or raw.get("stopLoss") is None:
            raise PlanError(f"{tag}: takeProfit and stopLoss are both required — both, or no entry")
        leg = SimpleNamespace(
            n=i, tag=tag, market=market, side=side, long=(side == "buy"), entry=entry,
            size=dec(raw.get("size"), f"{tag} size"), leverage=leverage, price=None,
            tp=dec(raw.get("takeProfit"), f"{tag} takeProfit"), sl=dec(raw.get("stopLoss"), f"{tag} stopLoss"),
            errors=[], warnings=[], result=None,
        )
        if entry == "limit":
            if raw.get("price") is None:
                raise PlanError(f"{tag}: price is required for a limit entry")
            leg.price = dec(raw.get("price"), f"{tag} price")
        legs.append(leg)
    return legs


# ─── exchange reads ──────────────────────────────────────────────────────────


def get(base, path, key=None, secret=None):
    st, txt = api.request(base, "GET", path, key=key, secret=secret)
    if st == 0:
        raise PlanError(f"{path}: {txt}")
    return st, api.unwrap(txt)


def enrich(base, leg):
    st, m = get(base, f"/api/v1/perp/markets/{leg.market}")
    if st != 200 or not isinstance(m, dict):
        leg.errors.append(f"not a perp market (HTTP {st})")
        return
    if m.get("status") != "active":
        leg.errors.append(f"status {m.get('status')}")
    st, t = get(base, f"/api/v1/perp/markets/{leg.market}/ticker")
    if st != 200 or not isinstance(t, dict) or not t.get("markPrice"):
        leg.errors.append("no mark price right now")
        return
    leg.mark = Decimal(str(t["markPrice"]))
    step, tick = Decimal(str(m.get("stepSize", "0"))), Decimal(str(m.get("tickSize", "0")))
    min_size = Decimal(str(m.get("minOrderSize", "0")))
    if step > 0 and not multiple_of(leg.size, step):
        leg.errors.append(f"size {leg.size} is not a multiple of stepSize {fp(step)}")
    if leg.size < min_size:
        leg.errors.append(f"size {leg.size} is below minOrderSize {fp(min_size)}")
    max_lev = int(m.get("maxLeverage") or 0)
    if max_lev and leg.leverage > max_lev:
        leg.errors.append(f"leverage {leg.leverage}x exceeds the market's {max_lev}x right now")
    leg.ref = leg.price if leg.entry == "limit" else leg.mark
    if leg.entry == "limit":
        if tick > 0 and not multiple_of(leg.price, tick):
            leg.errors.append(f"price {leg.price} is not a multiple of tickSize {fp(tick)}")
        dev = abs(float(leg.price - leg.mark) / float(leg.mark))
        if dev > PRICE_BAND:
            leg.errors.append(f"limit {fp(leg.price)} is {dev:.0%} from mark {fp(leg.mark)} — beyond the {PRICE_BAND:.0%} band, the venue rejects it")
    for name, v in (("takeProfit", leg.tp), ("stopLoss", leg.sl)):
        if tick > 0 and not multiple_of(v, tick):
            leg.warnings.append(f"{name} {v} is not on the {fp(tick)} tick")
    if leg.long and not (leg.sl < leg.ref < leg.tp):
        leg.errors.append(f"long needs stopLoss < entry < takeProfit (got {fp(leg.sl)} / {fp(leg.ref)} / {fp(leg.tp)})")
    if not leg.long and not (leg.tp < leg.ref < leg.sl):
        leg.errors.append(f"short needs takeProfit < entry < stopLoss (got {fp(leg.tp)} / {fp(leg.ref)} / {fp(leg.sl)})")
    leg.notional = float(leg.ref * leg.size)
    leg.margin = leg.notional / leg.leverage
    leg.bankrupt = float(leg.ref) * (1 - 1 / leg.leverage) if leg.long else float(leg.ref) * (1 + 1 / leg.leverage)
    leg.gain = abs(float(leg.tp - leg.ref)) * float(leg.size)
    leg.risk = abs(float(leg.ref - leg.sl)) * float(leg.size)
    if leg.risk > leg.margin:
        leg.warnings.append(f"stop {fp(leg.sl)} sits beyond the bankruptcy price {fp(leg.bankrupt)} — liquidation fires first; move the stop closer or lower the leverage")
    if m.get("sessionDegraded"):
        why = m.get("capReason") or "off-session pricing"
        if leg.leverage > DEGRADED_LEV or leg.notional > DEGRADED_NOTIONAL:
            leg.warnings.append(f"degraded session ({why}): new risk is capped at {DEGRADED_LEV}x and {DEGRADED_NOTIONAL:,.0f} USDC notional — this leg will be refused until the session is live")
        else:
            leg.warnings.append(f"degraded session ({why})")
    leg.body = {
        "market": leg.market, "side": leg.side, "entryType": leg.entry, "size": str(leg.size),
        "leverage": leg.leverage, "takeProfitPrice": str(leg.tp), "stopLossPrice": str(leg.sl),
    }
    if leg.entry == "limit":
        leg.body["entryPrice"] = str(leg.price)
    leg.path = "/api/v1/perp/orders/bracket"


# ─── payoff ──────────────────────────────────────────────────────────────────


def leg_pnl(leg, p):
    """PnL of one leg if the market ends at `p`: exits at TP or SL when reached,
    marked at `p` in between, never worse than the margin posted."""
    e, q, tp, sl = float(leg.ref), float(leg.size), float(leg.tp), float(leg.sl)
    if leg.long:
        v = (sl - e) * q if p <= sl else (tp - e) * q if p >= tp else (p - e) * q
    else:
        v = (e - sl) * q if p >= sl else (e - tp) * q if p <= tp else (e - p) * q
    return max(v, -leg.margin)


def basket_curve(legs):
    """Portfolio PnL when every market moves together by x (fraction), as
    (x, pnl) points at each leg's stop and target — piecewise linear between."""
    kinks = {0.0}
    for l in legs:
        ref = float(l.ref)
        kinks |= {float(l.tp) / ref - 1, float(l.sl) / ref - 1}
    lo, hi = min(kinks), max(kinks)
    pad = (hi - lo) * 0.12 or 0.01
    xs = sorted(kinks | {lo - pad, hi + pad})
    return [(x, sum(leg_pnl(l, float(l.ref) * (1 + x)) for l in legs)) for x in xs]


def read_account(base):
    key, secret, _ = api.resolve_credentials(base)
    acct = SimpleNamespace(key=key, secret=secret, id=None, available=None, problem=None)
    if not (key and secret):
        acct.problem = "not connected"
        return acct
    try:
        st, ov = get(base, "/api/v1/accounts/me/overview", key, secret)
    except PlanError as e:
        acct.problem = str(e)
        return acct
    if st in (401, 403):
        acct.problem = "key rejected — revoked or rotated on the web"
    elif st != 200 or not isinstance(ov, dict):
        acct.problem = f"overview HTTP {st}"
    else:
        acct.id = ov.get("accountId")
        try:
            acct.available = float(ov.get("availableBalance"))
        except (TypeError, ValueError):
            acct.available = None
    return acct


def build(base, plan, raw):
    legs = normalise(plan)
    for leg in legs:
        try:
            enrich(base, leg)
        except PlanError as e:
            leg.errors.append(str(e))
    model = SimpleNamespace(
        base=base, net=api.net_name(base), title=str(plan.get("title") or "Order plan"),
        thesis=str(plan.get("thesis") or ""), legs=legs, id=plan_id(raw),
        errors=[f"{l.tag} {l.market}: {e}" for l in legs for e in l.errors],
        warnings=[f"{l.tag} {l.market}: {w}" for l in legs for w in l.warnings],
    )
    for leg in legs:
        leg.cid = f"{model.id}_{leg.n}"
        if hasattr(leg, "body"):
            leg.body["clientOrderId"] = leg.cid
            leg.body_json = json.dumps(leg.body, separators=(",", ":"))
    if model.errors:
        return model
    model.best = sum(l.gain for l in legs)
    model.worst = -sum(min(l.risk, l.margin) for l in legs)
    model.capital = sum(l.margin for l in legs)
    model.curve = basket_curve(legs)
    model.account = read_account(base)
    a = model.account
    if a.available is not None and model.capital > a.available:
        model.warnings.append(f"this plan commits {fm(model.capital)} USDC but only {fm(a.available)} is available — the venue will refuse the legs it cannot margin")
    risks = [l.risk for l in legs]
    if len(risks) > 1 and max(risks) > 3 * min(risks):
        model.warnings.append(f"uneven risk: the largest leg risks {fm(max(risks))} at its stop, the smallest {fm(min(risks))} — size for equal loss at the stop, or say why not")
    return model


# ─── text ────────────────────────────────────────────────────────────────────


def leg_line(l):
    e = ("market " if l.entry == "market" else "limit ") + fp(l.ref)
    return f"  {l.side:<4} {fp(l.size):>8} {l.market:<11} {l.leverage:>2}x  {e:<18} TP {fp(l.tp):<10} SL {fp(l.sl)}"


def text_summary(m):
    lines = [f"PLAN  {m.title}  ·  {m.net}  ·  {len(m.legs)} leg{'s' if len(m.legs) != 1 else ''}"]
    lines += [leg_line(l) for l in m.legs]
    a = m.account
    if a.problem:
        avail = f" · {a.problem}, the button stays off"
    else:
        avail = f" ({m.capital / a.available:.0%} of {fm(a.available)} available)" if a.available else ""
    lines.append(f"MAX PROFIT {fm(m.best, True)}   MAX LOSS {fm(m.worst, True)}   MARGIN {fm(m.capital)}{avail}")
    lines.append("USDC, before fees and funding; a gap through a stop can cost up to the margin.")
    if m.warnings:
        lines += [f"  ! {w}" for w in m.warnings]
    return "\n".join(lines)


# ─── hosted page ─────────────────────────────────────────────────────────────


def document(m):
    """What `POST /api/v1/plans` stores and the page shows. The first nine leg
    fields are the bracket request itself; the rest are display numbers the
    page repeats as they are."""
    legs = []
    for l in m.legs:
        d = dict(l.body)
        d.update(referencePrice=str(l.ref), margin=round(l.margin, 2), maxProfit=round(l.gain, 2),
                 maxLoss=round(-min(l.risk, l.margin), 2))
        legs.append(d)
    doc = {"title": m.title, "legs": legs, "maxProfit": round(m.best, 2), "maxLoss": round(m.worst, 2),
           "margin": round(m.capital, 2), "curve": [[round(x, 5), round(v, 2)] for x, v in m.curve],
           "warnings": m.warnings}
    if m.thesis:
        doc["thesis"] = m.thesis
    if m.account.available is not None:
        doc["availableBalance"] = round(m.account.available, 2)
    return doc


def publish(m, ttl):
    """Upload the plan; returns (plan_id, expires_at_ms). Raises PlanError."""
    body = dict(document(m), ttlSeconds=ttl)
    st, txt = api.request(m.base, "POST", PLANS, json.dumps(body, separators=(",", ":")),
                          key=m.account.key, secret=m.account.secret)
    if st == 0:
        raise PlanError(f"could not reach {m.net}: {txt}")
    if st != 200:
        try:
            err = json.loads(txt).get("error") or {}
        except ValueError:
            err = {}
        raise PlanError(f"{PLANS} refused (HTTP {st}) {err.get('code', '')}: {err.get('message') or txt[:200]}")
    d = api.unwrap(txt) or {}
    if not d.get("planId"):
        raise PlanError(f"{PLANS} answered without a planId: {txt[:200]}")
    return d["planId"], int(d.get("expiresAt") or 0)


def plan_url(base, plan_id):
    # Built here, not read from the server: testnet's Public API advertises
    # the mainnet web origin (same gap api.py patches on the connect link).
    return f"{api.web_origin(base)}/plan/{plan_id}"


def fetch_plan(base, plan_id):
    st, view = get(base, f"{PLANS}/{plan_id}")
    if st == 404:
        raise PlanError(f"no plan {plan_id} on {api.net_name(base)} — wrong network, or a typo in the id")
    if st != 200 or not isinstance(view, dict):
        raise PlanError(f"{PLANS}/{plan_id}: HTTP {st}")
    return view


def wait_for_decision(base, plan_id, wait):
    """Read the plan until it is decided or `wait` seconds pass; returns the last view."""
    deadline = time.monotonic() + wait
    while True:
        view = fetch_plan(base, plan_id)
        if view.get("status") not in ("pending", "placing") or time.monotonic() >= deadline:
            return view
        time.sleep(max(0.0, min(POLL_S, deadline - time.monotonic())))


def report(base, plan_id, view):
    """Say where the plan stands; after Place, list each leg and read the account back.
    Returns the exit code."""
    net = api.net_name(base)
    status = view.get("status")
    legs = view.get("legs") or []
    if status == "placed":
        results = view.get("results") or []
        ok_all = True
        out("")
        out(f"Placed on {net} — {len(legs)} legs:")
        for r in results:
            leg = legs[r["n"] - 1] if 0 < r.get("n", 0) <= len(legs) else {}
            label = f"{leg.get('side', '?')} {fp(leg.get('size', 0))} {leg.get('market', '?')} {leg.get('leverage', '?')}x · TP {fp(leg.get('takeProfitPrice', 0))} / SL {fp(leg.get('stopLossPrice', 0))}"
            if r.get("status") == "accepted":
                replay = " (replayed an earlier order with the same clientOrderId — no second order)" if r.get("idempotentReplay") else ""
                out(f"  ✓ {label} — accepted: orderId {r.get('orderId', '?')} · status {r.get('orderStatus', '?')}{replay}")
            else:
                ok_all = False
                out(f"  ✕ {label} — refused: {r.get('errorCode', '?')}: {r.get('message', '')}")
        key, secret, _ = api.resolve_credentials(base)
        out("")
        if key and secret:
            time.sleep(1.0)  # let fills settle before reading the account
            try:
                rb = readback(SimpleNamespace(base=base, account=SimpleNamespace(key=key, secret=secret),
                                              legs=[SimpleNamespace(market=l.get("market", "")) for l in legs]))
            except PlanError as e:
                rb = f"(could not read the account back: {e})"
            out(f"Account now ({net}):")
            out("  " + rb.replace("\n", "\n  ") if rb else "  (nothing to show)")
        else:
            out(f"Not connected on {net}, so the account was not read back — /portfolio on the web shows it.")
        n_ok = sum(1 for r in results if r.get("status") == "accepted")
        out("")
        out(f"{n_ok} of {len(legs)} legs accepted." + ("" if ok_all else " Some legs were refused — see the list.")
            + " Accepted means on the exchange, not necessarily filled — the account lines above are what counts.")
        return 0 if ok_all else 6
    out("")
    if status == "cancelled":
        out("Cancelled on the page — nothing was sent.")
        return 5
    if status == "expired":
        out(f"The page expired at {api.fmt_ts(view.get('expiresAt'))} before a decision — nothing was sent. Re-run the preview to show it again.")
        return 4
    if status == "placing":
        out("The user clicked Place and the exchange is still working through the legs — run "
            f"`python3 scripts/plan.py status {plan_id}{' --testnet' if base == api.TESTNET else ''}` in a moment for the outcome.")
        return 4
    out(f"No decision yet — nothing was sent. The page stays open until {api.fmt_ts(view.get('expiresAt'))}; "
        f"check again with `python3 scripts/plan.py status {plan_id}{' --testnet' if base == api.TESTNET else ''}`.")
    return 4


# ─── execution ───────────────────────────────────────────────────────────────


def leg_label(l):
    return f"{l.side} {fp(l.size)} {l.market} {l.leverage}x · TP {fp(l.tp)} / SL {fp(l.sl)}"


def execute(m, progress):
    """Send every leg in plan order; returns True when all were accepted."""
    key, secret = m.account.key, m.account.secret
    ok_all = True
    for l in m.legs:
        l.result = ("sending", "")
        progress(l)
        st, txt = api.request(m.base, "POST", l.path, l.body_json, key=key, secret=secret, timeout=30)
        if st == 0:
            l.result = ("no answer", txt)
            ok_all = False
        elif 200 <= st < 300:
            d = api.unwrap(txt) or {}
            l.result = ("accepted", f"orderId {d.get('orderId', '?')} · status {d.get('status', '?')}")
        else:
            try:
                err = (json.loads(txt).get("error") or {})
            except ValueError:
                err = {}
            code = err.get("code") or f"HTTP {st}"
            l.result = ("refused", f"{code}: {err.get('message') or txt[:160]}")
            ok_all = False
        progress(l)
    return ok_all


def readback(m):
    """What the account shows now for the plan's markets — the only truth."""
    key, secret = m.account.key, m.account.secret
    markets = sorted({l.market for l in m.legs})
    st, pos = get(m.base, "/api/v1/perp/positions", key, secret)
    st2, br = get(m.base, "/api/v1/perp/orders/bracket", key, secret)
    pos = pos if isinstance(pos, list) else []
    br = br if isinstance(br, list) else []
    lines = []
    for mk in markets:
        ps = [p for p in pos if p.get("market") == mk]
        bs = [b for b in br if b.get("market") == mk]
        for p in ps:
            tp, sl = p.get("takeProfitPrice"), p.get("stopLossPrice")
            lines.append(f"{mk:<14} {p.get('side')} {fp(p.get('size', 0))} @ {fp(p.get('entryPrice', 0))}  mark {fp(p.get('markPrice', 0))}  "
                         f"TP {fp(tp) if tp else '—'}  SL {fp(sl) if sl else '—'}  ({p.get('tpSlStatus', '?')})")
        if not ps:
            if bs:
                lines.append(f"{mk:<14} no position — bracket {', '.join(b.get('orderId', '?') + ' ' + b.get('status', '') for b in bs)} (entry resting, exits arm on the fill)")
            else:
                lines.append(f"{mk:<14} NO position and NO bracket order — this leg did not land")
    return "\n".join(lines)


def run_and_report(m):
    """`execute`: send from here, read back, print. Returns the exit code."""
    def progress(l):
        status, detail = l.result
        if status != "sending":
            out(f"  {'✓' if status == 'accepted' else '✕'} {leg_label(l)} — {status}{': ' + detail if detail else ''}")

    out("")
    out(f"Sending {len(m.legs)} legs on {m.net}:")
    ok_all = execute(m, progress)
    time.sleep(1.0)  # let fills settle before reading the account
    try:
        rb = readback(m)
    except PlanError as e:
        rb = f"(could not read the account back: {e})"
    out("")
    out(f"Account now ({m.net}):")
    out("  " + rb.replace("\n", "\n  ") if rb else "  (nothing to show)")
    n_ok = sum(1 for l in m.legs if l.result and l.result[0] == "accepted")
    out("")
    out(f"{n_ok} of {len(m.legs)} legs accepted." + ("" if ok_all else " Some legs were refused — see the list.")
        + " Accepted means on the exchange, not necessarily filled — the account lines above are what counts.")
    return 0 if ok_all else 6


# ─── main ────────────────────────────────────────────────────────────────────


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2 or args[0] not in ("preview", "execute", "status"):
        sys.stderr.write(__doc__)
        return 2
    verb, target = args
    base = os.environ.get("API_1024_BASE") or api.MAINNET
    if "--testnet" in flags:
        base = api.TESTNET
    wait, ttl = (0 if verb == "status" else DEFAULT_WAIT), DEFAULT_TTL
    for f in flags:
        if f.startswith("--base="):
            base = f.split("=", 1)[1]
        if f.startswith("--wait="):
            wait = max(0, int(f.split("=", 1)[1]))
        if f.startswith("--ttl="):
            ttl = int(f.split("=", 1)[1])

    if verb == "status":
        try:
            view = wait_for_decision(base, target, wait)
        except PlanError as e:
            sys.stderr.write(f"{e}\n")
            return 1
        out(f"PLAN  {view.get('title', '?')}  ·  {api.net_name(base)}  ·  {len(view.get('legs') or [])} legs  ·  {view.get('status')}")
        out(f"Preview: {plan_url(base, target)}")
        return report(base, target, view)

    try:
        with open(target, "rb") as fh:
            raw = fh.read()
        plan = json.loads(raw.decode("utf-8"))
    except OSError as e:
        sys.stderr.write(f"cannot read {target}: {e}\n")
        return 2
    except ValueError as e:
        sys.stderr.write(f"{target} is not valid JSON: {e}\n")
        return 2
    try:
        m = build(base, plan, raw)
    except PlanError as e:
        sys.stderr.write(f"plan invalid: {e}\n")
        return 2
    if m.errors:
        sys.stderr.write("plan invalid — fix these and run again (nothing was sent):\n")
        for e in m.errors:
            sys.stderr.write(f"  - {e}\n")
        return 2
    out(text_summary(m))
    if m.account.problem:
        sys.stderr.write(f"\nnot {'sending' if verb == 'execute' else 'publishing'}: {m.account.problem}\n")
        return 3
    if verb == "execute":
        return run_and_report(m)

    try:
        plan_id, expires_at = publish(m, ttl)
    except PlanError as e:
        sys.stderr.write(f"\ncould not publish the preview: {e}\n")
        return 1
    url = plan_url(base, plan_id)
    out("")
    out(f"Preview: {url}")
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 — a sandbox with no browser must not turn into a crash
        opened = False
    out(("Opened in the browser here. " if opened else "")
        + "The link works on any device — phone included; the user signs in with their own 1024 login "
        f"there and clicks Place or Cancel. Open until {api.fmt_ts(expires_at)}. Nothing is sent until they click.")
    if wait:
        out(f"Waiting up to {wait} s for the click… (`plan.py status {plan_id}` reads it any time)")
    try:
        view = wait_for_decision(base, plan_id, wait)
    except PlanError as e:
        sys.stderr.write(f"\nlost the plan while waiting: {e}\n")
        return 1
    return report(base, plan_id, view)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped — the page stays open; `plan.py status <id>` says what happened.\n")
        sys.exit(130)
