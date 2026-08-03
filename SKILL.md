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

Credentials come from env — never ask the user to paste secrets into chat:

```
API_1024_KEY     1024_<64-hex>
API_1024_SECRET  64-hex (delivered exactly once at issuance)
API_1024_BASE    optional override; default mainnet
```

No key yet? Send the user to the web app — API keys live in Settings:

- mainnet: log in at https://www.1024ex.com then open
  https://www.1024ex.com/settings
- testnet: log in at https://testnet.1024ex.com then open
  https://testnet.1024ex.com/settings

There they create a key (enabling trading if they intend to trade) and
copy the secret — it is shown exactly once, at creation. Then export
both env vars in the shell running you. Mainnet and testnet are separate
accounts: a `--testnet` call needs a key minted on testnet.

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
GET /api/v1/perp/markets                       tick/step, max leverage per market
GET /api/v1/perp/markets/{m}/ticker            also /orderbook /klines /trades
GET /api/v1/prediction/markets/active          also /trending /search?q= /markets/{id}
GET /api/v1/prediction/markets/{id}/orderbook  also /depth /price-history
```

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
```

**Symbol formats differ**: perp is `BTC-USDC` (dash), prediction takes a
numeric `marketId`. Anything beyond this table (funding, TP/SL, advanced
orders, treasury, streams) → Canonical docs below.

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
