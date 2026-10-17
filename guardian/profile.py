"""Demo persona: Mia Novak, an international master's student in Melbourne.

All people, organisations, accounts and numbers are fictional. Phone numbers are from the
Australian ACMA range reserved for fiction; domains use the reserved .example TLD.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .autonomy import RiskModel
from .clock import SimClock
from .core import Guardian
from .crypto import KeyPair
from .duress import DuressGuard, TrustedContact
from .ledger import Bank
from .llm import LLM
from .models import Envelope, EnvelopeRules, Payee, Rail

NORMAL_PIN = "1379"
DURESS_PIN = "9731"
START = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
LONG_AGO = datetime(2026, 2, 1, tzinfo=timezone.utc)

_MODEL: RiskModel | None = None


def risk_model() -> RiskModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = RiskModel()
    return _MODEL


def build_demo(llm: LLM | None = None, seeded_keys: bool = True) -> Guardian:
    clock = SimClock(START)
    bank = Bank(clock, owner="Mia Novak")
    payees = {
        "greenway": Payee(id="payee_greenway", name="Greenway Property Management", account="payid:rent@greenwaypm.example",
                          category="rent", verified=True, first_seen=LONG_AGO),
        "southgate": Payee(id="payee_southgate", name="Southgate University", account="GB33SGUN20000012345678",
                           country="GB", category="education", verified=True, first_seen=LONG_AGO),
        "powergrid": Payee(id="payee_powergrid", name="PowerGrid Energy", account="payid:billing@powergrid.example",
                           category="utilities", verified=True, first_seen=LONG_AGO),
        "streamflix": Payee(id="payee_streamflix", name="StreamFlix", account="card-merchant:streamflix",
                            category="subscriptions", verified=True, first_seen=LONG_AGO),
        "freshmart": Payee(id="payee_freshmart", name="FreshMart Supermarket", account="card-merchant:freshmart",
                           category="groceries", verified=True, first_seen=LONG_AGO),
        "metro": Payee(id="payee_metro", name="Metro Transit", account="card-merchant:metro", category="transport",
                       verified=True, first_seen=LONG_AGO),
        "techmart": Payee(id="payee_techmart", name="TechMart Australia", account="payid:orders@techmart.example",
                          category="shopping", verified=True, first_seen=LONG_AGO),
    }
    for p in payees.values():
        bank.register_payee(p)

    envs = [
        Envelope(id="rent", name="Rent", purpose="Monthly rent to Greenway", protected=True, priority=10,
                 rules=EnvelopeRules(allowed_payees=["payee_greenway"], allowed_rails=[Rail.DOMESTIC_TRANSFER],
                                     day_window=(1, 5), max_per_tx=90000)),
        Envelope(id="tuition", name="Tuition", purpose="Tuition instalments to Southgate University", protected=True,
                 priority=10, rules=EnvelopeRules(allowed_payees=["payee_southgate"],
                                                  allowed_rails=[Rail.INTERNATIONAL_TRANSFER, Rail.DOMESTIC_TRANSFER])),
        Envelope(id="bills", name="Bills", purpose="Utilities and subscriptions", priority=20,
                 rules=EnvelopeRules(allowed_payees=["payee_powergrid", "payee_streamflix"], max_per_tx=15000)),
        Envelope(id="food", name="Food", purpose="Groceries and eating out", priority=30,
                 rules=EnvelopeRules(allowed_categories=["groceries", "restaurant"], allowed_rails=[Rail.CARD],
                                     max_per_tx=15000, allow_unverified_payees=True, new_payee_cap=8000)),
        Envelope(id="transport", name="Transport", purpose="Public transport", priority=30,
                 rules=EnvelopeRules(allowed_categories=["transport"], allowed_rails=[Rail.CARD], max_per_tx=8000)),
        Envelope(id="savings", name="Savings", purpose="Emergency fund", protected=True, priority=90,
                 rules=EnvelopeRules(locked_until=START + timedelta(days=60), allowed_rails=[Rail.INTERNAL])),
        Envelope(id="free", name="Free money", purpose="Anything else, with limits", priority=80,
                 rules=EnvelopeRules(allow_unverified_payees=True, max_per_tx=15000, new_payee_cap=15000,
                                     allowed_rails=[Rail.CARD, Rail.DOMESTIC_TRANSFER])),
    ]
    for e in envs:
        bank.add_envelope(e)

    duress = DuressGuard(NORMAL_PIN, DURESS_PIN, [
        TrustedContact(name="Anna Novak (sister)", channel="sms", address="+61 491 570 158"),
        TrustedContact(name="Southgate Campus Security", channel="campus_security", address="security@southgate.example"),
    ])
    owner_key = KeyPair.from_seed("demo-owner-mia") if seeded_keys else KeyPair()
    guardian_key = KeyPair.from_seed("demo-guardian") if seeded_keys else KeyPair()
    secrets = ["Mia Novak", "mia.novak@student.southgate.example", "+61 491 570 159", "PA4471902",
               "21 Example Lane", NORMAL_PIN, DURESS_PIN, "4000 0566 5566 5556"]
    g = Guardian(bank, duress, owner_key, guardian_key, llm=llm, risk_model=risk_model(), owner_secrets=secrets,
                 location="Southgate University library, Melbourne")
    g.budget_plan = [("rent", 90000), ("tuition", 30000), ("bills", 12000), ("food", 40000), ("transport", 8000),
                     ("savings", 50000), ("free", 30000)]
    g.bills = [
        {"payee_id": "payee_greenway", "amount": 90000, "day": 1, "rail": "domestic_transfer", "category": "rent",
         "memo": "October rent", "envelope": "rent"},
        {"payee_id": "payee_powergrid", "amount": 6800, "day": 3, "rail": "domestic_transfer", "category": "utilities",
         "memo": "Electricity", "envelope": "bills"},
        {"payee_id": "payee_streamflix", "amount": 1599, "day": 3, "rail": "card", "category": "subscriptions",
         "memo": "StreamFlix monthly", "envelope": "bills"},
    ]
    return g


def pay_day(g: Guardian) -> list[dict]:
    """Advance to 1 October and receive the scholarship and part-time salary."""
    g.clock.set(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    alloc = g.receive_income(200000, "Southgate Scholarship")
    alloc += g.receive_income(60000, "Campus Café payroll")
    return alloc
