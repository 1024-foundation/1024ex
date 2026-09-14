# 1024ex — agent skill for 1024 Exchange

Trade on [1024 Exchange](https://www.1024ex.com) from Claude Code, Cursor,
Codex or any agent that understands [Agent Skills](https://agentskills.io) —
perpetuals and prediction markets over the public HTTP API: connect,
HMAC-signed orders, positions, balances and treasury.

## Install

This repo is the default install address:

```sh
npx -y skills add 1024-foundation/1024ex -y
```

The same package is also served from 1024ex.com itself:

```sh
npx -y skills add https://www.1024ex.com --skill 1024ex -y
```

```sh
curl -fsSL https://www.1024ex.com/skills/claude/install.sh | sh
```

Human-friendly walkthrough: https://www.1024ex.com/skills/install

## Connect

No key to paste. From the installed skill directory:

```sh
python3 scripts/api.py status     # exit 0 connected · 3 not connected · 2 key rejected
python3 scripts/api.py connect    # prints a login link — open it and approve
```

The API key lands in `~/.1024ex/credentials.json`; the secret never appears in
the chat. Or just tell your agent: "connect my 1024 account". Every connected
AI is listed, and revocable, at https://www.1024ex.com/connect.

Mainnet and testnet are separate accounts — pass `--testnet` to connect and
trade on https://testnet.1024ex.com.

## Contents

- [SKILL.md](SKILL.md) — the skill: connect flow, endpoint map, signing
  contract, market conventions, guardrails
- [scripts/api.py](scripts/api.py) — stdlib-only signed HTTP client
  (`status`, `connect`, `disconnect`, `deposit`, and signed `GET`/`POST`)
- [scripts/qr.py](scripts/qr.py) — vendored QR encoder (MIT, stdlib only) so
  a deposit address can be drawn in the chat

Full API reference lives at https://www.1024ex.com/skills (also indexed in
[llms.txt](https://www.1024ex.com/llms.txt)). The package here is built from
that vault and synced on every change.
