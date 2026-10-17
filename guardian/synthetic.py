"""Synthetic, labelled payment events used to train and calibrate the risk model.

No real customer data is used anywhere in this project. The generator encodes publicly
documented fraud typologies (impersonation, invoice redirection, investment scams,
account takeover, agent mistakes) next to ordinary student spending.
"""

from __future__ import annotations

import numpy as np

# (name, weight, is_scam)
SCENARIOS = [
    ("bill_verified", 0.20, False),
    ("card_shopping", 0.25, False),
    ("card_new_merchant", 0.12, False),
    ("friend_transfer", 0.08, False),
    ("tuition_international", 0.04, False),
    ("agent_purchase_ok", 0.06, False),
    ("impersonation_scam", 0.07, True),
    ("invoice_redirect", 0.05, True),
    ("investment_scam", 0.05, True),
    ("account_takeover", 0.04, True),
    ("agent_manipulated", 0.04, True),
]

RISKY_CATEGORIES = {"crypto", "gambling", "gift_cards", "investment", "government_fee", "unknown"}


def _event(rng: np.random.Generator, kind: str) -> dict:
    e = {"amount": 50.0, "new_payee": 0, "verified": 1, "international": 0, "rail": "card", "hour": 14,
         "urgency": 0.0, "initiator": "user", "velocity": 1, "share_of_balance": 0.02, "category": "groceries"}
    u = rng.random
    if kind == "bill_verified":
        e.update(amount=rng.uniform(40, 950), rail="domestic_transfer", initiator="guardian", category="rent",
                 share_of_balance=rng.uniform(0.05, 0.45), hour=int(rng.integers(8, 20)))
    elif kind == "card_shopping":
        e.update(amount=rng.uniform(3, 120), hour=int(rng.integers(7, 24)), category=rng.choice(["groceries", "restaurant", "transport"]))
    elif kind == "card_new_merchant":
        e.update(amount=rng.uniform(5, 150), new_payee=1, verified=0, category=rng.choice(["electronics", "clothing", "restaurant"]))
    elif kind == "friend_transfer":
        e.update(amount=rng.uniform(10, 200), new_payee=int(u() < 0.5), verified=int(u() < 0.5),
                 rail="domestic_transfer", category="personal")
    elif kind == "tuition_international":
        e.update(amount=rng.uniform(500, 3000), international=1, rail="international_transfer", category="education",
                 share_of_balance=rng.uniform(0.3, 0.8))
    elif kind == "agent_purchase_ok":
        e.update(amount=rng.uniform(20, 150), initiator="external_agent", new_payee=int(u() < 0.4),
                 verified=int(u() < 0.7), category="electronics")
    elif kind == "impersonation_scam":
        e.update(amount=rng.uniform(300, 3000), new_payee=1, verified=0, initiator="inbound_request",
                 urgency=rng.uniform(0.5, 1.0), international=int(u() < 0.7),
                 rail=rng.choice(["international_transfer", "crypto", "gift_card", "domestic_transfer"]),
                 share_of_balance=rng.uniform(0.3, 1.0), category=rng.choice(["government_fee", "unknown"]))
    elif kind == "invoice_redirect":
        e.update(amount=rng.uniform(200, 1500), new_payee=1, verified=0, initiator="inbound_request",
                 urgency=rng.uniform(0.2, 0.7), rail="domestic_transfer", category=rng.choice(["rent", "education"]),
                 share_of_balance=rng.uniform(0.1, 0.6))
    elif kind == "investment_scam":
        e.update(amount=rng.uniform(100, 5000), new_payee=1, verified=0, rail=rng.choice(["crypto", "international_transfer"]),
                 international=1, urgency=rng.uniform(0.1, 0.6), category="investment",
                 share_of_balance=rng.uniform(0.2, 1.0))
    elif kind == "account_takeover":
        e.update(amount=rng.uniform(200, 2500), new_payee=1, verified=0, hour=int(rng.integers(0, 6)),
                 velocity=int(rng.integers(4, 12)), rail=rng.choice(["domestic_transfer", "crypto"]),
                 share_of_balance=rng.uniform(0.3, 1.0), category="unknown")
    elif kind == "agent_manipulated":
        e.update(amount=rng.uniform(20, 300), initiator="external_agent", new_payee=1, verified=0,
                 category=rng.choice(["unknown", "electronics", "gift_cards"]), urgency=rng.uniform(0.0, 0.3))
    return e


def generate_events(n: int = 6000, seed: int = 7) -> tuple[list[dict], np.ndarray]:
    rng = np.random.default_rng(seed)
    names = [s[0] for s in SCENARIOS]
    weights = np.array([s[1] for s in SCENARIOS])
    scam = {s[0]: s[2] for s in SCENARIOS}
    events, labels = [], []
    for _ in range(n):
        kind = rng.choice(names, p=weights / weights.sum())
        e = _event(rng, kind)
        e["amount"] = float(e["amount"])
        # Label noise: some "legit-looking" events are mistakes and some scams look benign.
        bad = scam[kind]
        if (bad and rng.random() < 0.05) or (not bad and rng.random() < 0.01):
            bad = not bad
        events.append(e)
        labels.append(int(bad))
    return events, np.array(labels)
