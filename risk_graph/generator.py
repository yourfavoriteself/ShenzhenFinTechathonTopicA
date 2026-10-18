"""Synthetic intra-bank transfer graph with four planted mule schemes and honest look-alikes.

Normal life: salaries from employers (a legitimate fan-out), rent to landlords and payments to
shops (legitimate fan-in), small transfers between friends and family, loans paid back (cycles of
two). Planted schemes, whose intermediary accounts are labelled as mules:

  ring          4-6 fresh accounts pass similar sums around a cycle within hours, several times;
  structuring   one account splits a large sum into many transfers just below 15 000 RUB;
  fan_in_out    a fresh collector receives from many victims and forwards almost all within hours;
  chain         a sum hops through 4-6 fresh accounts, losing a small cut at each hop.

Victims are ordinary people and stay unlabelled. To keep the test honest, 40% of mule accounts
are bought aged accounts and half of them carry cover activity (a salary, shop payments). All amounts are in kopecks.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from pydantic import BaseModel

SCHEMES = ("ring", "structuring", "fan_in_out", "chain")
THRESHOLD = 1_500_000          # 15 000 RUB: transfers just below it are a structuring sign


class Transfer(BaseModel):
    src: str
    dst: str
    amount: int
    ts: datetime


class TransferGraph(BaseModel):
    transfers: list[Transfer]
    opened: dict[str, datetime]
    kind: dict[str, str]             # person | employer | merchant | landlord | mule | exit
    label: dict[str, str | None]     # scheme name for mule accounts, None otherwise
    as_of: datetime

    def accounts(self) -> list[str]:
        return list(self.opened)


class _Gen:
    def __init__(self, seed: int, as_of: datetime):
        self.r = random.Random(seed)
        self.as_of = as_of
        self.t: list[Transfer] = []
        self.opened: dict[str, datetime] = {}
        self.kind: dict[str, str] = {}
        self.label: dict[str, str | None] = {}
        self._n = 0

    def acct(self, kind: str, age_days: tuple[int, int], label: str | None = None, fixed: str | None = None) -> str:
        self._n += 1
        if kind == "mule" and self.r.random() < 0.4:      # a bought, aged account
            age_days = (120, 1500)
        a = fixed or f"408178105{self.r.randrange(10**11):011d}"
        self.opened[a] = self.as_of - timedelta(days=self.r.randint(*age_days), hours=self.r.randint(0, 23))
        self.kind[a], self.label[a] = kind, label
        return a

    def send(self, src: str, dst: str, amount: int, ts: datetime) -> None:
        if ts <= self.as_of and amount > 0:
            self.t.append(Transfer(src=src, dst=dst, amount=int(amount), ts=ts))

    def when(self, days_back: float) -> datetime:
        return self.as_of - timedelta(days=days_back, minutes=self.r.randint(0, 1439))


def generate(seed: int, as_of: datetime, people: int = 500, days: int = 60, schemes: int = 6,
             plant: dict[str, str] | None = None) -> TransferGraph:
    """`plant` fixes account numbers for the demo: {"collector": "...", "ordinary": "..."}."""
    g, r = _Gen(seed, as_of), random.Random(seed + 1)
    plant = plant or {}
    folks = [g.acct("person", (60, 3000), fixed=plant.get("ordinary") if i == 0 else None) for i in range(people)]
    employers = [g.acct("employer", (900, 5000)) for _ in range(12)]
    shops = [g.acct("merchant", (300, 5000)) for _ in range(25)]
    landlords = [g.acct("landlord", (200, 4000)) for _ in range(30)]
    work = {p: r.choice(employers) for p in folks}
    home = {p: r.choice(landlords) for p in folks if r.random() < 0.4}
    friends = {p: r.sample(folks, 4) for p in folks}
    for d in range(days, 0, -1):
        day = as_of - timedelta(days=d)
        for p in folks:
            if day.day in (10, 25):
                g.send(work[p], p, r.randint(30_000, 120_000) * 100, day.replace(hour=9))
            if day.day == 20 and p in home:
                g.send(p, home[p], r.randint(25_000, 60_000) * 100, day.replace(hour=r.randint(10, 20)))
            if r.random() < 0.08:
                g.send(p, r.choice(shops), r.randint(500, 20_000) * 100, day + timedelta(hours=r.randint(8, 22)))
            if r.random() < 0.03:
                f, amt = r.choice(friends[p]), r.randint(300, 8_000) * 100
                ts = day + timedelta(hours=r.randint(8, 23))
                g.send(p, f, amt, ts)
                if r.random() < 0.3:                      # a loan paid back: an honest two-cycle
                    g.send(f, p, amt, ts + timedelta(days=r.randint(1, 10)))
        for s in shops:
            if r.random() < 0.15:
                g.send(s, r.choice(employers), r.randint(50_000, 300_000) * 100, day.replace(hour=12))
    victims = [p for p in folks if p != plant.get("ordinary")]
    kinds = list(SCHEMES) * (schemes // len(SCHEMES) + 1)
    r.shuffle(kinds)
    for i, k in enumerate(kinds[:schemes]):
        fixed = plant.get("collector") if k == "fan_in_out" and i == kinds.index("fan_in_out") else None
        getattr(_Schemes, k)(g, r, victims, fixed)
    for a, k in list(g.kind.items()):                     # cover activity on half of the mule accounts
        if k == "mule" and r.random() < 0.5:
            for _ in range(r.randint(3, 10)):
                ts = g.when(r.uniform(1, days))
                if r.random() < 0.5:
                    g.send(r.choice(employers), a, r.randint(20_000, 60_000) * 100, ts)
                else:
                    g.send(a, r.choice(shops), r.randint(500, 15_000) * 100, ts)
    return TransferGraph(transfers=sorted(g.t, key=lambda x: x.ts), opened=g.opened, kind=g.kind, label=g.label,
                         as_of=as_of)


class _Schemes:
    @staticmethod
    def ring(g: _Gen, r: random.Random, victims: list[str], fixed: str | None) -> None:
        ring = [g.acct("mule", (5, 40), "ring") for _ in range(r.randint(4, 6))]
        g.send(r.choice(victims), ring[0], r.randint(200_000, 600_000) * 100, g.when(r.uniform(8, 20)))
        base = r.randint(150_000, 500_000) * 100
        start = g.when(r.uniform(2, 7))
        for lap in range(r.randint(2, 4)):
            ts = start + timedelta(hours=lap * r.randint(10, 30))
            for a, b in zip(ring, ring[1:] + ring[:1]):
                ts += timedelta(minutes=r.randint(20, 180))
                g.send(a, b, int(base * r.uniform(0.97, 1.03)), ts)

    @staticmethod
    def structuring(g: _Gen, r: random.Random, victims: list[str], fixed: str | None) -> None:
        src = g.acct("mule", (10, 90), "structuring")
        g.send(r.choice(victims), src, r.randint(150_000, 400_000) * 100, g.when(r.uniform(5, 15)))
        outs = [g.acct("mule", (3, 60), "structuring") for _ in range(r.randint(3, 6))]
        ts = g.when(r.uniform(1, 4))
        for _ in range(r.randint(8, 16)):
            ts += timedelta(minutes=r.randint(15, 240))
            g.send(src, r.choice(outs), r.randint(13_600, 14_990) * 100, ts)
        ex = g.acct("exit", (100, 2000))
        for o in outs:
            g.send(o, ex, r.randint(20_000, 40_000) * 100, ts + timedelta(hours=r.randint(1, 12)))

    @staticmethod
    def fan_in_out(g: _Gen, r: random.Random, victims: list[str], fixed: str | None) -> None:
        col = g.acct("mule", (3, 25), "fan_in_out", fixed=fixed)
        exits = [g.acct("mule", (5, 60), "fan_in_out") for _ in range(r.randint(3, 5))]
        sink = g.acct("exit", (100, 2000))
        start = g.when(r.uniform(1, 5))
        for v in r.sample(victims, r.randint(10, 25)):
            ts = start + timedelta(hours=r.uniform(0, 40))
            amt = r.randint(20_000, 300_000) * 100
            g.send(v, col, amt, ts)
            left = int(amt * r.uniform(0.9, 0.98))
            for e in r.sample(exits, 2):
                part = left // 2
                g.send(col, e, part, ts + timedelta(minutes=r.randint(15, 180)))
                g.send(e, sink, part, ts + timedelta(hours=r.randint(2, 10)))

    @staticmethod
    def chain(g: _Gen, r: random.Random, victims: list[str], fixed: str | None) -> None:
        hops = [g.acct("mule", (4, 50), "chain") for _ in range(r.randint(4, 6))]
        amt = r.randint(200_000, 900_000) * 100
        ts = g.when(r.uniform(1, 10))
        g.send(r.choice(victims), hops[0], amt, ts)
        for a, b in zip(hops, hops[1:]):
            ts += timedelta(hours=r.uniform(1, 6))
            amt = int(amt * r.uniform(0.97, 0.99))
            g.send(a, b, amt, ts)
        g.send(hops[-1], g.acct("exit", (100, 2000)), int(amt * 0.98), ts + timedelta(hours=2))
