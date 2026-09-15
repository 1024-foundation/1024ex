---
name: 1024ex
metadata:
  version: 2026.09.14.6
  updated: 2026-09-14
description: Trade on 1024 Exchange via its public HTTP API — perpetuals and prediction markets. Onboarding, HMAC-signed orders, positions, balances and treasury; multi-market baskets with a take-profit and stop-loss on every leg, shown on a preview page (max profit, max loss, one button that sends). Use when the user asks to trade, quote, monitor, or automate anything on 1024 / 1024ex.com, or when they want to connect or log in to their 1024 account.
---

# 1024 Exchange trading

You are operating a real exchange account. Every authenticated call moves or
risks real funds. Before placing, cancelling-all or transferring, restate
what you are about to do (market, side, size, price, amount) and get the
user's explicit confirmation. Never trade unprompted. For perp entries
that confirmation is the **Place** button on the preview page
(`scripts/plan.py`, below): a page on 1024ex.com the user can open on
any device, showing every leg, the max profit, the max loss and the
exact requests. Nothing is sent until they click there.

## Getting connected

Anything authenticated needs credentials: env `API_1024_KEY` +
`API_1024_SECRET`, else this network's entry in
`~/.1024ex/credentials.json`. Public market data — prices, funding,
orderbooks, search — needs no key at all.

**"Am I connected?", "check my login", or anything that needs a key: run
`status` first.** It mints nothing and answers in a few lines you can
read out as they are:

```bash
python3 scripts/api.py status     # exit 0 connected · 3 not connected · 2 key rejected
```

Paths in this file are relative to the skill's own directory (the one
this SKILL.md sits in — from a project root that is
`./.agents/skills/1024ex/`). `status` only reads from the exchange; the
one thing it writes is a small local cache for the version check.

Connected: account, key label, permissions, since when and from which
credential file, last use, balance, and the links the user can open
themselves (next section). The `1024_…` prefix it prints is the key's
public identifier — the same mask the web shows on /connect so the user
can match the row — never the secret. Not connected: it says
so — and if a link is already waiting for approval it repeats **that**
link with its remaining minutes instead of letting you mint a new one.
Rejected (exit 2): the key was revoked or rotated, most likely by the
user on the web — say that plainly and offer to reconnect. Web login and
this connection are separate things: the user can be signed in to
1024ex.com while you are not connected, and vice versa; when they ask
"am I logged in", answer about this connection and hand them the link.

Nothing on hand? Say so and offer to connect, in one line, rather than
letting the user discover it through a 401. But **offer — do not run
`connect` on your own initiative.** It mints a real API key with trade
permission on a real account; that is the user's decision to make, not
an install side effect, and an unprompted credential flow is worth
refusing no matter who asked for it.

Once they agree — or when they ask for something that plainly needs a
key, which makes connecting the first step of what they already asked
for:

```bash
python3 scripts/api.py connect --label="Claude Code"
```

- **`--label` is the name the user will see on 1024's Connected AIs
  page** — pass your own product name (`"Claude Code"`, `"Cursor"`,
  `"Codex"`). Omitted, the script guesses from the environment.
- **Say "open the link and approve" — never promise a wallet signature.**
  Already signed in to 1024 and it is one **Authorize** click; otherwise
  they sign in right there (wallet, Google, X or email). Surface the link
  prominently and let the page speak for itself.
- The command polls until approved, then writes key + secret to disk. The
  secret never passes through the chat — never ask for it, never print it.
- `connect` is idempotent: already connected → it says so and mints
  nothing. `--force` mints a second key; `disconnect` revokes this one
  and forgets it locally.
- "still pending" (exit 4): re-run it — same session, same link, valid
  ~15 min. A 429 on create is IP rate limiting: wait a minute.
- The key carries read + trade. Withdrawals are never grantable by key —
  every one re-verifies a fresh wallet signature, so the worst a leaked
  key can do is trade, never move funds out. Withdrawing is a web action:
  send the user to https://www.1024ex.com/portfolio.

## Links: hand them over, every time

The user does one thing in this whole flow: open a link. So the link is
the message, not a detail of it.

- **Every 1024 link on its own line, complete and clickable.** Never
  "see above", never shortened, never retyped — only what `api.py`
  printed (the host is only ever `www.1024ex.com` or
  `testnet.1024ex.com`).
- **A deposit address is the one thing you relay that is not a 1024
  link** — a Tron, Bitcoin, EVM or Solana address exactly as
  `deposit --token` printed it, alone on its own line in the block
  between its ✂ lines. Copied, never retyped, shortened or "fixed",
  never from memory or an earlier message. It always travels
  with its verify link, and that link is on `www.1024ex.com` or
  `testnet.1024ex.com` — never relay an address without it, or with a
  link to anywhere else.
- **One sentence before it says what they do** ("open it, one click on
  Authorize"); **one sentence after says what you do next** ("I'll wait
  here and check your balance once it's through").
- **Asked again while waiting? Repeat the same link** and the minutes it
  has left. Expired? Give the new one — never send them to scroll back.
- **Every status, balance or position report ends with the links the
  user can open themselves**, so they never have to ask you to look:
  - `https://www.1024ex.com/connect` — signed in there, they see every
    AI connected to the account and can revoke any, including you
  - `https://www.1024ex.com/deposit` — funding, minimum 5 USDC
    (`api.py deposit` makes one addressed to this account; money on an
    exchange or another chain gets a deposit address right in the chat —
    both below)
  - `https://www.1024ex.com/portfolio` — balances, positions, and where
    they withdraw
- **Frame the links as their control, not your task**: "this is where you
  see who is connected and switch any of us off" is the reason they will
  actually open it.
- A 401 out of nowhere on a key that used to work means they revoked it
  on that page. Say so; do not silently reconnect.

Just installed, nothing asked yet? End with a handful of concrete things
the user could say next — their words, not commands to run — leading with
what needs no key, so their first impression is not a login wall:

- "What's BTC trading at?" — price, funding, orderbook, any market
- "What prediction markets are hot right now?"
- "Build me a basket around NVDA with stops and show me the preview" —
  the priced summary works before connecting; publishing the page needs
  the account connected
- "Connect my 1024 account" — required for positions, balances, orders
- "How do I fund my account?" — a deposit link, or an address right here
  in the chat for money on an exchange; once connected

## Setup

`scripts/api.py` looks for credentials in env first, then in
`~/.1024ex/credentials.json` (written by `connect`, keyed per network):

```
API_1024_KEY     1024_<64-hex>
API_1024_SECRET  64-hex (delivered exactly once at issuance)
API_1024_BASE    optional override; default mainnet
```

Users may instead paste an existing key and secret into the chat — that
is expected, accept them. Set the two env vars for whatever shell runs
`api.py`, then verify with a signed call such as
`GET /api/v1/accounts/me/overview`. Handle the paste with care: never
echo the secret back, and never write it into anything that gets
committed or logged. Manual keys live in Settings — log in at
https://www.1024ex.com and open https://www.1024ex.com/settings (secret
shown exactly once, at creation).

Or testnet — for testing functions without real money: run
`connect --testnet`, or create a key at https://testnet.1024ex.com/settings.
Accounts are separate from mainnet, and a `--testnet` call needs a
testnet-minted credential. A testnet account starts
empty; fund it with one signed call (no browser, no bridge):

```bash
python3 scripts/api.py --testnet GET  /api/v1/testnet/faucet/status
python3 scripts/api.py --testnet POST /api/v1/testnet/faucet/claim '{}'
```

Credits itself only — there is no wallet parameter. Needs a main-account
key with `canTrade`. Crediting is async (~30-40s): poll
`/api/v1/accounts/me/overview` before sizing an order. Details:
https://www.1024ex.com/skills/raw/00-quickstart/claim-testnet-usdc.md

Empty account? First ask **where the money is now**, then take one path:

- **USDC already in their login wallet on Base, Ethereum or Solana** →
  the link — cheapest, no conversion:

  ```bash
  python3 scripts/api.py deposit --amount=100          # prints the link
  python3 scripts/api.py deposit --amount=100 --wait   # …and waits until it is credited
  ```

- **On an exchange, or on another chain** (USDT on Tron, BTC, ETH, USDC
  elsewhere) → a deposit address made for this account, right here in
  the chat. First ask **what they will send** (if they haven't said),
  **which network** they will withdraw on — the exchange's withdrawal
  screen names it; pass their words (`TRC20`, `ERC20`, `BEP20`,
  `Arbitrum One`…) and 1024 resolves them — and **how much**. The amount
  is required: 1024 checks it against the minimum before they send. Not
  sure which network? Suggest a low-fee one their exchange offers
  (`deposit --routes` lists what works), and always say that Tron has no
  auto-refund. Then two steps:

  ```bash
  python3 scripts/api.py deposit --token=USDT --chain=TRC20 --amount=300   # 1. mint: address, QR, facts, verify link
  python3 scripts/api.py deposit --status=<requestId> --wait              # 2. after relaying, while they send
  ```

  Relay rules:
  - Mint **once** — every run makes a new address — and the mint never
    waits: relay first, then run step 2.
  - Relay the block between its ✂ lines: the address alone on its own
    line, the QR in its code fence and the verify link, all copied
    exactly — never retyped, shortened or reformatted. For a user writing
    in another language, put the heading and the facts in their
    language, keeping the token, network and numbers as printed.
  - Always include the verify link: signed in, the user sees on 1024 that
    the address is theirs before sending.
  - Never drop the facts — they are the ways to lose money: only that
    token on that network, the amount they named (the minimum counts
    what arrives after conversion), and on Tron no auto-refund.
  - Step 2 follows the deposit for up to 540 s, requests included —
    longer than many hosts let a tool call run by default (Claude Code:
    2 minutes). Give it your longest timeout (Claude Code: Bash
    `timeout: 600000`), or run it in the background and read its output,
    or pass `--wait=<seconds>` under your limit. Its first line is the
    resume command, in case the call is cut off.
  - The USDC lands in their own wallet first. Step 2 stops right then
    (exit 4) with a one-tap finish link that moves it into 1024: hand it
    over at once, then run the `--finish-sent` line it prints to follow
    the move-in. Other exits: 0 in 1024 · 4 not yet (relay any link it
    printed, run the line it prints) · 5 refunded, failed or not this
    account's address · 3 no answer, or switched off (the verify link
    still shows it).
  - No address on this deployment (not enabled yet, or switched off)?
    The output is the web link instead; relay that.
- **No crypto** → the link: card or bank transfer on the page.

The link opens https://www.1024ex.com/deposit — a standalone page, not a
dialog: the user signs in there if needed and lands straight back on it.
It names you ("Requested by Claude Code"), prefills the amount, and
carries the tail of this account's wallet, so a user signed in to a
different account is warned before paying into one you cannot see. By
hand it is `/deposit?amount=100&chain=base&from=<your name>&to=<last 6
characters of the wallet>`, every parameter optional. Minimum is 5 USDC by
link; an address prints its own minimum. Crediting is async — use
`--wait`, or re-check `GET /api/v1/accounts/me/overview`. Testnet: the
same page at https://testnet.1024ex.com/deposit has a one-click test-USDC
button — use that for test money, never a deposit address.

## Making calls

Use the bundled client for EVERY call — do not hand-roll HMAC in shell;
byte-exact body signing is the #1 cause of 401s:

```bash
python3 scripts/api.py GET /api/v1/system/time
python3 scripts/api.py GET '/api/v1/perp/positions'
python3 scripts/api.py POST /api/v1/perp/orders \
  '{"market":"BTC-USDC","side":"buy","type":"limit","price":"65000","size":"0.01","leverage":5,"clientOrderId":"cc_a7f3"}'
python3 scripts/api.py --testnet DELETE /api/v1/prediction/orders/cancel \
  '{"marketId":"1998","orderId":"312001"}'
```

Every response uses one envelope: `{success, data, error, meta}`. Branch on
`error.code` (stable string), never on `message`.

## Endpoint quick reference

Covers everyday querying and order flow with zero doc fetches. Public
market data needs no key:

```text
GET /api/v1/prediction/search/unified?q=&limit=  keyword → perps + PM events + PM markets
GET /api/v1/perp/markets                       tick/step, max leverage per market
GET /api/v1/perp/markets/{m}/ticker            also /orderbook /klines /trades
GET /api/v1/prediction/markets/active          also /trending /markets/{id}
GET /api/v1/prediction/markets/{id}/orderbook  also /depth /price-history
```

**Start every "which market?" from unified search** — it is the only
keyword lookup that spans both products, so one call turns "bitcoin" into
a perp symbol AND the prediction markets on it. `limit` is per group
(pass it explicitly; default varies by build, cap 25); the three groups
are always present, empty when nothing matched. Response:

```json
{"query":"bitcoin",
 "perps":[{"symbol":"BTC-USDC","maxLeverage":100,"status":"active","…":"…"}],
 "collections":[{"collectionId":887,"name":"Bitcoin above ___ on August 4?","marketCount":8,"…":"…"}],
 "markets":[{"marketId":35737,"question":"When will Bitcoin hit 150k?",
             "marketType":"binary","yesPriceE6":36000,"endTime":"…","…":"…"}]}
```

`yesPriceE6` is meaningful for `binary` only; for `multi_outcome` read
per-outcome prices from `/api/v1/prediction/markets/{id}/outcomes`.

Account and trading (HMAC — always via `scripts/api.py`):

```text
GET    /api/v1/accounts/me/overview            equity + balances across products
GET    /api/v1/accounts/me/api-key/introspect  whoami — label, permissions, last use (what `status` reads)
GET    /api/v1/perp/positions                  open positions
GET    /api/v1/perp/orders                     open orders; filled/cancelled: /orders/history
POST   /api/v1/perp/orders                     place (body fields: see example above)
POST   /api/v1/perp/orders/bracket             entry + TP + SL in one call — what plan.py sends for a perp leg
DELETE /api/v1/perp/orders/{id}                per market: DELETE /orders/cancel-all + {"market":…}
GET    /api/v1/prediction/me/positions         also /me/orders /me/trades
POST   /api/v1/prediction/orders               binary; multi-outcome: /multi-outcome/orders
DELETE /api/v1/prediction/orders/cancel        body {"marketId":…,"orderId":…}
POST   /api/v1/testnet/faucet/claim            testnet only; body {"amountE6"?:…}
```

Prediction order bodies use **numeric enums and different field names** from
perp — this shape, not the perp one:

```json
{"marketId":"1998","side":0,"outcomeIndex":0,"priceE6":650000,
 "amount":100,"orderType":0,"clientOrderId":"agent-42-a"}
```

`side` 0=buy 1=sell · `outcomeIndex` 0=Yes 1=No · `amount` is a share count,
not a dollar size. Sending perp-style `{"side":"buy","size":…}` fails with
400 `REQ_INVALID_JSON`.

**Symbol formats differ**: perp is `BTC-USDC` (dash), prediction takes a
numeric `marketId`. Anything beyond this table (funding, TP/SL, advanced
orders, treasury) → Canonical docs below.

## Suggest the basket and the exits — never impose them

The order is the user's. When they ask to open perp exposure, answer with
the plan for exactly what they asked and, in the same message, offer the
two things that make it survivable — as one question with the numbers
already filled in, never as a menu, never done silently:

- **Exits.** Propose one stop and one target: the stop beyond the recent
  range (`high24h`/`low24h` on the ticker, or a few 1h klines), the target
  at least 1.5× the stop distance away, each with its USDC outcome. Ask
  whether those levels work. Every perp entry carries both (Rules below);
  what the user decides is *where*, not *whether* — and if they want no
  stop at all, do not place the entry: say the level you would have used
  and leave it there.
- **Company.** Name one to three markets the same driver moves — sector
  peers for an equity (`NVDA` → `AMD`, `AVGO`, `SMH`), the majors for
  crypto (`BTC` → `ETH`, `SOL`) — each sized to risk about the same USDC
  at its stop as the leg they asked for (`|entry − stop| × size`), and ask
  whether to add any. A "no" is final: one leg, bracketed.

One message, one question, concrete numbers: *"Buy 10 NVDA at market —
stop 200 (−123), target 235 (+228). Do those levels work, and do you want
AMD and AVGO alongside at the same risk, about 120 each?"* Then build the
plan from their answer and show it. Never widen it on your own, and never
re-ask once they have answered. Prediction markets are not part of a plan;
place them as before.

**Write the plan, publish the page, let the click send it.** One JSON
file, every number a decimal string:

```json
{"title": "Long semis",
 "thesis": "AI capex intact; NVDA and AMD long into earnings, stops under last week's lows",
 "legs": [
   {"market": "NVDA-USDC", "side": "buy", "size": "10", "leverage": 3,
    "entry": "market", "takeProfit": "235", "stopLoss": "200"},
   {"market": "AMD-USDC", "side": "buy", "size": "4", "leverage": 3,
    "entry": "limit", "price": "490", "takeProfit": "540", "stopLoss": "465"}
 ]}
```

```bash
python3 scripts/plan.py preview plan.json          # validates, prices, prints the summary, publishes the page, waits for the click
python3 scripts/plan.py status  pl_…               # where a published plan stands — any time, from any run
```

`preview` checks every leg against the live market (step, tick, price
band, leverage, session, TP/SL on the right side of entry), computes
**max profit** (every leg at its take-profit) and **max loss** (every
stop fills at its level — a gap through a stop can cost up to the
margin posted, and the page says so), prints that summary, then
publishes the page with this key (`POST /api/v1/plans`) and prints its
link: `https://www.1024ex.com/plan/pl_…` (testnet: `testnet.1024ex.com`).
The page is served by 1024ex.com, **not by the machine running the
script** — it opens on the user's phone, from a sandboxed host,
anywhere. There the user signs in with their own 1024 login (the same
account this key is connected to) and clicks Place; the exchange then
sends one bracket order per leg, in plan order. This key never leaves
here and the page holds nothing that can trade without that login.
- **Relay the printed summary** — the `MAX PROFIT / MAX LOSS / MARGIN`
  line, each leg, the warnings — **and the preview link on its own
  line.** Say it works on any device. Do not ask for a second
  confirmation in the chat: the click is the confirmation.
- **The command waits for the click up to `--wait` seconds** (default
  300) and then reports; the page itself stays open for `--ttl` seconds
  (default 900) regardless. If the host cuts the call short, or the user
  needs longer, nothing is lost: `plan.py status pl_…` (the id is in the
  link) reads where it got to and prints the same report. `--wait=0`
  publishes and returns at once.
- **Read the exit code, then report what it printed under "Account
  now"** — the positions and resting brackets the exchange holds, leg
  by leg, which is the only thing that counts. 0 = every leg accepted
  · 6 = some leg refused (say which, from the list) · 5 = cancelled on
  the page, nothing sent · 4 = no decision yet — the output says whether
  the page is still open (check again with `status`) or expired (offer
  to show it again) · 2 = plan invalid, the reasons are printed: fix the
  file and re-run · 3 = not connected — the summary printed, nothing was
  published; connect first.
- Warnings come from the live market and are not blockers, but the
  user should hear them: degraded-session caps, uneven risk across
  legs, a stop beyond the bankruptcy price.
- `plan.py execute plan.json` sends from here without the page. Only
  for a user who has no browser on any device, and only after they
  confirmed the same printed summary in the chat.
- The `clientOrderId`s derive from the file's content and the UTC
  date, so re-running the same file the same day retries rather than
  doubles; edit the file for a genuinely new plan.

## Confirm every order against the account

A 2xx on `POST /orders` means accepted, not filled — the account is the
only thing that settles the question. Read the position BEFORE placing, so
you have something to compare against, then read it back after:

```bash
python3 scripts/api.py GET '/api/v1/perp/positions'    # a fill lands here
python3 scripts/api.py GET '/api/v1/perp/orders'       # an unfilled limit rests here
python3 scripts/api.py GET '/api/v1/perp/orders/history?market=BTC-USDC&limit=5'
```

A market order is never in `/orders` — filled, rejected and IOC-expired
orders only exist in `/orders/history`, so check both. Prediction is the
same drill on `/api/v1/prediction/me/positions?marketId=…` and
`/api/v1/prediction/me/orders?marketId=…&limit=50` (that one carries every
status, filled included; PM share counts are `sharesE6` micro-units).

Then report what the account actually shows — the new position size and
entry **plus the two exit levels now attached to it**, or "resting on the
book, nothing filled yet". **A position that did not move and no order row
means the order did NOT land: say that, never "order placed".** Same after
a close or a cancel — the position must really be gone (or smaller), the
order really out of `/orders`.

## Rules that prevent losses

- **Every perp position carries a take-profit AND a stop-loss. Both, or
  no entry.** Decide the two levels before you place anything. Enter with
  the `bracket` advanced order when you can — entry, TP and SL in one
  signed call, so no window exists where the position is bare; the preview
  page sends nothing else for a perp leg — otherwise attach them in the
  same turn the entry fills, before you report it:
  `POST /api/v1/perp/positions/{market}/tpsl {"take_profit_price": "…",
  "stop_loss_price": "…"}`. The endpoint accepts one side alone; one side
  alone is not protection, so send both. Then read the position back and
  confirm `takeProfit` and `stopLoss` are both non-null. Two exemptions,
  and only these: an order that reduces or closes an existing position,
  and a side already covered by a live bracket/OCO leg — that one answers
  `POS_TPSL_EXISTS` (14005) and is already protected, so leave it alone
  rather than firing `replace: true` at it. If the user will not name a
  stop, do not place the entry; tell them the level you would have used
  and let them decide. Prediction positions have no equivalent engine —
  say so plainly instead of implying an exit is attached.
- **Omitting `leverage` on a perp order uses your per-market preference,
  else 20x** (clamped to the market max; before 2026-08-29 it meant the
  market MAXIMUM). Set it explicitly on every order anyway — the response
  echoes the value applied.
- **`price`/`size` are JSON strings, and the TPSL / batch-cancel / leverage
  bodies are snake_case** (`take_profit_price`, `order_ids`,
  `position_side`). A camelCase key there is silently ignored and the call
  still returns 200 — TPSL comes back `takeProfit: null`, batch cancel
  `cancelledCount: 0`. Read the position / count back after each call.
- **Off-session markets cap new risk at 2x leverage and 1,000 USDC
  notional** — armed when a market's price is on the fallback feed or its
  content has gone stale (equity perps outside their session). Over
  either: 400 `REQ_INVALID_PARAMS` on `leverage` or `qty`. It applies to
  the 11 advanced order types as well, measured on the algo's total size,
  and reduce-only is exempt only when a real position backs it.
- **Prediction `priceE6` lives on a 0.1¢ grid**: a multiple of 1000,
  inside [1000, 999000]. Off-grid → 400 `REQ_INVALID_PRICE`.
- **Prediction routes are typed**: binary markets on
  `/api/v1/prediction/orders`, multi-outcome on
  `/api/v1/prediction/multi-outcome/orders`; a mismatch is rejected.
  Cancels are unified — and they are DELETE **with a JSON body** (the
  client signs it correctly).
- **Perp orders are rejected past 15% from mark/index**, and this fires
  BEFORE the balance check — a far-from-market resting limit comes back
  400 `TRADE_PRICE_DEVIATION`, not "insufficient funds". Quote inside the
  band or use a market order.
- **Prediction BUY orders need ~1 USDC notional** (`amount x priceE6`, with a
  one-share tolerance). Below it: 400 `REQ_INVALID_PARAMS` on `amount`.
  Sells have no minimum — a dust position can always be closed.
- Always pass `clientOrderId` (1-64 chars `[A-Za-z0-9_-]`) so retries are
  idempotent instead of duplicate orders.
- Keys default to `canTrade: false` and can carry market allowlists — a
  403 `PERM_*` means the key, not the request.

## Canonical docs

This file is a wrapper; the skill manual is the product. Before first use
of an area beyond the quick reference (funding, TP/SL, advanced orders,
treasury…), fetch the relevant note as raw markdown from
https://www.1024ex.com/skills/raw/<path>.md — pick `<path>` straight from
the directory below, no index fetch needed. Complete corpus:
https://www.1024ex.com/llms-full.txt · index: https://www.1024ex.com/llms.txt
· graph: https://www.1024ex.com/skills.json

<!-- note-directory:begin — generated by scripts/gen-skill-index.mjs; do not edit by hand -->
- 00-quickstart/claim-testnet-usdc — POST /testnet/faucet/claim — fund your own testnet account with one signed call, no browser, no source-chain deposit.
- 00-quickstart/sign-requests — HMAC-SHA256 request signing — the three headers every authenticated call must carry.
- 10-discover/funding-and-prices — Funding rate current/list/history, mark & index price, open interest, insurance fund. Public.
- 10-discover/options-chain — Options discovery — catalog, contract detail, expiries, the priced chain with Greeks, book and tape. Public, no auth. Every number is an e6 integer, and every "not found" answers empty instead of 404.
- 10-discover/perp-markets — Perp market discovery — list, detail, ticker, orderbook, trades, klines. Public, no auth.
- 10-discover/prediction-collections — Event groupings — one tournament or series is a collection of related binary/multi-outcome markets.
- 10-discover/prediction-discovery — Find markets from a keyword — unified search (perps + collections + markets) plus the filtered PM list, shelves, categories and tags.
- 10-discover/prediction-market-data — Per-market PM data — detail, outcomes, orderbook/depth with the LP virtual ladder, prices, klines, trades, media.
- 10-discover/watchlists — Cross-product watchlists — perp/PM items with stance; share, clone, community. Same lists the web app shows.
- 20-trade/advanced-orders — 11 perp algo order types — conditional, twap, vwap, scale, oco, bracket, iceberg, pegged, pov, trailing-stop, sniper.
- 20-trade/close-position — Close a perp position full or partial. Market by default; type=limit is an IOC at your price — no_fill leaves the position untouched and rests nothing.
- 20-trade/leverage-and-margin — Read/set per-market leverage and add/remove position margin. Request bodies are snake_case here.
- 20-trade/manage-orders — List, fetch, cancel perp orders — single, batch of 50, or cancel-all. DELETEs carry JSON bodies.
- 20-trade/mint-redeem-claim — USDC to complete-set mint, redeem, claim winnings/refunds, settlement sweep — binary and multi-outcome.
- 20-trade/options-orders — Place, list and cancel options orders. e6 integers not decimal strings, `orderType` not `type`, `clientOrderId` mandatory, cancel is a POST — almost nothing carries over from the perp order path.
- 20-trade/place-perp-order — POST a perp order, limit or market. camelCase body; price/size are strings. Omitted leverage = your per-market preference, else 20x — no longer the market max.
- 20-trade/prediction-orders — Place and cancel PM orders — binary vs multi-outcome routes, numeric-TIF wire quirk, unified DELETE cancels.
- 20-trade/tpsl — Attach, modify, cancel market-priced take-profit / stop-loss on a perp position. snake_case body.
- 30-portfolio/account-overview — Equity, cash, locks, margin ratios and risk level in one call — plus perp margin, 30d stats, token holdings.
- 30-portfolio/balances — Token balances — available vs locked per token, USDC-valued, all tokens or one symbol.
- 30-portfolio/history — Every look-back surface — perp orders and trades, funding, liquidations, ADL, position history v2, activity.
- 30-portfolio/my-prediction-data — Your PM orders, positions, trades, stats and match/activity feeds — integer shares vs sharesE6 units.
- 30-portfolio/options-positions — Open options positions, manual American exercise, and the exercise/settlement ledger. Exercise is irreversible, idempotency-keyed, and gated on price freshness.
- 30-portfolio/pnl — Perp PnL summary — realized, live unrealized, funding and fees, netted overall and per market.
- 30-portfolio/positions — Open perp positions — all or per market — entry/mark/liq prices, uPnL, margin, ADL rank.
- 40-treasury/deposit — Fund the account — hand over the /deposit link (USDC in the login wallet, other crypto via a one-time address, or card / bank), then confirm the credit landed.
- 40-treasury/deposit-address — A deposit address made for this account right in the chat, for money on an exchange or another chain (USDT on Tron, BTC, ETH, USDC elsewhere). Relay converts it to USDC in the user's own wallet; one tap on 1024 moves it in.
- 40-treasury/internal-transfer — Move USDC between your main account and its subs — direction is fixed by which key signs.
- 40-treasury/sub-accounts — One atomic call creates a sub-account plus its own trading API key, optionally pre-funded from the parent.
- 50-risk-and-keys/api-key-lifecycle — Introspect, list, rotate, and revoke API keys, and the per-key permission model.
- 50-risk-and-keys/error-model — One envelope for every response; code registry highlights, HTTP mapping surprises, and retry rules per class.
- 50-risk-and-keys/idempotency — clientOrderId semantics per domain, PM dedup keys, and how the HMAC replay window shapes safe retries.
- 70-analytics/leaderboards — Trading championships — list, detail, ranked leaderboard, top3, plus your own rank (the only signed call).
- 90-recipes/agent-fleet — One sub-account per strategy — isolated balances, own API keys, one-call kill switch.
- 90-recipes/funding-scanner — Sweep funding rates across every perp market, rank extremes, harvest the carry.
- 90-recipes/liquidation-guard — Watch margin ratio in real time, de-risk automatically before the engine does it for you.
- 90-recipes/market-maker-loop — Quote both sides, stay inside rate budgets, requote on book deltas, cancel clean on exit.
- 90-recipes/one-key-lifecycle — One ETH private key → account → funds → trades → withdrawal. The complete journey, no browser.
- 90-recipes/pm-basket — Build and execute a multi-market prediction basket — discover, price, then IOC each leg.
<!-- note-directory:end -->
