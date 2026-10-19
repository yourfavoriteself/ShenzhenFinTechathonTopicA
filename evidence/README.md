# Evidence

Everything here is produced by the code in this repository on the synthetic sandbox bank. Regenerate with `make evidence` (and `make anchor` for the chain part, which needs Foundry).

| File | What it shows | Produced by |
|---|---|---|
| `demo_run.log` | The whole "month for you" demo, scenarios C1 to C10, as a readable sandbox log: what the client said, every kernel card with its fields and provenance, signatures, receipts and what the kernel verified, the fake bill case, the scam-call ladder, the month later | `scripts/demo_run.py` |
| `demo_journal.jsonl` | The signed, hash-chained kernel journal of that run (one entry per line) | `scripts/demo_run.py` |
| `demo_summary.json` | Totals of that run: saved, done, asked, stopped; model calls by level; autonomy stages; journal head and public key | `scripts/demo_run.py` |
| `trust_report.json` | Earned trust in that run: levels per kind of task, each agent's record, promotions, demotions, strikes, quarantines, revoked passports, faulty agent actions proposed and executed, every move | `scripts/demo_run.py` |
| `eval_report.json` | Evaluation set: 48 functional tasks (C1 to C10, EN/RU/ZH) and 50 attacks, pass rates per scenario | `scripts/eval_report.py` |
| `redteam.json` | Red-team: 39 static and 5 retention attacks with and without the kernel, adaptive attacker curves (10 rounds × 6 tries × 6 goals), the earlier prototype's suite | `scripts/redteam_report.py` |
| `costs.json` | Model tokens and estimated rubles for one client's first and next month, per task; projection to a million clients | `scripts/cost_report.py` |
| `budget_report.json` | Planted leaks and double charge found, spending forecast error on held-out months, cash-gap back-test | `scripts/budget_report.py` |
| `risk_graph.json` | Payee risk model on held-out synthetic transfer graphs: ROC-AUC, precision, recall, false positives, recall per scheme | `scripts/risk_graph_report.py` |
| `anchors.json`, `journal_demo.jsonl` | Journal checkpoints anchored by the `AuditAnchor` contract on a local devnet, read back, and three journal copies checked against them (original, rewritten entry, cut tail) | `scripts/anchor_journal.py` |
| `world.json` | The demo bank: accounts, cards on file, payees, documents | `data/generator.py` |

Numbers in stub mode use deterministic model stubs; token counts are measured on the real prompts. Live runs (with an API key) add measured usage to `costs.json` and recorded model responses under `runs/llm/` for replay.
