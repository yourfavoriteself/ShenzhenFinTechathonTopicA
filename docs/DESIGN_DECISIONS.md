# Design decisions

The choices that shaped Guardian, each with the problem behind it and what we gave up, so that every part of the system can be traced back to a reason.

## 1. Models propose, code decides

**Problem.** An assistant that can pay can be talked into paying the wrong person: by a fake bill, a hidden instruction in a document, or a scammer on the phone. No prompt makes a model immune to that.

**Decision.** No model is on the path that allows money to move. Agents send intents; a small deterministic kernel checks them and is the only thing that can reach the bank's executor. We designed every check as if the agent were already compromised, and the red-team attacks exactly that case: an agent with a valid passport that does what the attacker wants.

**Cost.** Everything the kernel allows must be expressible as rules and data. Free-form requests become a card for the client instead of an action.

## 2. The kernel draws the card, the device signs its hash

**Problem.** If the model writes the confirmation text, whoever controls the model controls what the client believes they approve.

**Decision.** The card (payee, amount, source, what Guardian can and cannot do) is rendered by the kernel from bank data. The client's device key signs the hash of that card, and execution uses a single-use grant bound to the same hash. What you see is what you sign.

**Cost.** Card texts are templates, less flexible than generated text. We think that is the right trade for money.

## 3. Mandates in the shape of Verifiable Intent and AP2

**Problem.** A home-made permission format would be one more thing for a bank to learn and audit.

**Decision.** Mandates follow Mastercard Verifiable Intent constraints (allowed payees, amount range, budget, recurrence, reference) and the AP2 split into intent and payment mandates. We added two constraints we needed: allowed origins (merchant domains) and the money source. Keys are Ed25519 or P-256 in WebCrypto, marked non-extractable.

**Cost.** Mandates are signed JSON for now. SD-JWT selective disclosure and passkeys are the next step.

## 4. Provenance labels instead of hoping to catch every injection

**Problem.** Injection detectors miss new phrasings. A payee taken from a document is dangerous no matter how innocent the document looks.

**Decision.** Every field of an intent carries CLIENT, BANK or UNTRUSTED. Payees and amounts may come only from the client's words or the bank's records. The injection detector is still there, but as an extra signal for the fraud case, not as the line of defence.

## 5. A critic that never reads attacker text

**Problem.** A second model that reads the same poisoned bill can be poisoned the same way.

**Decision.** The critic sees only the client's own words and the kernel's card. On high risk a counterfactual check replays the step with the client's request masked: if the agent still proposes the same payment, the data it was shown is driving it, not the client, and the kernel stops it. Both can only make a decision stricter, never looser.

## 6. Cancellation through the merchant's API, not a browser agent

**Problem.** An agent that reads and clicks merchant pages is exactly the surface that hidden instructions on web pages target, and it needs the client's password.

**Decision.** The bank talks to the merchant's API. Credentials live in the bank vault, bound to the merchant's official domain from the bank's directory; the client types them once into a bank form with domain and certificate checks, and the SMS code on their phone. The bank also blocks future charges from that merchant on that card, so the client is protected even if the merchant only pretends to cancel. The result is checked from three sources.

**Cost.** It needs merchants with an API. For the rest the card block alone already stops the money.

## 7. Envelopes are the money source

**Problem.** "Pay up to $53.11" says nothing about what the money was meant for.

**Decision.** A mandate names its source: "from the Bills envelope". The client understands that limit, the bank can enforce it, and the budget stays true without extra bookkeeping.

## 8. A pause and a person for scam calls, not a hard block

**Problem.** People under pressure from a fake "bank security officer" confirm anything and know the answers the scammer told them to give. A hard refusal just sends them to another bank or an ATM.

**Decision.** A four-level ladder driven by signals (an ongoing call from a spoofed number reported by the mobile operator, scam wording, a new recipient, money just moved from savings): explain, then a pause with questions and the PIN, then a 24-hour reversible hold with a trusted person informed, then a live analyst. A duress PIN shows an ordinary receipt while nothing leaves. Time and a human break the script.

## 9. Trust is earned per kind of task, and stage 3 is not offered

**Problem.** One global "autonomy" switch is either too risky or useless.

**Decision.** Each kind of task (subscriptions, bills, own transfers, external payments) starts at level 0, where every action needs a card, and climbs after five verified results in a row. One mistake drops it a level; a scope violation drops it to zero. Standing permissions cover stage 2 of the code for agent payment apps. Stage 3, broad autonomy, we do not offer at all.

## 10. Cheap by design: model ladder and templates

**Problem.** A capable model on every request does not scale to millions of clients.

**Decision.** Level 0 is rules, templates and kernel code. Level 1 is a small model for reading documents and for the critic. Level 2 plans new tasks, level 3 is kept for rare hard cases. A solved task becomes a template, so the repeat needs no model at all. The gateway enforces a hard budget and refuses prompts that carry secrets.

**Result.** 97.6 % of the demo month runs on levels 0 and 1; the second month costs 63 % less than the first.

## 11. A rehearsal before every payment

**Problem.** A payment can be correct on paper and still move money in an unexpected way (a hidden fee, a second debit).

**Decision.** Before execution the kernel replays the payment in a copy of the bank and compares what moved with what was asked. Any difference is a denial.

## 12. The journal: a signed hash chain, append only, with anchored checkpoints

**Problem.** Regulators and clients need to know who allowed what and which model proposed it, and a database administrator must not be able to rewrite that quietly.

**Decision.** Every step goes into a hash chain signed by the kernel. In PostgreSQL the table accepts only the next link of a chain and refuses updates and deletes for everyone, under row level security. Only Merkle roots of checkpoints go on chain, so no client data leaves the bank.

## 13. Reproducible without keys

**Problem.** Judges and reviewers should be able to check every number without our API keys or our machines.

**Decision.** The default replay mode plays back recorded model runs and falls back to deterministic stubs. The demo world is generated from a fixed seed. Every number in the README comes from a script in `scripts/` and is covered by tests; CI runs the whole suite, including PostgreSQL and the contract, on every push.

## 14. Honest numbers

**Problem.** Synthetic data makes it easy to report perfect scores.

**Decision.** We publish what the scores are measured on and where they will be lower: the transfer graph model is trained and tested on our own generator, the cost is an estimate until live runs, and the security report lists fifteen known risks with what limits each one.
