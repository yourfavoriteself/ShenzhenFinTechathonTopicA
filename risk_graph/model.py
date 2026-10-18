"""Mule-account model: gradient boosting on graph features, trained and scored on CPU inside the bank.

Training graphs and the test graph come from different seeds, so the test accounts, victims and
schemes were never seen in training. The kernel uses the model as one more risk check before a
payment to an account number: a high score asks the client, and feeds the scam-call ladder (C9).
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from .features import NAMES, Index, explain
from .generator import SCHEMES, TransferGraph, generate

HIGH, MEDIUM = 0.7, 0.4
TRAIN_SEEDS = (101, 102, 103)
TEST_SEEDS = (201, 202, 203, 204, 205)


def dataset(g: TransferGraph) -> tuple[np.ndarray, np.ndarray, list[str]]:
    idx = Index(g.transfers, g.opened, g.as_of)
    accs = [a for a in g.accounts() if idx.inc.get(a) or idx.out.get(a)]
    X = np.array([idx.features(a) for a in accs], dtype=float)
    y = np.array([g.label[a] is not None for a in accs], dtype=int)
    return X, y, accs


@lru_cache(maxsize=4)
def train(as_of_iso: str, seeds: tuple[int, ...] = TRAIN_SEEDS) -> HistGradientBoostingClassifier:
    as_of = datetime.fromisoformat(as_of_iso)
    parts = [dataset(generate(s, as_of)) for s in seeds]
    X = np.vstack([p[0] for p in parts])
    y = np.concatenate([p[1] for p in parts])
    m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, class_weight="balanced", random_state=0)
    return m.fit(X, y)


def evaluate(as_of: datetime) -> dict[str, Any]:
    m = train(as_of.isoformat())
    runs = []
    for seed in TEST_SEEDS:
        g = generate(seed, as_of)
        X, y, accs = dataset(g)
        p = m.predict_proba(X)[:, 1]
        pred = p >= HIGH
        hard = np.array([g.kind[a] in ("employer", "merchant", "landlord") for a in accs])
        per = {}
        for s in SCHEMES:
            mask = np.array([g.label[a] == s for a in accs])
            per[s] = (int(pred[mask].sum()), int(mask.sum()))
        runs.append({"seed": seed, "accounts": len(accs), "mules": int(y.sum()), "roc_auc": float(roc_auc_score(y, p)),
                     "precision": float(precision_score(y, pred, zero_division=0)),
                     "recall": float(recall_score(y, pred)), "fpr": float(pred[y == 0].mean()),
                     "fp_hard": int(pred[hard].sum()), "per": per})

    def mean(k: str) -> float:
        return round(float(np.mean([r[k] for r in runs])), 3)
    per_scheme = {s: round(sum(r["per"][s][0] for r in runs) / max(1, sum(r["per"][s][1] for r in runs)), 3)
                  for s in SCHEMES}
    return {"train_seeds": list(TRAIN_SEEDS), "test_seeds": list(TEST_SEEDS), "threshold": HIGH,
            "accounts_per_graph": runs[0]["accounts"], "mules_total": sum(r["mules"] for r in runs),
            "roc_auc": mean("roc_auc"), "precision": mean("precision"), "recall": mean("recall"),
            "false_positive_rate": round(float(np.mean([r["fpr"] for r in runs])), 4),
            "false_positives_on_employers_merchants_landlords": sum(r["fp_hard"] for r in runs),
            "recall_per_scheme": per_scheme,
            "per_seed": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "per"}
                         for r in runs]}


class GraphRisk:
    """Scores a payee account on the bank's own transfer graph; the data never leaves the kernel."""

    def __init__(self, graph: TransferGraph):
        self.graph = graph
        self.model = train(graph.as_of.isoformat())
        self.index = Index(graph.transfers, graph.opened, graph.as_of)

    def score(self, account: str) -> tuple[float, list[str]] | None:
        if account not in self.graph.opened:
            return None
        f = self.index.features(account)
        p = float(self.model.predict_proba(np.array([f]))[0, 1])
        return p, explain(dict(zip(NAMES, f)))

    def _account(self, intent) -> str | None:
        payee = intent.val("payee")
        return payee.removeprefix("acct:") if isinstance(payee, str) and payee.startswith("acct:") else None

    def points(self, intent) -> tuple[int, list[str]]:
        """Contribution to the scam-call ladder (C9)."""
        acc = self._account(intent)
        r = self.score(acc) if acc else None
        if r is None or r[0] < MEDIUM:
            return 0, []
        return (3 if r[0] >= HIGH else 1), [f"the recipient looks like a money-mule account ({r[0]:.2f})"]

    def check(self, ctx):
        from kernel.cards import Risk
        from kernel.pipeline import StepResult, Verdict
        acc = self._account(ctx.intent)
        r = self.score(acc) if acc else None
        if r is None:
            return StepResult(step="")
        p, why = r
        data = {"graph_risk": round(p, 3)}
        if p >= HIGH:
            return StepResult(step="", verdict=Verdict.ASK, risk=Risk.HIGH,
                              reasons=[f"the recipient account looks like a money mule (graph score {p:.2f})"] + why,
                              data=data)
        if p >= MEDIUM:
            return StepResult(step="", verdict=Verdict.ASK, risk=Risk.MEDIUM,
                              reasons=[f"the recipient account has unusual transfer patterns (graph score {p:.2f})"],
                              data=data)
        return StepResult(step="", data=data)
