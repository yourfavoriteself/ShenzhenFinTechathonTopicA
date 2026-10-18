"""Anchor the journal of a demo session on chain and prove that a tampered copy no longer matches.

    python -m scripts.anchor_journal                      # local devnet (anvil), no keys needed
    python -m scripts.anchor_journal --network sepolia    # GUARDIAN_ANCHOR_RPC + ~/.sepolia_key (test ETH only)
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from chain.anchor import Anchorer, Devnet, checkpoint, run_id_chain, verify
from evalset.tasks import Chat
from kernel.crypto import canonical, sha256_hex


# a short demo month; the journal is anchored after each step
STEPS = [
    ("cash gap closed from savings", lambda c: c.close_gap()),
    ("Melodify paid, monthly permission signed", lambda c: c.yes(c.say("Pay for Melodify"), standing=True)),
    ("subscription leaks found", lambda c: c.say("Find leaks in my subscriptions")),
    ("bills checked, fake bill flagged", lambda c: c.say("Pay my bills")),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", choices=["anvil", "sepolia"], default="anvil")
    ap.add_argument("--out", type=Path, default=Path("evidence/anchors.json"))
    args = ap.parse_args()
    devnet = None
    if args.network == "anvil":
        devnet = Devnet()
        rpc, key, contract = devnet.rpc, devnet.key, None
    else:
        rpc = os.environ["GUARDIAN_ANCHOR_RPC"]
        key = Path("~/.sepolia_key").expanduser().read_text().strip()
        contract = os.environ.get("GUARDIAN_ANCHOR_CONTRACT")
    try:
        a = Anchorer(rpc, key, contract)
        deployed = a.deploy() if contract is None else {"contract": contract}
        c = Chat()
        j = c.svc.k.journal
        run = uuid.uuid4().hex[:12]
        chain = run_id_chain(j.public_key, run)
        anchors = []
        for label, step in STEPS:
            step(c)
            cp = checkpoint(j.entries)
            anchors.append({"after": label, **cp, **a.anchor(chain, cp)})
        onchain = a.read(chain)
        ok, why = verify(j.entries, onchain)
        tampered = [e.model_copy(deep=True) for e in j.entries]
        victim = next(e for e in tampered if e.event == "mandate.signed")
        victim.data["standing"] = not victim.data["standing"]
        victim.hash = sha256_hex(canonical(victim.body()))           # the attacker even recomputes the hash
        t_ok, t_why = verify(tampered, onchain)
        truncated_ok, truncated_why = verify(j.entries[:-5], onchain)
        report = {"network": "anvil local devnet (chain id 31337)" if devnet else "sepolia",
                  "contract": deployed["contract"], "deploy_tx": deployed.get("tx"), "run_chain": chain,
                  "journal_entries": len(j.entries), "journal_public_key": j.public_key, "anchors": anchors,
                  "read_back": onchain,
                  "verification": {"original": {"ok": ok, "why": why},
                                   "entry_rewritten_and_rehashed": {"ok": t_ok, "why": t_why},
                                   "last_5_entries_cut": {"ok": truncated_ok, "why": truncated_why}},
                  "privacy": "only hashes, a sequence number and a run identifier are on chain"}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1))
        j.export_jsonl(args.out.with_name("journal_demo.jsonl"))
        print(f"{len(anchors)} anchors on {report['network']}; original {ok}, rewritten {t_ok}, truncated "
              f"{truncated_ok}")
    finally:
        if devnet:
            devnet.close()


if __name__ == "__main__":
    main()
