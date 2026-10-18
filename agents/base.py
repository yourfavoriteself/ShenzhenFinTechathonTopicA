"""What every agent holds: a passport-backed session to the kernel and access to the model ladder.
Agents never hold bank credentials, card numbers or signing keys."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from kernel.core import Kernel
from kernel.intent import Claim, IntentProposal
from kernel.llm_gateway import LLMGateway
from kernel.passport import AgentSession, Role
from kernel.pipeline import Decision

RECIPE_VERSION = "stage3"


def session(kernel: Kernel, gateway: LLMGateway, role: Role, level: int, instructions: str, client_id: str,
            task_id: str, ttl: timedelta = timedelta(hours=1)) -> AgentSession:
    model = gateway.ladder[level].model if level > 0 else "kernel-code"
    return kernel.passports.issue(role, model, level, instructions, client_id, task_id, recipe_version=RECIPE_VERSION,
                                  ttl=ttl)


def propose(kernel: Kernel, s: AgentSession, proposal: IntentProposal) -> Decision:
    return kernel.submit(s.request("propose_intent", {"intent": proposal.model_dump(mode="json")}))


def claim(fact: dict[str, Any] | None) -> Claim | None:
    """Agents cite facts by reference; the value travels with its reference and the kernel re-checks both."""
    return None if fact is None else Claim(value=fact["value"], ref=fact["ref"])
