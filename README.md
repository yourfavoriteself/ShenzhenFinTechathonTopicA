<p align="center">
  <img src="docs/media/banner.svg" alt="Guardian: an AI assistant that finishes your money tasks, inside a bank kernel that lets it do only what you signed" width="100%">
</p>

<p align="center">
  <a href="https://github.com/yourfavoriteself/ShenzhenFinTechathonTopicA/actions/workflows/ci.yml"><img src="https://github.com/yourfavoriteself/ShenzhenFinTechathonTopicA/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/tests-338-2ea44f" alt="338 tests">
  <img src="https://img.shields.io/badge/red--team-39%2F39%20stopped-2ea44f" alt="red-team 39 of 39 stopped">
  <img src="https://img.shields.io/badge/python-3.14-3776ab" alt="Python 3.14">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license">
</p>

<p align="center">
  <b><a href="docs/media/guardian_demo.mp4">Demo video (3:18)</a></b> ·
  <b><a href="docs/presentation.pdf">Presentation</a></b> ·
  <b><a href="docs/technical_documentation.pdf">Technical documentation</a></b> ·
  <b><a href="docs/security_report.pdf">Security report</a></b> ·
  <b><a href="evidence/README.md">Execution evidence</a></b>
</p>

**2026 Shenzhen FinTechathon · International Track · Topic A: Personal Finance Assistant · Theme: *AI as a Financial Participant***

Banking apps have shown spending charts for years, and almost nobody acts on them. Finding a money leak is easy. Fixing it is a chore: pay this, cancel that, dispute a double charge, check a bill, move money to a goal. Banks do not let AI do these chores because an agent that can move money can also be tricked into moving it to the wrong place.

**Guardian lets the assistant do the chores and makes that safe.** The assistant pays, cancels, disputes, budgets and saves for the client. Next to it sits a small deterministic kernel inside the bank. The kernel lets the assistant do exactly what the client signed on their own device, checks every result with the bank, and writes everything to a signed journal. Models propose; code decides.

<p align="center"><img src="docs/media/demo.gif" alt="Guardian closes a cash gap: the kernel renders a card, the client signs it with the device key, the kernel verifies the result" width="88%"></p>

## Contents

[What the client gets](#what-the-client-gets) · [Results](#results) · [A month for one client](#a-month-for-one-client) · [How it works](#how-it-works) · [Security](#security) · [Compliance](#compliance) · [Cost](#cost) · [Check it yourself in 5 minutes](#check-it-yourself-in-5-minutes) · [How we built it](#how-we-built-it) · [Limitations and next steps](#limitations-and-next-steps) · [Team](#team)

## What the client gets

1. **The job gets done.** Guardian finds the problem, asks once, acts, and checks the result with the bank or the merchant. It does not stop at advice.
2. **Exactly what you confirmed, nothing more.** Payee, amount, period, money source and merchant are fixed in a mandate that your phone signs. The card you sign is drawn by the kernel, not by a model: what you see is what you sign.
3. **No passwords or card numbers in any model.** Merchant sign in goes through a bank form with domain and certificate checks; the bank vault keeps the credentials; the model gateway refuses any prompt that carries a secret.
4. **Repeat tasks are almost free.** The first run uses a model; the repeat comes from a saved template with no model at all. One client's month costs 0.73 ₽ in tokens the first time and 0.27 ₽ after that.

## Results

Every number below is produced by code in this repository on a synthetic sandbox bank (seed 22, 1 156 transactions, "now" is 17 Oct 2026 10:00 MSK). Run `make evidence` to get them again.

| What we measured | Result | Proof |
|---|---|---|
| Functional evaluation, scenarios C1 to C10, in English, Russian and Chinese | **48 / 48** tasks | [`eval_report.json`](evidence/eval_report.json) |
| Attacks in the evaluation set | **50 / 50** stopped | [`eval_report.json`](evidence/eval_report.json) |
| Static red-team: a compromised agent with a valid passport | **39 / 39** stopped, 0 ₽ lost. Without the kernel 39 / 39 succeed and **532 038.76 ₽** is lost | [`redteam.json`](evidence/redteam.json) |
| Retention tricks during cancellation (discount, upsell, wrong card) | **5 / 5** stopped | [`redteam.json`](evidence/redteam.json) |
| Adaptive attacker that learns from the kernel's answers (10 rounds × 6 tries × 6 goals) | **0 / 360** attempts went outside the mandate. Without the kernel all 6 goals are reached on the first try | [`redteam.json`](evidence/redteam.json) |
| Attack suite of our earlier prototype, replayed | 20 / 21 prevented, 1 bounded by the limit | [`redteam.json`](evidence/redteam.json) |
| Subscription leaks planted and found | 5 / 5, no false finds (847 ₽ a month, 10 164 ₽ a year) | [`budget_report.json`](evidence/budget_report.json) |
| Double charge planted and found | 1 / 1 | [`budget_report.json`](evidence/budget_report.json) |
| Cash gap caught in time on held-out months | 1 / 1, 0 false alarms in 18 probes | [`budget_report.json`](evidence/budget_report.json) |
| Spending forecast on held-out months (July to September) | WAPE 27.3 % overall; bills 4.3 %, subscriptions 1.3 % | [`budget_report.json`](evidence/budget_report.json) |
| Payee risk on the bank's transfer graph (held-out synthetic graphs) | ROC-AUC 1.0, precision 0.993, recall 0.944, no false alarms on employers, merchants or landlords | [`risk_graph.json`](evidence/risk_graph.json) |
| Journal anchoring on a local devnet | 4 checkpoints; a rewritten entry and a cut tail are both caught | [`anchors.json`](evidence/anchors.json) |
| Token cost of one client's month (real prompts, stub answers) | 0.73 ₽ the first month, 0.27 ₽ the next (63 % less); about 270 000 ₽ a month for a million clients | [`costs.json`](evidence/costs.json) |
| The whole demo month | 548 ₽ a month saved, 10 tasks done, 11 questions, 2 attacks stopped, 292 signed journal entries, chain verified | [`demo_run.log`](evidence/demo_run.log) |
| Automated tests | 329 pass; 9 more run against PostgreSQL in CI | [CI](.github/workflows/ci.yml) |

We are honest about one thing: the graph model is trained and tested on graphs from our own generator. Real bank data will score lower. The numbers show that the pipeline works, not field accuracy.

## A month for one client

The demo follows one client, Anna, through a month in the sandbox bank. All ten scenarios run in the real interface; the [video](docs/media/guardian_demo.mp4) shows them in order and [`demo_run.log`](evidence/demo_run.log) is the full log.

| # | Anna says | What Guardian does |
|---|---|---|
| C1 | "Pay for Melodify" | A kernel card with the real payee, amount and source. One signature, and she can allow it monthly. Next month it runs alone inside that permission, with no model call |
| C2 | "Cancel the 399 ₽ subscription" | The kernel asks which of two matching subscriptions, blocks future charges on that card, signs in on the merchant's official domain with credentials from the bank vault, cancels through the merchant API and checks three sources |
| C3 | "Find leaks in my subscriptions" | Unused, duplicate and more expensive subscriptions: 847 ₽ a month, with the transactions behind each one |
| C4 | "Was I charged twice?" | Both transactions found; a dispute opens after one confirmation |
| C5 | "Pay my bills" | Every bill checked against the bank's payee directory and her history before a card appears |
| C6 | a fake bill in the inbox | Look-alike sender, wrong account, pressure words, hidden text for AI agents, a link to an earlier phishing mail: a case for the bank's analysts instead of a payment |
| C7 | a free trial ends tomorrow | Guardian asks before the first charge and cancels if she says so |
| C8 | "Make me a budget", "Save 60 000 for a vacation by July, don't touch the gym" | Budget from twelve months of history, envelopes, safe to spend today, a cash gap warning with fixes from her own money (never credit), salary split, goal plan, month review, forecast accuracy |
| C9 | "Transfer 350 000 ₽ to a safe account" during a call | The mobile operator reports a call from a spoofed number. The scam ladder rises: explain, pause with questions and PIN, 24 hour reversible hold with her daughter informed, live analyst. A duress PIN shows a normal receipt while nothing leaves |
| C10 | "Transfer 20 000 to account …" | The payee is scored on the bank's transfer graph. A money mule raises the risk; an ordinary account does not |

<table>
  <tr>
    <td width="50%"><img src="docs/media/01_card.png" alt="A kernel card"><br><sub><b>A card.</b> Every field shows where it came from. The device key signs the hash of exactly this card.</sub></td>
    <td width="50%"><img src="docs/media/04_protected_form.png" alt="Protected sign in form"><br><sub><b>Protected form.</b> Domain and certificate checked; no model sees the password.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/media/06_fake_bill.png" alt="A fake bill stopped"><br><sub><b>Fake bill.</b> Two real bills pass; the look-alike is stopped with its reasons and sent to analysts.</sub></td>
    <td><img src="docs/media/08_scam.png" alt="Scam call ladder"><br><sub><b>Scam call.</b> The right PIN and a confident "no" only start a pause; then a person at the bank decides.</sub></td>
  </tr>
  <tr>
    <td><img src="docs/media/09_redteam.png" alt="Red-team section"><br><sub><b>Red-team section</b> in the app: with and without the kernel.</sub></td>
    <td><img src="docs/media/09_cost.png" alt="Cost section"><br><sub><b>Cost section:</b> which model level did the work and what it cost.</sub></td>
  </tr>
</table>

## How it works

<p align="center"><img src="docs/diagrams/architecture.png" alt="Architecture: untrusted agents propose, the trusted kernel decides, the bank executes, the journal records" width="100%"></p>

Agents (dispatcher, planner, document readers, critic) are untrusted. They send *intents* that say what to do and where every value came from. The kernel runs the same pipeline for every intent, whoever sent it:

**passport → schema → mandate → provenance → references → policy → risk → critic → shadow run → execute → verify**

- **Passport (know your agent).** Each agent has a role, a model level, a hash of its instructions and a session key. The kernel checks it at every step and writes it to the journal.
- **Mandate.** The client's permission in Mastercard Verifiable Intent format: payee, amount range, total budget, period, money source, expiry. Signed by the device key (Ed25519 or P-256 in WebCrypto, not extractable) and countersigned by the kernel. Agents get single-use grants that can only narrow it.
- **Provenance.** Every field carries a CLIENT, BANK or UNTRUSTED label. Money can go only to payees and amounts that came from the client or the bank, never from a document or a model.
- **Critic.** A second model sees only the client's own words and the card, never the documents an attacker controls. On high risk a counterfactual check runs too. Both can only make a decision stricter.
- **Shadow run.** The payment is rehearsed in a copy of the bank first. If money would move differently from what was asked, it is denied.
- **Verify.** After execution the kernel checks the bank and the merchant itself. The agent's word is not enough.
- **Earned trust.** Each kind of task starts at level 0 (a card for everything) and climbs after 5 verified results in a row. Any mistake drops it a level; a scope violation drops it to 0.
- **Journal.** Signed and hash chained. In PostgreSQL it is append only (a trigger accepts only the next link) under row level security. Merkle checkpoints are anchored by the [`AuditAnchor`](chain/src/AuditAnchor.sol) contract.

## Security

The [security report](docs/security_report.pdf) covers the threat model, who may do what at each autonomy stage, every control, the red-team method and results, and fourteen known risks with what limits each of them. In short:

| Attack | What stops it |
|---|---|
| Fake bill with a look-alike sender and hidden instructions for AI agents | Provenance (the document cannot set a payee), payee directory, domain check, injection detector, link to earlier suspicious events |
| Agent swaps the payee or raises the amount | The card is drawn by the kernel; the signature covers its hash; the grant is single use and bound to that hash |
| "Safe account" scam call | Telecom signal, four level ladder, reversible hold, live analyst, duress PIN, trusted person informed |
| Phishing during cancellation | Official domain from the bank's directory, certificate check, credentials only in the bank vault and only for that domain |
| Retention tricks (discount, upsell, wrong card) | The mandate allows one action on one card; anything else is denied |
| Rewriting the journal after the fact | Hash chain, device and kernel signatures, append only table, on chain checkpoints |

## Compliance

Guardian follows **智能体支付应用自律公约**, the self-regulatory code for agent payment apps of the Payment & Clearing Association of China (2026), and maps its mandates to **Mastercard Verifiable Intent** and **Google AP2**. The full tables, with where to check each line, are in the app's Compliance section and in the [security report](docs/security_report.pdf).

| Requirement of the code | How Guardian meets it |
|---|---|
| Three stages of autonomy | Stages 1 and 2 implemented; stage 3 (broad autonomy) is not offered, by design |
| Whoever provides the payment service is responsible | Guardian is a bank module; only the bank's executor moves money |
| Know your agent | Agent passport, checked at every step and journaled |
| Verify the client's intent | Kernel drawn card, device signature, critic |
| Withdraw authorisation at any time | One press, child grants revoked too |
| Immutable records | Signed hash chain, append only storage, anchored checkpoints |
| Human fallback, emergency stop | Analyst hold, kill switch, 24 hour reversible hold |
| Stricter rules for the elderly and minors | Elevated protection, trusted person informed |
| No inducement to spend | No credit or upsell; offers in model text are replaced |

## Cost

Models sit on a ladder. Level 0 is rules, templates and kernel code and costs nothing. Level 1 is a small model (claude-haiku-4-5) for readers and the critic. Level 2 (claude-sonnet-5-5) plans new tasks, and level 3 (claude-opus-5-5) is kept for rare hard cases. The gateway enforces a hard budget of 2 000 ₽ and filters secrets out of every prompt.

In the full demo 96.7 % of the work stays on levels 0 and 1. The second month of the same tasks costs 63 % less than the first, because the repeat payment and the repeated check come from templates. See [`costs.json`](evidence/costs.json): these are token counts on the real prompts, priced at list prices; a live run will replace the estimate.

## Check it yourself in 5 minutes

```bash
git clone https://github.com/yourfavoriteself/ShenzhenFinTechathonTopicA.git
cd ShenzhenFinTechathonTopicA
docker compose up --build        # runs all tests during the build, then serves http://localhost:8000
```

No API keys are needed: the default replay mode plays back recorded model runs and falls back to deterministic stubs. Then in the browser:

1. Click **Pay for Melodify**, tick "allow monthly" and confirm. Open **Permissions**: the mandate is there and can be revoked.
2. Click **Pay my bills**. Two bills get cards; the fake one is stopped with its reasons.
3. Press **📞 Scam call**, then send "Transfer 350 000 ₽ from my savings to safe account 40817810500001234567". Watch the ladder.
4. Press **+1 month** and ask for Melodify again: a notification, no card, no model call.
5. Open **Red-team**, **Cost**, **Compliance** and **Journal**.

Without Docker (Python 3.14):

```bash
make install        # pinned requirements and Chromium for the UI tests
make test           # unit, end to end in Chromium, red-team, evaluation
make run            # http://localhost:8000
make evidence       # recomputes every number in the Results table
make anchor         # anchors the journal on a local anvil devnet (needs Foundry)
make docs video     # rebuilds the PDFs, the slides and the demo video
GUARDIAN_TEST_PG=postgresql://…/empty_db make test-pg
```

Modes (`GUARDIAN_LLM_MODE`): `replay` (default), `stub` for tests, `live` and `record` with `ANTHROPIC_API_KEY` in `.env` (see [`.env.example`](.env.example)).

## How we built it

We started from our earlier prototype ([`guardian/`](guardian)): purpose bound envelopes, autonomy priced by risk, a duress mode. For this track we wanted more: a permission the client signs on their own device, a kernel that decides without any model, and proof for every result. So we rebuilt the core around one rule, *models propose, code decides*, and treated every agent as if it were already compromised. The red-team in this repository attacks exactly that: an agent with a valid passport that does whatever an attacker wants.

A few decisions that shaped the project (the full list with reasons is in [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md)):

- **Cancellation through the merchant's API, not a browser agent.** A browser agent reading merchant pages is exactly the surface that hidden instructions target. Talking to the merchant's API through the bank, with credentials from the bank vault, removes that surface.
- **The kernel draws the card.** If the model wrote the confirmation text, an attacker who controls the model would control what the client signs.
- **Envelopes are the money source in the mandate.** "Pay up to 4 400 ₽ from Bills" is a limit the client understands and the bank can enforce.
- **A pause, not a refusal, for scam calls.** Victims under pressure confirm anything. Time and a person at the bank break the script; a hard block just sends them to another bank.
- **Measure, don't claim.** Every number in this README is produced by a script in [`scripts/`](scripts) and checked by tests.

## Limitations and next steps

- Live model runs (cost measured on real answers, the adaptive attacker driven by a live model) are ready in code and wait for API keys; until then cost is an estimate on real prompts.
- The payee risk model needs real bank graphs to show field accuracy.
- Journal anchoring runs on a local devnet; a public test network needs only an RPC endpoint and a funded key.
- Mandates are signed JSON; SD-JWT selective disclosure and passkeys (WebAuthn) are the next step toward full Verifiable Intent.
- The client device is trusted: if it is fully compromised, the attacker can sign cards. Elevated protection and the scam ladder limit the damage.

## Repository layout

| Path | Contents |
|---|---|
| [`kernel/`](kernel) | The trusted kernel: passports, mandates, pipeline, provenance, references, policy, critic, counterfactual check, trust, budget, cancellations, secret vault, journal, model gateway, PostgreSQL store |
| [`agents/`](agents) | Untrusted agents: dispatcher, planner, readers |
| [`app/`](app), [`web/`](web) | Chat service, web API and the UI with kernel drawn cards and the device key |
| [`sandbox/`](sandbox) | Sandbox bank and merchant accounts with their APIs |
| [`data/`](data) | Synthetic world generator |
| [`risk_graph/`](risk_graph) | Transfer graph generator, features and model |
| [`redteam/`](redteam), [`evalset/`](evalset) | Attack harness (static, retention, adaptive) and functional evaluation tasks |
| [`chain/`](chain) | `AuditAnchor` contract (Foundry), its tests and the anchoring client |
| [`db/`](db) | SQL migrations: schema, row level security, append only journal |
| [`tests/`](tests) | Unit, end to end (Chromium), integration, red-team and evaluation suites |
| [`scripts/`](scripts), [`evidence/`](evidence) | Report generators and their output |
| [`docs/`](docs) | The six submission materials ([index](docs/README.md)) |
| [`guardian/`](guardian) | Our earlier prototype, kept because its attack suite is replayed against the new kernel |

All data is synthetic. Brands (Melodify, StreamBox, CloudNest), people and accounts are fictional; domains use the reserved `.example` zone.

## Team

**Kseniia Kuzmina** and **Nikita Stepanov**, Bauman Moscow State Technical University.

## License

MIT, see [`LICENSE`](LICENSE). Third party packages: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Security notes: [`SECURITY.md`](SECURITY.md).
