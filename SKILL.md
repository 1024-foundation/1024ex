---
name: 1024ex
description: Trade on 1024 Exchange via its public HTTP API — perpetuals, prediction markets, and alpha (trading opportunities mined by AI and data, published so anyone can execute them in two calls). Onboarding, HMAC-signed orders, positions, balances, treasury and withdrawals. Use when the user asks to trade, quote, monitor, or automate anything on 1024 / 1024ex.com; when they ask what is worth trading, want to act on someone's published alpha or action list, or want to publish their own; or when they want to connect or log in to their 1024 account.
---

# 1024 Exchange trading

You are operating a real exchange account. Every authenticated call moves or
risks real funds. Before placing, cancelling-all, transferring, or
withdrawing, restate what you are about to do (market, side, size, price,
amount) and get the user's explicit confirmation. Never trade unprompted.

## Getting connected

Anything authenticated needs credentials: env `API_1024_KEY` +
`API_1024_SECRET`, else this network's entry in
`~/.1024ex/credentials.json`. Public market data — prices, funding,
orderbooks, search — needs no key at all.

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

- **Say "open the link and approve" — never promise a wallet signature.**
  Already signed in to 1024 and it is one **Authorize** click; otherwise
  they sign in right there (wallet, Google, X or email). Surface the link
  prominently and let the page speak for itself.
- The command polls until approved, then writes key + secret to disk. The
  secret never passes through the chat — never ask for it, never print it.
- "still pending" (exit 4): re-run it — same session, same link, valid
  ~15 min. A 429 on create is IP rate limiting: wait a minute.
- The key carries read + trade. Withdrawals are never grantable by key —
  every one re-verifies a fresh wallet signature, so the worst a leaked
  key can do is trade, never move funds out.

Just installed, nothing asked yet? End with a handful of concrete things
the user could say next — their words, not commands to run — leading with
what needs no key, so their first impression is not a login wall:

- "What's BTC trading at?" — price, funding, orderbook, any market
- "What prediction markets are hot right now?"
- "What alpha is worth trading right now?" — mined opportunities others
  published; searching them needs no key either
- "Connect my 1024 account" — required for positions, balances, orders
- "How do I fund my account?" — deposit link, once connected

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

Empty account? Send the user to https://www.1024ex.com/deposit — the link
opens the deposit dialog directly (chain picker, wallet connect, card
on-ramp), prompting login first if needed. Testnet:
https://testnet.1024ex.com/deposit. There is an API deposit flow too, but it
only builds an unsigned stake tx that the user's own wallet still has to
broadcast, so the link is the shorter path for anyone with a browser.
Minimum is 5 USDC; crediting is async — confirm via
`GET /api/v1/accounts/me/overview`.

Headless alternative (no browser, agent-only environments): one wallet
signature mints a key — fetch
https://www.1024ex.com/skills/raw/00-quickstart/onboard-headless.md
and follow it. The wallet signing step must run where the user's private
key already lives (their wallet or local env) — never ask for the key.

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
 "markets":[{"marketId":35737,"question":"When will Bitcoin hit $150k?",
             "marketType":"binary","yesPriceE6":36000,"endTime":"…","…":"…"}]}
```

`yesPriceE6` is meaningful for `binary` only; for `multi_outcome` read
per-outcome prices from `/api/v1/prediction/markets/{id}/outcomes`.

Account and trading (HMAC — always via `scripts/api.py`):

```text
GET    /api/v1/accounts/me/overview            equity + balances across products
GET    /api/v1/perp/positions                  open positions
GET    /api/v1/perp/orders                     open orders; filled/cancelled: /orders/history
POST   /api/v1/perp/orders                     place (body fields: see example above)
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
orders, treasury, streams) → Canonical docs below.

## Alpha — mined opportunities, executable in two calls

**Alpha is 1024's flagship: a trading opportunity surfaced by AI and data
mining, published as an object anyone can execute directly.** It is not
copy trading — never describe it that way. Nothing mirrors the author's
account and nothing keeps following them afterwards: the user gets a
concrete plan, sizes it themselves, and places it once. Route here the
moment they say *alpha*, *action list* or *跟单* (they mean this — fix the
framing), or ask what is worth trading right now.

| The user wants to… | What it is | Call |
| --- | --- | --- |
| see what is out there | published **action lists** (legs carrying preset order params), position-backed **tickets**, and a machine-generated **signal** feed | `GET /api/v1/alpha/lists?withPerf=true` (filters `q` `symbol` `sort=hot`); `GET /api/v1/alpha/search?q=` adds tickets + signals — public, no key |
| act on one | resolve the author's params against the live market, then place leg by leg | `POST /alpha/lists/{id}/plan` → `POST /alpha/lists/{id}/execute` |
| publish their own | legs + params, written atomically; or a ticket backing a position they hold | `POST /alpha/lists` · `POST /alpha/tickets` |

**Always ask for `withPerf=true` when listing alpha, and report the number
with what it is.** `perf.returnPct` is the same figure the web card shows —
every leg measured from the moment it entered the list, equal-weight,
unlevered, stance-signed (`window: sincePublished`, `basis:
equalWeightUnlevered`). It is **paper**: no entry slippage, no leverage, no
fees or funding, and no tp/sl exit, so a follower's result differs by all
four. `returnPct: null` means it could not be computed (`validLegs` /
`excluded` say why) — never render that as 0.00%. `/alpha/search` has no
`withPerf`; use `/alpha/lists` when the number matters.

`plan` turns a published idea into the exact orders for *this* account;
`execute` places them. **Never execute without planning first** — `plan`
is the only thing that interprets the author's params, and its `skipped` /
`warnings` are the only honest account of what will actually be placed.
`execute` partially succeeds by design: read `legs[].status` leg by leg.
`dryRun: true` rehearses everything and places nothing.

**Size is always the user's, never the author's.** `plan` takes
`budgetUsd` (or `perLegMarginUsd`) plus `leverage` — published alpha says
what and how, never how much.

Publishing runs the other way and is just as short: one signed
`POST /alpha/lists` writes every leg and its params in a single atomic
call. Where the idea came from does not matter — the user's own model,
this agent, a strategy they run on 1024's AgentX. A ticket is the other
kind: it must be backed by a position they actually hold, so it proves
the trade was real. Details: `15-alpha/alpha-search`,
`15-alpha/alpha-action-list`, `15-alpha/alpha-publish`.

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
entry, or "resting on the book, nothing filled yet". **A position that did
not move and no order row means the order did NOT land: say that, never
"order placed".** Same after a close or a cancel — the position must really
be gone (or smaller), the order really out of `/orders`.

## Rules that prevent losses

- **Omitting `leverage` on a perp order gives the market's MAXIMUM
  leverage, not 1x.** Set it explicitly on every order.
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
- **Prediction BUY orders need ~$1 notional** (`amount x priceE6`, with a
  one-share tolerance). Below it: 400 `REQ_INVALID_PARAMS` on `amount`.
  Sells have no minimum — a dust position can always be closed.
- Always pass `clientOrderId` (1-64 chars `[A-Za-z0-9_-]`) so retries are
  idempotent instead of duplicate orders.
- Keys default to `canTrade: false` and can carry market allowlists — a
  403 `PERM_*` means the key, not the request.

## Canonical docs

This file is a wrapper; the skill manual is the product. Before first use
of an area beyond the quick reference (funding, TP/SL, advanced orders,
treasury, streams…), fetch the relevant note as raw markdown from
https://www.1024ex.com/skills/raw/<path>.md — pick `<path>` straight from
the directory below, no index fetch needed. Complete corpus:
https://www.1024ex.com/llms-full.txt · index: https://www.1024ex.com/llms.txt
· graph: https://www.1024ex.com/skills.json

<!-- note-directory:begin — generated by scripts/gen-skill-index.mjs; do not edit by hand -->
- 00-quickstart/claim-testnet-usdc — POST /testnet/faucet/claim — fund your own testnet account with one signed call, no browser, no source-chain deposit.
- 00-quickstart/hello-exchange — First contact — read server time, exchange info, and system status with zero credentials.
- 00-quickstart/onboard-headless — POST /oauth/onboard — one wallet signature creates user, account, and API key in a single call.
- 00-quickstart/sign-requests — HMAC-SHA256 request signing — the three headers every authenticated call must carry.
- 10-discover/funding-and-prices — Funding rate current/list/history, mark & index price, open interest, insurance fund. Public.
- 10-discover/perp-markets — Perp market discovery — list, detail, ticker, orderbook, trades, klines. Public, no auth.
- 10-discover/prediction-collections — Event groupings — one tournament or series is a collection of related binary/multi-outcome markets.
- 10-discover/prediction-discovery — Find markets from a keyword — unified search (perps + collections + markets) plus the filtered PM list, shelves, categories and tags.
- 10-discover/prediction-market-data — Per-market PM data — detail, outcomes, orderbook/depth with the LP virtual ladder, prices, klines, trades, media.
- 10-discover/watchlists — Cross-product watchlists — perp/PM items with stance; share, clone, community. Same lists the web app shows.
- 15-alpha/alpha-action-list — Execute a published alpha end to end — the curator param vocabulary, the server-side plan that resolves it into concrete orders for your account, and per-leg execution. Your size, not theirs; one deliberate placement, not copy trading.
- 15-alpha/alpha-publish — Publish alpha you mined yourself — action lists anyone can execute and position-backed tickets anyone can verify, plus editing, unpublishing and deleting them. One signed call, whatever found the opportunity.
- 15-alpha/alpha-search — Find a mined opportunity worth taking — one keyword across published action lists, position-backed tickets and the server signal feed. Start here when the user asks what is worth trading right now. Public, no key.
- 20-trade/advanced-orders — 11 perp algo order types — conditional, twap, vwap, scale, oco, bracket, iceberg, pegged, pov, trailing-stop, sniper.
- 20-trade/close-position — Close a perp position full or partial. The price param is accepted but never applied — always a market close.
- 20-trade/leverage-and-margin — Read/set per-market leverage and add/remove position margin. Request bodies are snake_case here.
- 20-trade/manage-orders — List, fetch, cancel perp orders — single, batch of 50, or cancel-all. DELETEs carry JSON bodies.
- 20-trade/mint-redeem-claim — USDC to complete-set mint, redeem, claim winnings/refunds, settlement sweep — binary and multi-outcome.
- 20-trade/place-perp-order — POST a perp order, limit or market. camelCase body. Leverage defaults to market MAX, not 1x.
- 20-trade/prediction-orders — Place and cancel PM orders — binary vs multi-outcome routes, numeric-TIF wire quirk, unified DELETE cancels.
- 20-trade/tpsl — Attach, modify, cancel market-priced take-profit / stop-loss on a perp position. snake_case body.
- 30-portfolio/account-overview — Equity, cash, locks, margin ratios and risk level in one call — plus perp margin, 30d stats, token holdings.
- 30-portfolio/balances — Token balances — available vs locked per token, USDC-valued, all tokens or one symbol.
- 30-portfolio/history — Every look-back surface — perp orders and trades, funding, liquidations, ADL, position history v2, activity.
- 30-portfolio/my-prediction-data — Your PM orders, positions, trades, stats and match/activity feeds — integer shares vs sharesE6 units.
- 30-portfolio/pnl — Perp PnL summary — realized, live unrealized, funding and fees, netted overall and per market.
- 30-portfolio/positions — Open perp positions — all or per market — entry/mark/liq prices, uPnL, margin, ADL rank.
- 40-treasury/deposit — Fund the account — discover bridge routes, prepare a stake tx, broadcast from YOUR wallet, poll until credited.
- 40-treasury/internal-transfer — Move USDC between your main account and its subs — direction is fixed by which key signs.
- 40-treasury/sub-accounts — One atomic call creates a sub-account plus its own trading API key, optionally pre-funded from the parent.
- 40-treasury/withdraw — The exit gauntlet — grant canWithdraw, allowlist an address (24 h), clear limits and AML hold, create, poll.
- 50-risk-and-keys/api-key-lifecycle — Introspect, list, rotate, and revoke API keys; the per-key permission model and the stepped-up withdraw grant.
- 50-risk-and-keys/error-model — One envelope for every response; code registry highlights, HTTP mapping surprises, and retry rules per class.
- 50-risk-and-keys/idempotency — clientOrderId semantics per domain, PM dedup keys, and how the HMAC replay window shapes safe retries.
- 50-risk-and-keys/rate-limits — Per-key (else per-IP) sliding-window limits, VIP-0 defaults, burst math, daily caps, and the 429/403 split.
- 60-streams/incremental-orderbook — mode "incremental" book streams — snapshot then deltas, seq/prevSeq continuity, CRC32 checksum, recovery rules.
- 60-streams/websocket-auth — In-band auth frame — sign `{timestamp}GET/api/v1/ws` with your REST secret to unlock private channels.
- 60-streams/websocket-channels — One WS endpoint, 18 channels across perp/pm/user; frames, cadences, heartbeat and connection rules.
- 70-analytics/leaderboards — Trading championships — list, detail, ranked leaderboard, top3, plus your own rank (the only signed call).
- 70-analytics/market-analytics — Aggregator-shaped market data — CoinGecko/CMC tickers, CoinGecko-DEX pairs, per-market summaries, history, klines.
- 70-analytics/public-analytics — Protocol-wide aggregates — TVL, volume, fees, OI, liquidations, insurance fund, funding, DAU. Aggregator-ready.
- 90-recipes/agent-fleet — One sub-account per strategy — isolated balances, own API keys, one-call kill switch.
- 90-recipes/funding-scanner — Sweep funding rates across every perp market, rank extremes, harvest the carry.
- 90-recipes/liquidation-guard — Watch margin ratio in real time, de-risk automatically before the engine does it for you.
- 90-recipes/market-maker-loop — Quote both sides, stay inside rate budgets, requote on book deltas, cancel clean on exit.
- 90-recipes/one-key-lifecycle — One ETH private key → account → funds → trades → withdrawal. The complete journey, no browser.
- 90-recipes/pm-basket — Build and execute a multi-market prediction basket — discover, price, then IOC each leg.
<!-- note-directory:end -->
