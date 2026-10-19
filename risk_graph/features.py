"""Per-account features on the transfer graph, computed from transfers known at `as_of` only."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from .generator import THRESHOLD, Transfer

NAMES = ("age_days", "n_in", "n_out", "uniq_in", "uniq_out", "log_in", "log_out", "pass_through",
         "hold_hours", "below_threshold", "in_cycle", "burst_48h", "out_cv", "young_peers")
NEW_ACCOUNT = timedelta(days=30)


class Index:
    def __init__(self, transfers: list[Transfer], opened: dict[str, datetime], as_of: datetime):
        self.as_of, self.opened = as_of, opened
        self.inc: dict[str, list[Transfer]] = defaultdict(list)
        self.out: dict[str, list[Transfer]] = defaultdict(list)
        for t in transfers:
            if t.ts <= as_of:
                self.inc[t.dst].append(t)
                self.out[t.src].append(t)

    def in_cycle(self, a: str, max_len: int = 6, window: timedelta = timedelta(days=7)) -> bool:
        """A directed cycle back to `a` of length 2..max_len with every hop inside the window."""
        since = self.as_of - window
        stack = [(a, 0, {a})]
        while stack:
            node, depth, seen = stack.pop()
            if depth >= max_len:
                continue
            for t in self.out.get(node, ()):
                if t.ts < since:
                    continue
                if t.dst == a and depth >= 1:
                    # a two-cycle of the same amount days apart is a loan paid back, not circulation
                    if depth > 1 or any(abs(x.amount - t.amount) > 0.05 * t.amount or (t.ts - x.ts) < timedelta(days=1)
                                        for x in self.out.get(a, ()) if x.dst == node):
                        return True
                if t.dst not in seen and len(self.out.get(t.dst, ())) < 60:
                    stack.append((t.dst, depth + 1, seen | {t.dst}))
        return False

    def features(self, a: str) -> list[float]:
        inc, out = self.inc.get(a, []), self.out.get(a, [])
        opened = self.opened.get(a, self.as_of)
        s_in, s_out = sum(t.amount for t in inc), sum(t.amount for t in out)
        holds = []
        for t in out:
            prev = [x.ts for x in inc if x.ts <= t.ts]
            if prev:
                holds.append((t.ts - max(prev)).total_seconds() / 3600)
        both = inc + out
        below = sum(1 for t in both if 0.9 * THRESHOLD <= t.amount < THRESHOLD) / len(both) if both else 0.0
        burst, times = 0, sorted((t.ts, t.src) for t in inc)
        for i, (ts, _) in enumerate(times):
            burst = max(burst, len({s for x, s in times[i:] if x - ts <= timedelta(hours=48)}))
        amounts = [t.amount for t in out]
        cv = statistics.pstdev(amounts) / statistics.mean(amounts) if len(amounts) > 1 else 1.0
        peers = {t.src for t in inc} | {t.dst for t in out}
        young = sum(1 for p in peers if self.as_of - self.opened.get(p, self.as_of) < NEW_ACCOUNT) / len(peers) \
            if peers else 0.0
        return [(self.as_of - opened).days, len(inc), len(out), len({t.src for t in inc}), len({t.dst for t in out}),
                math.log1p(s_in / 100), math.log1p(s_out / 100), min(s_in, s_out) / max(s_in, 1),
                statistics.median(holds) if holds else 24 * 30, below, float(self.in_cycle(a)), burst, cv, young]


def explain(f: dict[str, float]) -> list[str]:
    """Plain-language reasons for the client card; thresholds chosen for readability, not for the score."""
    out = []
    if f["age_days"] < 30:
        out.append(f"the account was opened {int(f['age_days'])} days ago")
    if f["burst_48h"] >= 5:
        out.append(f"it received money from {int(f['burst_48h'])} different people within 48 hours")
    if f["pass_through"] > 0.8 and f["hold_hours"] < 12:
        out.append(f"it sends on {round(f['pass_through'] * 100)}% of what it receives, within "
                   f"{max(1, round(f['hold_hours']))} hours")
    if f["in_cycle"]:
        out.append("money goes around a circle of accounts and comes back")
    if f["below_threshold"] > 0.4:
        out.append("many transfers just below $181.05")
    if f["young_peers"] > 0.5 and f["n_out"]:
        out.append("most of its counterparties are new accounts")
    return out
