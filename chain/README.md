# AuditAnchor

Solidity contract that stores checkpoints of the Guardian journal: the Merkle root of the entries,
the hash of the newest entry and its sequence number, per kernel run. Hashes only: no client data,
amounts or payees go on chain. A copy of the journal is checked by recomputing the root
(`chain/anchor.py: verify`); a changed, removed or reordered entry, or a cut-off tail, no longer matches.

- `src/AuditAnchor.sol`: only the bank's anchoring key can write; the sequence must grow per run.
- `test/AuditAnchor.t.sol`: Foundry tests (`forge test` in this folder).
- `anchor.py`: deploys and anchors through Foundry's `cast` (no Ethereum libraries in Python).

```
python -m scripts.anchor_journal                     # local devnet (anvil), no keys
GUARDIAN_ANCHOR_RPC=<sepolia rpc> python -m scripts.anchor_journal --network sepolia   # test key in ~/.sepolia_key
```

The result goes to `evidence/anchors.json` with transaction hashes, the read-back value and three
checks: the original journal matches, a rewritten and re-hashed entry does not, a cut tail does not.
Foundry 1.8.5 and solc 0.8.30 are used; the tests are skipped when Foundry is not installed.
