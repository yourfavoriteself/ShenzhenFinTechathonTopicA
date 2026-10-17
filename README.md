# Guardian

**An AI assistant in the banking app that finishes money tasks for the client — and a kernel that lets it do exactly what the client signed, not a cent more.**

2026 Shenzhen FinTechathon · International Track · Topic A: Personal Finance Assistant · Theme: *AI as a Financial Participant*

> Finding money leaks is easy. Fixing them is risky. We make fixing them safe — and cheap enough to run for millions of clients.

> **Status:** work in progress. This README is filled in as the system is built.

## Promises

1. **The agent finishes the job** — it finds the problem, asks once, executes, and verifies the result.
2. **The agent can do exactly what you confirmed** — payee, amount, period, website. Nothing more.
3. **You never give the agent passwords or card numbers** — only the bank kernel sees them.
4. **Repeat tasks are almost free** — the first run uses a capable model, repeats use a saved recipe and a small model.

## Repository layout

| Path | Contents |
|---|---|
| `kernel/` | Trusted kernel: mandates (Verifiable Intent format), decision pipeline, provenance, policies, budget, executors, journal |
| `agents/` | LLM agents: dispatcher, planner, readers, browser agent (untrusted, propose intents only) |
| `risk_graph/` | Payee risk on the transfer graph |
| `sandbox/` | Sandbox bank and merchant websites |
| `web/` | Chat UI with kernel-rendered confirmation cards |
| `chain/` | `AuditAnchor` contract for journal Merkle roots |
| `db/` | SQL migrations |
| `tests/` | Unit, end-to-end, red-team and evaluation suites |
| `evidence/` | Recorded runs, signed journals, costs, attack curves |
| `guardian/` | Baseline modules, migrated into `kernel/` step by step |

## Quick start

```bash
docker compose up --build      # http://localhost:8000, replay mode, no API keys needed
```

Without Docker (Python 3.14):

```bash
make install
make test
make run
```

Live mode: copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY` and `GUARDIAN_LLM_MODE=live`.

All data is synthetic. Brands (`Melodify`, `StreamBox`, `CloudNest`) and people are fictional; domains use the reserved `.example` TLD.

## License

MIT, see `LICENSE`. Third-party packages: `THIRD_PARTY_NOTICES.md`.
