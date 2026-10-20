# Evidence

Everything here is produced by the code in this repository on the synthetic sandbox bank. Regenerate with `make evidence` (and `make anchor` for the chain part, which needs Foundry).

| File | What it shows | Produced by |
|---|---|---|
| `demo_run.log` | The whole "month for you" demo, scenarios C1 to C11, as a readable sandbox log: what the client said, every kernel card with its fields and provenance, signatures, receipts and what the kernel verified, the fake bill case, the scam-call ladder, the month later | `scripts/demo_run.py` |
| `demo_journal.jsonl` | The signed, hash-chained kernel journal of that run (one entry per line) | `scripts/demo_run.py` |
| `demo_summary.json` | Totals of that run: saved, done, asked, stopped; model calls by level; autonomy stages; journal head and public key | `scripts/demo_run.py` |
| `trust_report.json` | Earned trust in that run: levels per kind of task, each agent's record, promotions, demotions, strikes, quarantines, revoked passports, faulty agent actions proposed and executed, every move | `scripts/demo_run.py` |
| `invest_report.json` | Investing in that run (C11): the broker, the client's limits, the portfolio and orders, how many intents the broker step checked, what it stopped, and each fill | `scripts/demo_run.py` |
| `alpaca_paper.json` | Live run of 19 October 2026, 18:22 New York time: the C11 chat against an Alpaca paper account, order ids and statuses, the broker step for each request, and the broker call log without keys or the account number. One SPY limit order filled at $767.57; four off-mandate requests stopped. Needs Alpaca paper keys, so not part of `make evidence` | `scripts/alpaca_live.py` |
| `eval_report.json` | Evaluation set: 48 functional tasks (C1 to C10, EN/RU/ZH) and 50 attacks, pass rates per scenario | `scripts/eval_report.py` |
| `redteam.json` | Red-team: 48 static (S40 to S48 on investment orders) and 5 retention attacks with and without the kernel, adaptive attacker curves (10 rounds × 6 tries × 6 goals), the earlier prototype's suite | `scripts/redteam_report.py` |
| `costs.json` | Model tokens and estimated US dollars for one client's first and next month, per task (stub answers); projection to a million clients | `scripts/cost_report.py` |
| `costs_live.json`, `live_runs.json` | Live run with real models on 20 October 2026: the same month measured ($0.0114, then $0.0043), the functional evaluation with real models (44 / 48, every miss fails safe) and the model attacker: Claude declined five of six goals (four attempts on the sixth, all denied); the open model DeepSeek V4 Pro made 156 attempts over all six goals and reached none (`attacker_open_model`). Needs a key, so not part of `make evidence` | `scripts/live_report.py` |
| `budget_report.json` | Planted leaks and double charge found, spending forecast error on held-out months, cash-gap back-test | `scripts/budget_report.py` |
| `risk_graph.json` | Payee risk model on held-out synthetic transfer graphs: ROC-AUC, precision, recall, false positives, recall per scheme | `scripts/risk_graph_report.py` |
| `anchors.json`, `journal_demo.jsonl` | Journal checkpoints anchored by the `AuditAnchor` contract on a local devnet, read back, and three journal copies checked against them (original, rewritten entry, cut tail) | `scripts/anchor_journal.py` |
| `world.json` | The demo bank: accounts, cards on file, payees, documents | `data/generator.py` |

Numbers in stub mode use deterministic model stubs; token counts are measured on the real prompts. Live runs (with an API key, `python -m scripts.live_report`) write measured usage to `costs_live.json` and `live_runs.json`.
