#!/usr/bin/env python3
"""1024 order preview — validate and price a basket of perp entries, each with
its take-profit and stop-loss, draw it as one short page (the basket's max
profit, max loss and margin, one payoff curve, the exact requests), and
send it only when the user clicks the button on that page. Stdlib only;
every exchange call goes through api.py.

    python3 scripts/plan.py preview plan.json [--testnet] [--wait=300] [--no-serve] [--port=N]
    python3 scripts/plan.py execute plan.json [--testnet]

`preview` prints the summary the agent relays, writes the page, opens it in
the user's browser and serves it on 127.0.0.1 until the user clicks Place
(the orders go out, then the account is read back and printed) or Cancel,
or --wait seconds pass. `--no-serve` prints the summary and the page path
only — nothing is served, nothing can be sent. `execute` skips the page:
for a host with no browser, after the user confirmed the same summary in
the chat.

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
    preview   0 sent, every leg accepted · 6 sent, some leg refused (the
              output says which) · 5 cancelled on the page · 4 no click
              within --wait (nothing sent; re-run to show it again) ·
              2 plan invalid (reasons printed, nothing served) · 3 not
              connected (the page still shows; the button is off) · 1 error
    execute   0 / 6 / 2 / 3 / 1 as above
"""
import hashlib
import html
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402 — the bundled signed client; every call goes through it

MAX_LEGS = 12
PRICE_BAND = 0.15          # perp limit more than this from mark → TRADE_PRICE_DEVIATION
DEGRADED_LEV, DEGRADED_NOTIONAL = 2, 1000.0   # off-session caps on new perp risk
PAGE_GRACE = 8             # seconds the page is kept alive after the last state change


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


def commands(m):
    return [f"python3 scripts/api.py{' --testnet' if m.base == api.TESTNET else ''} POST {l.path} '{l.body_json}'" for l in m.legs]


# ─── page ────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&display=swap');
:root{--bg:#101010;--card:#0a0f15;--line:rgba(255,255,255,.09);--txt:#fff;--dim:rgba(255,255,255,.52);--faint:rgba(255,255,255,.4);--mint:#50d2c1;--cyan:#31e8ff;--violet:#aa8cff;--gold:#f6c76b;--up:#a9f2c4;--dn:#ff8a8a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 "Hanken Grotesk",system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased;padding:30px 20px 120px}
main{max-width:920px;margin:0 auto}.eyebrow{font-size:10px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;color:var(--mint)}
h1{font-size:30px;line-height:1.05;font-weight:600;letter-spacing:-.04em;margin:8px 0 6px}.thesis{color:var(--dim);font-size:14px;line-height:1.55;margin:0 0 20px;max-width:720px}
.card{position:relative;overflow:hidden;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:20px 22px 22px;margin-bottom:12px}
.card::before{content:"";position:absolute;left:0;right:0;top:0;height:1px;background:linear-gradient(90deg,transparent,var(--c,var(--mint)) 35%,transparent)}
.card::after{content:"";position:absolute;top:-80px;right:-60px;width:180px;height:180px;border-radius:50%;background:var(--c,var(--mint));opacity:.07;filter:blur(48px);pointer-events:none}
.lbl{display:block;font-size:10px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;color:var(--c,var(--mint));margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.kpi .l{font-size:10px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--faint)}
.kpi .v{font-size:28px;line-height:1.1;font-weight:600;letter-spacing:-.03em;font-variant-numeric:tabular-nums;margin-top:6px}.kpi .s{font-size:12px;line-height:1.45;color:var(--faint);margin-top:5px}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th{font-size:10px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);text-align:left;padding:0 12px 10px 0}
td{padding:9px 12px 9px 0;border-top:1px solid var(--line);white-space:nowrap}.tbl{overflow-x:auto}svg{width:100%;height:auto;display:block}
.warn{list-style:none;margin:0;padding:0}.warn li{display:flex;gap:10px;align-items:flex-start;font-size:12px;line-height:1.5;color:var(--dim);margin:6px 0}
.warn li::before{content:"!";flex:none;width:16px;height:16px;margin-top:1px;border-radius:50%;background:rgba(246,199,107,.16);color:var(--gold);font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center}
details{margin:4px 0 12px}summary{cursor:pointer;color:var(--faint);font-size:12px}pre{background:#070a0e;border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:10px 0 0;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);overflow-x:auto;white-space:pre}
.note{font-size:12px;line-height:1.5;color:var(--faint)}
.bar{position:fixed;left:0;right:0;bottom:0;background:rgba(16,16,16,.86);backdrop-filter:blur(14px);border-top:1px solid var(--line);padding:14px 20px}
.bar .in{max-width:920px;margin:0 auto;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button{font:inherit;font-weight:600;font-size:14px;min-height:46px;padding:0 24px;border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;transition:transform .15s,background .15s,border-color .15s}
button.go{background:var(--mint);color:#031311;border:0;box-shadow:0 0 34px rgba(80,210,193,.16)}button.go:hover:not(:disabled){background:#67e1d1;transform:translateY(-1px)}
button.alt{background:rgba(255,255,255,.035);color:#fff;border:1px solid rgba(255,255,255,.14)}button.alt:hover:not(:disabled){border-color:rgba(255,255,255,.25);background:rgba(255,255,255,.07)}
button:disabled{opacity:.4;cursor:default;transform:none}.st{color:var(--dim);font-size:13px;flex:1;min-width:220px}
.prog{list-style:none;margin:0;padding:0}.prog li{display:flex;align-items:center;gap:10px;font-size:13px;padding:9px 0;border-top:1px solid var(--line)}.prog li:first-child{border-top:0;padding-top:0}
.ic{flex:none;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;background:rgba(255,255,255,.06);color:var(--faint)}
.ok .ic{background:rgba(80,210,193,.16);color:var(--mint)}.bad .ic{background:rgba(255,138,138,.16);color:var(--dn)}.prog .d{margin-left:auto;font-size:12px;color:var(--faint);white-space:nowrap}
.up{color:var(--up)}.dn{color:var(--dn)}.small{font-size:12px;color:var(--faint)}
@media(max-width:700px){.kpi .v{font-size:24px}}@media(max-width:640px){body{padding:20px 16px 160px}h1{font-size:26px}.card{padding:18px;border-radius:20px}}@media(max-width:520px){.kpis{grid-template-columns:1fr;gap:14px}}
"""

JS = """
const T=document.body.dataset.token;const $=s=>document.querySelector(s);
const go=$('#go'),cancel=$('#cancel'),st=$('#st'),prog=$('#prog'),rb=$('#readback');
async function post(p){try{const r=await fetch('/'+T+'/'+p,{method:'POST',headers:{'X-1024-Plan':T,'Content-Type':'application/json'},body:'{}'});return r.ok}catch(e){return false}}
const cls=s=>s==='accepted'?'ok':s==='pending'||s==='sending'?'pend':'bad';
const ic=s=>s==='accepted'?'✓':s==='pending'?'·':s==='sending'?'…':'✕';
function render(s){
  if(s.phase==='running'||s.phase==='done'){prog.hidden=false;
    prog.querySelector('ul').innerHTML=s.legs.map(l=>`<li class="${cls(l.status)}"><span class="ic">${ic(l.status)}</span><span>${l.label}</span><span class="d">${l.detail||l.status}</span></li>`).join('')}
  if(s.phase==='done'){st.textContent=s.summary||'Done.';if(s.readback){rb.hidden=false;rb.querySelector('pre').textContent=s.readback}go.textContent='Sent';go.disabled=true;cancel.hidden=true}
  else if(s.phase==='running'){st.textContent='Sending…'}
  else if(s.phase==='cancelled'){st.textContent='Cancelled — nothing was sent. You can close this tab.';go.disabled=true;cancel.disabled=true}
  else if(s.phase==='expired'){st.textContent='This preview expired before a decision — nothing was sent. Ask your agent to show it again.';go.disabled=true;cancel.disabled=true}
}
async function poll(){let s;try{s=await(await fetch('/'+T+'/state')).json()}catch(e){st.textContent='The preview server is gone — if you clicked Place, your agent has the result.';go.disabled=true;cancel.disabled=true;return}
  render(s);if(!['done','cancelled','expired'].includes(s.phase))setTimeout(poll,1000)}
if(go){go.onclick=async()=>{go.disabled=true;cancel.disabled=true;st.textContent='Sending…';if(!await post('execute')){st.textContent='Could not reach the preview server.';return}}}
if(cancel){cancel.onclick=async()=>{cancel.disabled=true;go.disabled=true;await post('cancel')}}
poll();
"""


def svg_chart(m):
    w, h, ml, mr, mt, mb = 640, 170, 60, 14, 18, 26
    pts = m.curve
    x0, x1 = pts[0][0], pts[-1][0]
    ys = [v for _, v in pts] + [0.0]
    y0, y1 = min(ys), max(ys)
    pad = (y1 - y0) * 0.08 or 1.0
    y0, y1 = y0 - pad, y1 + pad
    sx = lambda x: ml + (x - x0) / (x1 - x0 or 1) * (w - ml - mr)
    sy = lambda v: mt + (y1 - v) / (y1 - y0 or 1) * (h - mt - mb)
    e = html.escape
    inner = [x for x, _ in pts[1:-1]]
    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="basket payoff">']
    zero = sy(0)
    parts.append(f'<line x1="{ml}" y1="{zero:.1f}" x2="{w - mr}" y2="{zero:.1f}" stroke="rgba(255,255,255,.12)" stroke-width="1"/>')
    parts.append(f'<text x="{ml - 6}" y="{sy(m.best) + 4:.1f}" font-size="10" fill="#a9f2c4" text-anchor="end">{e(fm(m.best, True))}</text>')
    parts.append(f'<text x="{ml - 6}" y="{sy(m.worst) + 4:.1f}" font-size="10" fill="#ff8a8a" text-anchor="end">{e(fm(m.worst, True))}</text>')
    parts.append(f'<text x="{ml - 6}" y="{zero + 4:.1f}" font-size="10" fill="rgba(255,255,255,.4)" text-anchor="end">0</text>')
    for x, label, color in ((min(inner), "all stops", "#ff8a8a"), (0.0, "now", "#31e8ff"), (max(inner), "all targets", "#a9f2c4")):
        parts.append(f'<line x1="{sx(x):.1f}" y1="{mt}" x2="{sx(x):.1f}" y2="{h - mb}" stroke="{color}" stroke-width="1" stroke-dasharray="3 3" opacity=".7"/>')
        parts.append(f'<text x="{sx(x):.1f}" y="{mt - 6}" font-size="10" fill="{color}" text-anchor="middle">{e(label)} {x:+.1%}</text>')
    parts.append(f'<polyline points="{" ".join(f"{sx(x):.1f},{sy(v):.1f}" for x, v in pts)}" fill="none" stroke="#50d2c1" stroke-width="2.2" stroke-linejoin="round"/>')
    parts.append(f'<text x="{(ml + w - mr) / 2:.0f}" y="{h - 8}" font-size="10" fill="rgba(255,255,255,.4)" text-anchor="middle">basket PnL when every market moves together — each leg exits at its own stop or target</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_page(m, token):
    e = html.escape
    a = m.account
    n = len(m.legs)
    pct = f" · {m.capital / a.available:.0%} of {fm(a.available)} available" if a.available else ""
    acct = e(a.id) if a.id else e(a.problem or "")
    rows = []
    for l in m.legs:
        entry = "market" if l.entry == "market" else f"limit {fp(l.price)}"
        rows.append(f"<tr><td>{e(l.side)}</td><td>{e(fp(l.size))}</td><td>{e(l.market)}</td><td>{l.leverage}x</td><td>{e(entry)} <span class='small'>({e(fp(l.ref))})</span></td>"
                    f"<td class='up'>{e(fp(l.tp))} <span class='small'>{e(fm(l.gain, True))}</span></td><td class='dn'>{e(fp(l.sl))} <span class='small'>{e(fm(-min(l.risk, l.margin), True))}</span></td><td>{e(fm(l.margin))}</td></tr>")
    warn = ("<section class='card' style='--c:#f6c76b'><span class='lbl'>Heads up</span><ul class='warn'>" + "".join(f"<li>{e(w)}</li>" for w in m.warnings) + "</ul></section>") if m.warnings else ""
    can = a.key and a.secret and not a.problem
    go = f'<button id="go" class="go"{"" if can else " disabled"}>Place {n} order{"s" if n != 1 else ""} on {e(m.net)} <span>→</span></button>'
    hint = "Clicking sends the requests below, in that order, with the key connected to this agent." if can else f"Not connected ({e(a.problem or '')}) — connect in the chat first, then ask for the preview again."
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(m.title)} · 1024 order preview</title><style>{CSS}</style></head>
<body data-token="{e(token)}"><main>
<div class="eyebrow">Order preview · {e(m.net)}</div>
<h1>{e(m.title)}</h1>
<p class="thesis">{e(m.thesis) if m.thesis else ''}{'<br>' if m.thesis else ''}<span class="small">{acct}</span></p>
<section class="card" style="--c:#50d2c1"><span class="lbl">01 / Basket</span><div class="kpis">
 <div class="kpi"><div class="l">Max profit</div><div class="v up">{e(fm(m.best, True))}</div><div class="s">every leg at its take-profit</div></div>
 <div class="kpi"><div class="l">Max loss</div><div class="v dn">{e(fm(m.worst, True))}</div><div class="s">every stop fills at its level</div></div>
 <div class="kpi"><div class="l">Margin posted</div><div class="v">{e(fm(m.capital))}</div><div class="s">the most a gap through every stop can cost{e(pct)}</div></div>
</div></section>
<section class="card" style="--c:#31e8ff"><span class="lbl">02 / Legs — {n} bracket order{'s' if n != 1 else ''}, entry + take-profit + stop-loss each</span>
<div class="tbl"><table><thead><tr><th>Side</th><th>Size</th><th>Market</th><th>Lev</th><th>Entry</th><th>Take-profit</th><th>Stop-loss</th><th>Margin</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class="card" style="--c:#aa8cff"><span class="lbl">03 / Payoff</span>{svg_chart(m)}</section>
{warn}
<details><summary>What the button sends</summary><pre>{e(chr(10).join(commands(m)))}</pre></details>
<p class="note">USDC, before fees and funding. Stops are market-triggered: a gap through a stop fills where the market is, not at the stop, up to the margin posted. Accepted ≠ filled: a limit entry rests until it trades; its exits arm on the fill.</p>
<section id="prog" class="card" style="--c:#50d2c1" hidden><span class="lbl">Sending</span><ul class="prog"></ul></section>
<section id="readback" class="card" style="--c:#31e8ff" hidden><span class="lbl">Account now</span><pre style="margin:0">{''}</pre></section>
</main>
<div class="bar"><div class="in">{go}<button id="cancel" class="alt">Cancel</button><span id="st" class="st">{hint}</span></div></div>
<script>{JS}</script></body></html>"""


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


def run_and_report(m, state=None):
    """Execute, read back, print. Returns the exit code."""
    lock = threading.Lock()
    glyph = {"accepted": "✓", "sending": "…"}

    def progress(l):
        status, detail = l.result
        if state is not None:
            with lock:
                for row in state["legs"]:
                    if row["n"] == l.n:
                        row["status"], row["detail"] = status, detail
        if status != "sending":
            out(f"  {glyph.get(status, '✕')} {leg_label(l)} — {status}{': ' + detail if detail else ''}")

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
    summary = f"{n_ok} of {len(m.legs)} legs accepted." + ("" if ok_all else " Some legs were refused — see the list.")
    if state is not None:
        with lock:
            state["readback"] = rb
            state["summary"] = summary
            state["phase"] = "done"
    out("")
    out(summary + " Accepted means on the exchange, not necessarily filled — the account lines above are what counts.")
    return 0 if ok_all else 6


# ─── server ──────────────────────────────────────────────────────────────────


def serve(m, page, token, wait, open_browser, port):
    state = {"phase": "waiting", "legs": [{"n": l.n, "label": leg_label(l), "status": "pending", "detail": ""} for l in m.legs],
             "readback": None, "summary": None}
    lock = threading.Lock()
    decided = threading.Event()
    exit_code = [4]
    can_send = bool(m.account.key and m.account.secret and not m.account.problem)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _route(self):
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            if not parts or parts[0] != token:
                return None
            return "/".join(parts[1:])

        def _same_origin(self):
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and origin != f"http://{host}":
                return False
            return self.headers.get("X-1024-Plan") == token

        def do_GET(self):
            r = self._route()
            if r is None:
                return self._send(404, "not found", "text/plain")
            if r == "":
                return self._send(200, page, "text/html")
            if r == "state":
                with lock:
                    return self._send(200, json.dumps(state))
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            r = self._route()
            if r is None or not self._same_origin():
                return self._send(403, "forbidden", "text/plain")
            with lock:
                phase = state["phase"]
            if r == "execute":
                if not can_send:
                    return self._send(409, "not connected", "text/plain")
                if phase != "waiting":
                    return self._send(409, phase, "text/plain")
                with lock:
                    state["phase"] = "running"
                exit_code[0] = None
                decided.set()
                return self._send(202, "{}")
            if r == "cancel":
                if phase == "waiting":
                    with lock:
                        state["phase"] = "cancelled"
                    exit_code[0] = 5
                    decided.set()
                return self._send(200, "{}")
            self._send(404, "not found", "text/plain")

        def do_OPTIONS(self):
            self._send(403, "forbidden", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{srv.server_address[1]}/{token}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out("")
    out(f"Preview: {url}")
    opened = open_browser and webbrowser.open(url)
    if opened:
        out(f"Opened in the browser. Waiting up to {wait} s for Place or Cancel — nothing is sent until then.")
    else:
        out(f"Open that link (a browser tool works too — it is local to this machine). Waiting up to {wait} s for Place or Cancel — nothing is sent until then.")
    if not can_send:
        out("The Place button is off: " + (m.account.problem or "not connected") + ".")
    try:
        if not decided.wait(wait):
            with lock:
                state["phase"] = "expired"
            out("")
            out(f"No decision within {wait} s — nothing was sent. Re-run the preview to show it again.")
            time.sleep(min(PAGE_GRACE, 3))
            return 4
        if exit_code[0] == 5:
            out("")
            out("Cancelled on the page — nothing was sent.")
            time.sleep(min(PAGE_GRACE, 3))
            return 5
        code = run_and_report(m, state)
        time.sleep(PAGE_GRACE)  # the page polls once more to show the outcome
        return code
    finally:
        srv.shutdown()


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
    if len(args) != 2 or args[0] not in ("preview", "execute"):
        sys.stderr.write(__doc__)
        return 2
    verb, path = args
    base = os.environ.get("API_1024_BASE") or api.MAINNET
    if "--testnet" in flags:
        base = api.TESTNET
    wait, port = 300, 0
    for f in flags:
        if f.startswith("--base="):
            base = f.split("=", 1)[1]
        if f.startswith("--wait="):
            wait = max(10, int(f.split("=", 1)[1]))
        if f.startswith("--port="):
            port = int(f.split("=", 1)[1])
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        plan = json.loads(raw.decode("utf-8"))
    except OSError as e:
        sys.stderr.write(f"cannot read {path}: {e}\n")
        return 2
    except ValueError as e:
        sys.stderr.write(f"{path} is not valid JSON: {e}\n")
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
    if verb == "execute":
        if m.account.problem:
            sys.stderr.write(f"\nnot sending: {m.account.problem}\n")
            return 3
        return run_and_report(m)
    token = secrets.token_urlsafe(24)
    page = render_page(m, token)
    if "--no-serve" in flags:
        dest = os.path.join(os.path.dirname(os.path.abspath(path)), f"{os.path.splitext(os.path.basename(path))[0]}.preview.html")
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(page)
        out("")
        out(f"Page written to {dest} (not served — its button cannot send anything).")
        return 0 if not m.account.problem else 3
    return serve(m, page, token, wait, "--no-open" not in flags, port)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped — if the page said Sending, read the account before assuming anything.\n")
        sys.exit(130)
