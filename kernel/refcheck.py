"""Reference check for model-written text: every number must come from the kernel.

The LLM only words the answer; the kernel hands it the facts. Before a reply reaches the client,
each number in it is matched against the facts it was given. Any number the kernel did not supply
means the reply is replaced by the kernel's own template.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from pydantic import BaseModel

NUMBER = re.compile(r"(?<![\w.])\d[\d  ]*(?:[.,]\d+)?")


class RefCheck(BaseModel):
    ok: bool
    unknown: list[str] = []


def _norm(s: str) -> str:
    s = re.sub(r"[  ]", "", s).replace(",", ".")
    s = s.rstrip(".")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def numbers(text: str) -> list[str]:
    return [_norm(m.group(0)) for m in NUMBER.finditer(text) if _norm(m.group(0))]


def _flatten(facts: Any) -> Iterable[str]:
    if isinstance(facts, dict):
        for v in facts.values():
            yield from _flatten(v)
    elif isinstance(facts, (list, tuple, set)):
        for v in facts:
            yield from _flatten(v)
    elif facts is not None and not isinstance(facts, bool):
        yield str(facts)


def allowed_numbers(facts: Any) -> set[str]:
    out: set[str] = set()
    for s in _flatten(facts):
        out.update(numbers(s))
        for part in re.findall(r"\d+", s):
            out.add(part.lstrip("0") or "0")
    return out


def check(text: str, facts: Any, small: int = 31) -> RefCheck:
    """Numbers up to `small` (days, counts) pass if they appear anywhere in the facts; others must match exactly."""
    allowed = allowed_numbers(facts)
    unknown = [n for n in numbers(text) if n not in allowed]
    return RefCheck(ok=not unknown, unknown=unknown)


def guarded(text: str | None, facts: Any, fallback: str) -> tuple[str, RefCheck]:
    if not text:
        return fallback, RefCheck(ok=True)
    r = check(text, facts)
    return (text if r.ok else fallback), r
