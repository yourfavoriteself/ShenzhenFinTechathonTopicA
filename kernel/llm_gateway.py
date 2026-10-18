"""LLM gateway: the only way any component reaches a model.

Model ladder: level 1 (small), 2 (medium), 3 (large, escalation only); level 0 is kernel code and
never gets here. Requests start at the cheapest level and escalate on facts: a response that fails
its schema, low confidence, or a refusal. The gateway holds the API keys, enforces a hard spend
cap in rubles plus a per-task budget, refuses any prompt that contains a card number or a vault
secret, and records responses so that every demo can be replayed without keys.

Modes: stub (deterministic test responders), record (live + save), replay (saved only), live.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ValidationError

from .crypto import obj_hash


class Mode(str, Enum):
    STUB = "stub"
    RECORD = "record"
    REPLAY = "replay"
    LIVE = "live"


@dataclass(frozen=True)
class ModelSpec:
    provider: str          # anthropic | openai_compat
    model: str
    in_usd: float          # per million input tokens
    out_usd: float         # per million output tokens

    @property
    def supports_effort(self) -> bool:
        return self.provider == "anthropic" and "haiku" not in self.model


# Prices are kept on the high side so the cap is never exceeded; check them against the
# current price list before a live run.
DEFAULT_LADDER = {
    1: ModelSpec("anthropic", "claude-haiku-4-5", 1.0, 5.0),
    2: ModelSpec("anthropic", "claude-sonnet-5-5", 3.0, 15.0),
    3: ModelSpec("anthropic", "claude-opus-5-5", 5.0, 25.0),
}
CACHE_WRITE_FACTOR = 1.25
CACHE_READ_FACTOR = 0.1
MAX_LEVEL = 3


class BudgetExceeded(Exception):
    pass


class TaskBudgetExceeded(BudgetExceeded):
    pass


class SecretLeak(Exception):
    pass


class ReplayMiss(Exception):
    pass


class LLMFailure(Exception):
    pass


class LLMResult(BaseModel):
    text: str
    parsed: dict[str, Any] | None = None
    role: str
    model: str
    level: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_rub: float = 0.0
    source: str
    estimated: bool = False
    task_id: str = "default"
    escalations: list[str] = []


class SpendLedger:
    """Persistent record of real money spent on our own keys."""

    def __init__(self, path: Path, budget_rub: float):
        self.path = path
        self.budget_rub = budget_rub
        data = json.loads(path.read_text()) if path.exists() else {}
        self.total_rub: float = data.get("total_rub", 0.0)
        self.by_task: dict[str, float] = data.get("by_task", {})

    def reserve(self, task_id: str, estimate_rub: float, task_budget_rub: float) -> None:
        if self.total_rub + estimate_rub > self.budget_rub:
            raise BudgetExceeded(f"spend cap {self.budget_rub:.0f} RUB would be exceeded "
                                 f"(spent {self.total_rub:.2f}, next call up to {estimate_rub:.2f})")
        if self.by_task.get(task_id, 0.0) + estimate_rub > task_budget_rub:
            raise TaskBudgetExceeded(f"task {task_id} budget {task_budget_rub:.0f} RUB would be exceeded")

    def commit(self, task_id: str, cost_rub: float) -> None:
        self.total_rub += cost_rub
        self.by_task[task_id] = self.by_task.get(task_id, 0.0) + cost_rub
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"total_rub": round(self.total_rub, 4), "by_task": self.by_task}, indent=1))


_PAN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


class SecretGuard:
    """Blocks prompts that carry card numbers or any secret registered by the vault."""

    def __init__(self):
        self._secrets: set[str] = set()

    def register(self, secret: str) -> None:
        if len(secret) >= 4:
            self._secrets.add(secret)

    def check(self, text: str) -> None:
        for m in _PAN.finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if 13 <= len(digits) <= 19 and _luhn(digits):
                raise SecretLeak("card number in a model prompt")
        if any(s in text for s in self._secrets):
            raise SecretLeak("vault secret in a model prompt")


Responder = Callable[[dict[str, Any]], str]


class LLMGateway:
    def __init__(self, mode: Mode | str | None = None, *, budget_rub: float | None = None,
                 task_budget_rub: float | None = None, usd_rub: float | None = None,
                 runs_dir: Path | None = None, spend_path: Path | None = None,
                 ladder: dict[int, ModelSpec] | None = None):
        env = os.environ
        self.mode = Mode(mode or env.get("GUARDIAN_LLM_MODE", "replay"))
        self.usd_rub = usd_rub or float(env.get("GUARDIAN_USD_RUB", "100"))
        self.task_budget_rub = task_budget_rub or float(env.get("GUARDIAN_TASK_BUDGET_RUB", "50"))
        self.runs_dir = runs_dir or Path(env.get("GUARDIAN_RUNS_DIR", Path(__file__).parent.parent / "evidence/runs/llm"))
        self.ledger = SpendLedger(spend_path or Path(env.get("GUARDIAN_SPEND_FILE", "~/.guardian/spend.json")).expanduser(),
                                  budget_rub or float(env.get("GUARDIAN_TOKEN_BUDGET_RUB", "2000")))
        self.ladder = ladder or _ladder_from_env(DEFAULT_LADDER)
        self.replay_fallback = env.get("GUARDIAN_REPLAY_FALLBACK", "stub") == "stub"
        self.guard = SecretGuard()
        self.stubs: dict[str, Responder] = {}
        self.providers: dict[str, Callable[[ModelSpec, dict[str, Any]], dict[str, Any]]] = {
            "anthropic": _call_anthropic, "openai_compat": _call_openai_compat}
        self.log: list[LLMResult] = []
        self.sent: list[dict[str, Any]] = []   # every prompt that passed the guard, for leak audits

    def register_stub(self, role: str, responder: Responder) -> None:
        self.stubs[role] = responder

    # ------------------------------------------------------------------ public

    def call(self, role: str, system: str, user: str, *, level: int = 1, task_id: str = "default",
             schema: type[BaseModel] | None = None, max_tokens: int = 1024, effort: str = "low",
             min_confidence: float | None = None) -> LLMResult:
        """Run a request on the cheapest adequate level; escalate on schema failure, low confidence or refusal."""
        if schema is not None:
            system = f"{system}\n\nReply with a single JSON object that matches this JSON schema, and nothing else:\n" \
                     f"{json.dumps(schema.model_json_schema(), sort_keys=True)}"
        self.guard.check(system + "\n" + user)
        self.sent.append({"role": role, "task_id": task_id, "text": system + "\n" + user})
        escalations: list[str] = []
        for lvl in range(max(1, level), MAX_LEVEL + 1):
            for attempt in (1, 2):
                req = {"role": role, "level": lvl, "system": system, "user": user, "max_tokens": max_tokens,
                       "effort": effort}
                res = self._raw(req, task_id)
                res.task_id = task_id
                if res.text == "" and res.source != "stub":
                    escalations.append(f"level {lvl}: empty or refused response")
                    continue
                if schema is None:
                    return self._done(res, escalations)
                try:
                    parsed = schema.model_validate(_extract_json(res.text)).model_dump(mode="json")
                except (ValueError, ValidationError) as e:
                    escalations.append(f"level {lvl} attempt {attempt}: schema failure ({type(e).__name__})")
                    continue
                conf = parsed.get("confidence")
                if min_confidence is not None and isinstance(conf, (int, float)) and conf < min_confidence \
                        and lvl < MAX_LEVEL:
                    escalations.append(f"level {lvl}: low confidence {conf}")
                    break
                res.parsed = parsed
                return self._done(res, escalations)
        raise LLMFailure(f"{role}: no valid response up to level {MAX_LEVEL}: {escalations}")

    def task_cost(self, task_id: str) -> dict[str, Any]:
        rs = [r for r in self.log if r.task_id == task_id]
        return {"calls": len(rs), "levels": sorted({r.level for r in rs}),
                "input_tokens": sum(r.input_tokens for r in rs), "output_tokens": sum(r.output_tokens for r in rs),
                "cost_rub": round(sum(r.cost_rub for r in rs), 4), "estimated": any(r.estimated for r in rs)}

    def report(self) -> dict[str, Any]:
        by_level: dict[int, dict[str, float]] = {}
        for r in self.log:
            s = by_level.setdefault(r.level, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_rub": 0.0})
            s["calls"] += 1
            s["input_tokens"] += r.input_tokens
            s["output_tokens"] += r.output_tokens
            s["cost_rub"] += r.cost_rub
        return {"mode": self.mode.value, "spent_total_rub": round(self.ledger.total_rub, 2),
                "cap_rub": self.ledger.budget_rub, "by_level": by_level}

    # ------------------------------------------------------------------ internals

    def _done(self, res: LLMResult, escalations: list[str]) -> LLMResult:
        res.escalations = escalations
        self.log.append(res)
        return res

    def _raw(self, req: dict[str, Any], task_id: str) -> LLMResult:
        spec = self.ladder[req["level"]]
        base = dict(role=req["role"], model=spec.model, level=req["level"])
        if self.mode == Mode.STUB:
            return self._stub(req, spec, base)
        key = obj_hash({"model": spec.model, "system": req["system"], "user": req["user"],
                        "max_tokens": req["max_tokens"], "effort": req["effort"]})
        path = self.runs_dir / f"{key}.json"
        if self.mode == Mode.REPLAY:
            if not path.exists():
                if self.replay_fallback and req["role"] in self.stubs:
                    return self._stub(req, spec, base, source="stub-fallback")
                raise ReplayMiss(f"no recorded response for role {req['role']} at level {req['level']}")
            rec = json.loads(path.read_text())
            return LLMResult(**{**rec, "source": "replay", "escalations": []})
        estimate = self._cost(spec, len(req["system"] + req["user"]) / 3, req["max_tokens"], 0, 0)
        self.ledger.reserve(task_id, estimate, self.task_budget_rub)
        out = self.providers[spec.provider](spec, req)
        cost = self._cost(spec, out["input_tokens"], out["output_tokens"], out.get("cache_read", 0),
                          out.get("cache_write", 0))
        self.ledger.commit(task_id, cost)
        res = LLMResult(text=out["text"], input_tokens=out["input_tokens"], output_tokens=out["output_tokens"],
                        cache_read_tokens=out.get("cache_read", 0), cache_write_tokens=out.get("cache_write", 0),
                        cost_rub=round(cost, 4), source="live", **base)
        if self.mode == Mode.RECORD:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(res.model_dump_json(indent=1))
        return res

    def _stub(self, req: dict[str, Any], spec: ModelSpec, base: dict[str, Any], source: str = "stub") -> LLMResult:
        """Deterministic responder; token counts are estimated (4 characters a token) and never charged."""
        responder = self.stubs.get(req["role"])
        if responder is None:
            raise LLMFailure(f"no stub registered for role {req['role']}")
        text = responder(req)
        inp, out = len(req["system"] + req["user"]) // 4, len(text) // 4
        return LLMResult(text=text, source=source, input_tokens=inp, output_tokens=out,
                         cost_rub=round(self._cost(spec, inp, out, 0, 0), 4), estimated=True, **base)

    def _cost(self, spec: ModelSpec, inp: float, out: float, cache_read: float, cache_write: float) -> float:
        usd = (inp * spec.in_usd + cache_read * spec.in_usd * CACHE_READ_FACTOR
               + cache_write * spec.in_usd * CACHE_WRITE_FACTOR + out * spec.out_usd) / 1_000_000
        return usd * self.usd_rub


def _ladder_from_env(default: dict[int, ModelSpec]) -> dict[int, ModelSpec]:
    """GUARDIAN_MODEL_L1="openai_compat:deepseek-chat:0.3:1.2" overrides a level."""
    ladder = dict(default)
    for lvl in (1, 2, 3):
        if v := os.environ.get(f"GUARDIAN_MODEL_L{lvl}"):
            provider, model, *prices = v.split(":")
            base = default[lvl]
            ladder[lvl] = ModelSpec(provider, model, float(prices[0]) if prices else base.in_usd,
                                    float(prices[1]) if len(prices) > 1 else base.out_usd)
    return ladder


def _extract_json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


def _call_anthropic(spec: ModelSpec, req: dict[str, Any]) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = {}
    if spec.supports_effort:
        kwargs["output_config"] = {"effort": req["effort"]}
    try:
        resp = client.messages.create(
            model=spec.model, max_tokens=req["max_tokens"],
            system=[{"type": "text", "text": req["system"], "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": req["user"]}], **kwargs)
    except anthropic.APIStatusError as e:
        raise LLMFailure(f"{spec.model}: HTTP {e.status_code}") from e
    u = resp.usage
    text = "" if resp.stop_reason == "refusal" else "".join(b.text for b in resp.content if b.type == "text")
    return {"text": text, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read": u.cache_read_input_tokens or 0, "cache_write": u.cache_creation_input_tokens or 0}


def _call_openai_compat(spec: ModelSpec, req: dict[str, Any]) -> dict[str, Any]:
    base = os.environ["OPENAI_COMPAT_BASE_URL"].rstrip("/")
    r = httpx.post(f"{base}/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {os.environ['OPENAI_COMPAT_API_KEY']}"},
                   json={"model": spec.model, "max_tokens": req["max_tokens"], "temperature": 0,
                         "messages": [{"role": "system", "content": req["system"]},
                                      {"role": "user", "content": req["user"]}]})
    if r.status_code != 200:
        raise LLMFailure(f"{spec.model}: HTTP {r.status_code}")
    d = r.json()
    return {"text": d["choices"][0]["message"]["content"] or "", "input_tokens": d["usage"]["prompt_tokens"],
            "output_tokens": d["usage"]["completion_tokens"]}
