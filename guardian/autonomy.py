"""Risk-priced autonomy.

Guardian may act on its own only when the *expected loss* of being wrong is small:

    EL = P(error) x amount x (1 - reversibility)

P(error) comes from a calibrated classifier. On top of it, a conformal risk control
threshold gives a distribution-free guarantee on calibration-exchangeable data:

    P(action is executed autonomously AND it is bad) <= alpha

Autonomy is also *earned*: every user reversal halves the trust multiplier of that
category, and a streak of correct autonomous actions slowly restores it.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

from .ledger import Bank
from .models import REVERSIBILITY, Initiator, Payee, PaymentRequest, Rail
from .policy import PolicyDecision
from .synthetic import RISKY_CATEGORIES, generate_events

URGENCY_TERMS = [
    "urgent", "immediately", "right now", "today only", "within 1 hour", "within one hour", "police", "arrest",
    "warrant", "embassy", "consulate", "immigration", "deport", "visa", "fine", "penalty", "do not tell",
    "don't tell", "keep this secret", "kidnap", "safe account", "verification fee", "suspended", "blocked",
    "final notice", "tax office", "court", "lawsuit", "investigation",
]

FEATURE_NAMES = ["log_amount", "new_payee", "unverified", "international", "irreversible", "night", "urgency",
                 "inbound_request", "external_agent", "velocity", "share_of_balance", "risky_category"]


def urgency_score(*texts: str | None) -> float:
    blob = " ".join(t.lower() for t in texts if t)
    hits = sum(1 for term in URGENCY_TERMS if term in blob)
    return min(1.0, hits / 3)


def vectorize(e: dict) -> list[float]:
    rail = Rail(e["rail"])
    return [
        math.log10(e["amount"] + 1),
        float(e["new_payee"]),
        float(1 - e["verified"]),
        float(e["international"]),
        1.0 - REVERSIBILITY[rail],
        float(e["hour"] < 6 or e["hour"] >= 23),
        float(e["urgency"]),
        float(e["initiator"] == "inbound_request"),
        float(e["initiator"] == "external_agent"),
        min(1.0, e["velocity"] / 10),
        min(1.0, e["share_of_balance"]),
        float(e["category"] in RISKY_CATEGORIES),
    ]


def conformal_threshold(p_cal: np.ndarray, y_cal: np.ndarray, alpha: float) -> float:
    """Conformal risk control (Angelopoulos et al., 2022) for the monotone loss
    L_i(t) = 1{p_i <= t and y_i = 1}: pick the largest t with (n*R(t) + 1)/(n + 1) <= alpha."""
    n = len(p_cal)
    best = 0.0
    for t in np.unique(np.concatenate([[0.0], p_cal])):
        risk = np.sum((p_cal <= t) & (y_cal == 1))
        if (risk + 1) / (n + 1) <= alpha:
            best = float(t)
        else:
            break
    return best


class RiskModel:
    def __init__(self, seed: int = 7, alpha: float = 0.02, p_cap: float = 0.10):
        events, y = generate_events(9000, seed)
        X = np.array([vectorize(e) for e in events])
        idx = np.random.default_rng(seed).permutation(len(y))
        tr, cal, te = idx[:5000], idx[5000:7000], idx[7000:]
        base = LogisticRegression(max_iter=2000)
        self.clf = CalibratedClassifierCV(base, method="isotonic", cv=3).fit(X[tr], y[tr])
        p_cal = self.clf.predict_proba(X[cal])[:, 1]
        self.alpha = alpha
        self.conformal = conformal_threshold(p_cal, y[cal], alpha)
        # A hard cap only shrinks the autonomous set, so the conformal guarantee still holds.
        self.threshold = min(self.conformal, p_cap)
        p_te = self.clf.predict_proba(X[te])[:, 1]
        auto = p_te <= self.threshold
        self.report = {
            "train_size": int(len(tr)), "calibration_size": int(len(cal)), "test_size": int(len(te)),
            "alpha": alpha, "conformal_threshold": round(self.conformal, 4), "p_cap": p_cap,
            "threshold": round(self.threshold, 4),
            "test_auto_and_bad_rate": round(float(np.mean(auto & (y[te] == 1))), 4),
            "test_autonomy_rate": round(float(np.mean(auto)), 4),
            "test_bad_caught_rate": round(float(np.mean(~auto[y[te] == 1])), 4),
            "test_auc": round(_auc(p_te, y[te]), 4),
        }

    def p_error(self, event: dict) -> float:
        return float(self.clf.predict_proba(np.array([vectorize(event)]))[0, 1])


def _auc(p: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(p)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


Decision = Literal["auto", "confirm", "block"]


class RiskAssessment(BaseModel):
    decision: Decision
    p_error: float
    amount: int
    reversibility: float
    expected_loss: int
    el_limit: int
    trust: float
    conformal_threshold: float
    alpha: float
    reasons: list[str]
    features: dict


class AutonomyEngine:
    def __init__(self, model: RiskModel, el_limit: int = 1000, block_p: float = 0.8, home_country: str = "AU"):
        self.model = model
        self.base_el_limit = el_limit
        self.block_p = block_p
        self.home_country = home_country
        self.trust: dict[str, float] = {}
        self.streak: dict[str, int] = {}

    def event_from_request(self, req: PaymentRequest, payee: Payee, is_new: bool, bank: Bank) -> dict:
        return {
            "amount": req.amount / 100,
            "new_payee": int(is_new),
            "verified": int(payee.verified),
            "international": int(req.rail == Rail.INTERNATIONAL_TRANSFER or payee.country != self.home_country),
            "rail": req.rail.value,
            "hour": bank.clock.now().hour,
            "urgency": urgency_score(req.source_message, req.memo),
            "initiator": req.initiator.value,
            "velocity": bank.payments_last_24h(),
            "share_of_balance": req.amount / max(bank.total, 1),
            "category": req.category,
        }

    def trust_for(self, category: str) -> float:
        return self.trust.get(category, 1.0)

    def assess(self, req: PaymentRequest, payee: Payee, is_new: bool, policy: PolicyDecision, bank: Bank) -> RiskAssessment:
        event = self.event_from_request(req, payee, is_new, bank)
        p = self.model.p_error(event)
        rev = REVERSIBILITY[req.rail]
        el = int(round(p * req.amount * (1 - rev)))
        trust = self.trust_for(req.category)
        limit = int(self.base_el_limit * trust)
        reasons: list[str] = []
        if not policy.allowed:
            decision: Decision = "block"
            reasons.append(policy.summary)
        elif p >= self.block_p:
            decision = "block"
            reasons.append(f"Fraud probability {p:.0%} is above the block threshold {self.block_p:.0%}.")
        else:
            auto_ok = True
            if req.initiator == Initiator.INBOUND_REQUEST:
                auto_ok = False
                reasons.append("Money requested by a third party is never paid without the owner.")
            if p > self.model.threshold:
                auto_ok = False
                reasons.append(f"P(error) {p:.1%} is above the conformal threshold {self.model.threshold:.1%}.")
            if el > limit:
                auto_ok = False
                reasons.append(f"Expected loss ${el / 100:.2f} is above the autonomy limit ${limit / 100:.2f}.")
            decision = "auto" if auto_ok else "confirm"
            if auto_ok:
                reasons.append(f"Low risk: P(error) {p:.1%}, expected loss ${el / 100:.2f} within limit ${limit / 100:.2f}.")
        return RiskAssessment(decision=decision, p_error=round(p, 4), amount=req.amount, reversibility=rev,
                              expected_loss=el, el_limit=limit, trust=round(trust, 3),
                              conformal_threshold=round(self.model.threshold, 4), alpha=self.model.alpha,
                              reasons=reasons, features=event)

    def record_outcome(self, category: str, reverted: bool) -> float:
        t = self.trust_for(category)
        if reverted:
            t = max(0.125, t * 0.5)
            self.streak[category] = 0
        else:
            self.streak[category] = self.streak.get(category, 0) + 1
            if self.streak[category] % 5 == 0:
                t = min(2.0, t * 1.25)
        self.trust[category] = t
        return t
