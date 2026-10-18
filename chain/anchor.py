"""Anchor journal checkpoints on chain and verify a journal copy against them.

Only hashes leave the bank: the Merkle root of the journal entries, the hash of the newest entry and
its sequence number, under an identifier of the kernel run. The transaction is sent with Foundry's
`cast`, so the Python side needs no Ethereum libraries. A local devnet (anvil) is used for tests and
the demo; a public testnet (Sepolia) works the same way with an RPC URL and a funded test key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from kernel.journal import JournalEntry, merkle_root

HERE = Path(__file__).resolve().parent
SIG_ANCHOR = "anchor(bytes32,uint64,bytes32,bytes32)"
SIG_LATEST = "latest(bytes32)((bytes32,bytes32,uint64,uint64))"
TEST_MNEMONIC = "test test test test test test test test test test test junk"   # public, devnet only
SIG_GET = "get(bytes32,uint256)((bytes32,bytes32,uint64,uint64))"


class AnchorError(Exception):
    pass


def tool(name: str) -> str | None:
    local = Path(os.environ.get("FOUNDRY_BIN", "~/.foundry/bin")).expanduser() / name
    return str(local) if local.exists() else shutil.which(name)


def available() -> bool:
    return all(tool(t) for t in ("forge", "cast", "anvil"))


def run_id_chain(public_key: str, run_id: str) -> str:
    return "0x" + hashlib.sha256(f"{public_key}:{run_id}".encode()).hexdigest()


def checkpoint(entries: list[JournalEntry], seq: int | None = None) -> dict[str, Any]:
    upto = entries if seq is None else entries[:seq + 1]
    return {"seq": upto[-1].seq, "merkle_root": merkle_root([e.hash for e in upto]), "head_hash": upto[-1].hash}


def verify(entries: list[JournalEntry], anchored: dict[str, Any]) -> tuple[bool, str]:
    """Does this copy of the journal contain, unchanged, everything the anchor covers?"""
    seq = int(anchored["seq"])
    if len(entries) <= seq:
        return False, f"journal copy ends at seq {len(entries) - 1}, the anchor covers seq {seq}"
    cp = checkpoint(entries, seq)
    if cp["merkle_root"] != anchored["merkle_root"].removeprefix("0x"):
        return False, "Merkle root differs from the anchored one: an entry was changed, removed or reordered"
    if cp["head_hash"] != anchored["head_hash"].removeprefix("0x"):
        return False, "head hash differs from the anchored one"
    return True, f"entries 0..{seq} match the anchor"


class Anchorer:
    def __init__(self, rpc_url: str, private_key: str, contract: str | None = None):
        self.rpc, self._key, self.contract = rpc_url, private_key, contract

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        env = dict(os.environ)
        solc = Path("~/.foundry/solc/solc-0.8.30").expanduser()
        if solc.exists():
            env.setdefault("FOUNDRY_SOLC", str(solc))
            env.setdefault("FOUNDRY_OFFLINE", "true")
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
        if p.returncode:
            msg = (p.stderr or p.stdout).replace(self._key, "***")
            raise AnchorError(msg.strip()[-500:])
        return p.stdout

    def deploy(self) -> dict[str, Any]:
        out = self._run([tool("forge"), "create", "src/AuditAnchor.sol:AuditAnchor", "--rpc-url", self.rpc,
                         "--private-key", self._key, "--broadcast", "--json"], cwd=HERE)
        data = json.loads(out[out.index("{"):])
        self.contract = data["deployedTo"]
        return {"contract": self.contract, "tx": data["transactionHash"], "deployer": data.get("deployer")}

    def anchor(self, chain: str, cp: dict[str, Any]) -> dict[str, Any]:
        out = self._run([tool("cast"), "send", self.contract, SIG_ANCHOR, chain, str(cp["seq"]),
                         "0x" + cp["merkle_root"], "0x" + cp["head_hash"], "--rpc-url", self.rpc,
                         "--private-key", self._key, "--json"])
        r = json.loads(out)
        if r.get("status") not in ("0x1", 1, "1"):
            raise AnchorError(f"transaction failed: {r.get('transactionHash')}")
        return {"tx": r["transactionHash"], "block": int(r["blockNumber"], 16), "gas_used": int(r["gasUsed"], 16)}

    def read(self, chain: str, index: int | None = None) -> dict[str, Any]:
        args = [SIG_LATEST, chain] if index is None else [SIG_GET, chain, str(index)]
        out = self._run([tool("cast"), "call", self.contract, *args, "--rpc-url", self.rpc]).strip()
        root, head, seq, at = re.findall(r"0x[0-9a-f]{64}|\d+", out.strip("()"))[:4]
        return {"merkle_root": root, "head_hash": head, "seq": int(seq), "at": int(at)}


class Devnet:
    """A throwaway local chain (anvil) with funded test accounts; keys come from anvil's own output."""

    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.rpc = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen([tool("anvil"), "--port", str(self.port), "--silent", "--chain-id", "31337"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        self.key = self._first_key()

    def _first_key(self) -> str:
        # anvil funds accounts derived from Foundry's public test mnemonic; account 0 deploys and anchors
        return subprocess.run([tool("cast"), "wallet", "private-key", TEST_MNEMONIC], capture_output=True, text=True,
                              timeout=30).stdout.strip()

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
