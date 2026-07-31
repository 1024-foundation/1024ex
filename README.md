# 1024ex — agent skill for 1024 Exchange

Trade on [1024 Exchange](https://www.1024ex.com) from Claude Code or any
agent that understands [Agent Skills](https://agentskills.io) — perpetuals,
spot, and prediction markets over the public HTTP API: onboarding,
HMAC-signed orders, positions, balances, treasury and withdrawals.

## Install

Any one of:

```sh
npx skills add 1024-foundation/1024ex
```

```sh
curl -fsSL https://www.1024ex.com/skills/claude/install.sh | sh
```

Or point your agent at the well-known discovery index:
`https://www.1024ex.com/.well-known/agent-skills/index.json`

Human-friendly walkthrough: https://www.1024ex.com/skills/install

## Setup

Create an API key in the web app (Settings → API keys) and export:

```
API_1024_KEY     1024_<64-hex>
API_1024_SECRET  64-hex secret (shown exactly once at creation)
API_1024_BASE    optional base-URL override; default mainnet
```

Mainnet and testnet are separate accounts — a `--testnet` call needs a key
minted on https://testnet.1024ex.com.

## Contents

- [SKILL.md](SKILL.md) — the skill: endpoint map, signing contract, market
  conventions, guardrails
- [scripts/api.py](scripts/api.py) — stdlib-only signed HTTP client

Full API reference lives at https://www.1024ex.com/skills (also indexed in
[llms.txt](https://www.1024ex.com/llms.txt)). This repo mirrors the package
served from 1024ex.com; the site is the source of truth.
