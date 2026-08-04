---
name: 1024ex
description: Trade on 1024 Exchange via its public HTTP API — perpetuals and prediction markets. Onboarding, HMAC-signed orders, positions, balances, treasury and withdrawals. Use when the user asks to trade, quote, monitor, or automate anything on 1024 / 1024ex.com.
---

# 1024 Exchange trading

You are operating a real exchange account. Every authenticated call moves or
risks real funds. Before placing, cancelling-all, transferring, or
withdrawing, restate what you are about to do (market, side, size, price,
amount) and get the user's explicit confirmation. Never trade unprompted.

## Setup

`scripts/api.py` reads credentials from env:

```
API_1024_KEY     1024_<64-hex>
API_1024_SECRET  64-hex (delivered exactly once at issuance)
API_1024_BASE    optional override; default mainnet
```

Most users will simply paste their key and secret into the chat — that
is expected, accept them. Set the two vars for whatever shell runs
`api.py` (export them, or prefix each call with the assignments), then
verify with a signed call such as `GET /api/v1/accounts/me/overview`.
Handle the paste with care: never echo the secret back, and never write
it into anything that gets committed or logged.

No key yet? Send the user to the web app — API keys live in Settings:
log in at https://www.1024ex.com and open https://www.1024ex.com/settings.
There they create a key (enabling trading if they intend to trade) and
copy the secret — it is shown exactly once, at creation.

Or testnet — for testing functions without real money: keys live at
https://testnet.1024ex.com/settings, accounts are separate, and a
`--testnet` call needs a testnet-minted key. A testnet account starts
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

This file is a wrapper; the skill manual is the product. Index:
https://www.1024ex.com/llms.txt — fetch the relevant note as raw markdown
from https://www.1024ex.com/skills/raw/<path>.md before first use of an
area (orders, treasury, streams…). Complete corpus:
https://www.1024ex.com/llms-full.txt · graph: https://www.1024ex.com/skills.json
