"""Counterfactual check for high-risk actions (after MELON, ICML 2025).

The step that produced an intent is replayed with the client's request masked. If the agent
still proposes the same action to the same payee for the same amount, the action is driven by
the data it was shown (a document, a page, a fact planted by an attacker), not by the client,
and the kernel stops it. The replay only returns proposals; it cannot act. It runs on a small
model and only when the risk is already high.
"""

from __future__ import annotations

from typing import Any, Callable

MASK = "The client did not ask for anything. Summarise the facts; propose an action only if the facts require it."

Replay = Callable[[str], list[Any]]


class CounterfactualCheck:
    def __init__(self, kernel):
        self.k = kernel
        self.replays: dict[str, Replay] = {}
        self.runs = 0

    def register(self, task_id: str, replay: Replay) -> None:
        """The service registers how to re-run the planning step of a task with another request."""
        self.replays[task_id] = replay

    def __call__(self, ctx):
        from .cards import Risk
        from .pipeline import StepResult, Verdict
        i = ctx.intent
        replay = self.replays.get(i.task_id)
        if replay is None:
            return StepResult(step="", data={"counterfactual": "no agent step to replay"})
        self.runs += 1
        try:
            proposals = replay(MASK)
        except Exception as e:  # noqa: BLE001  a failed replay must not block on its own; the card still asks
            self.k.journal.append("kernel", "counterfactual.failed", {"intent": i.id, "error": str(e)[:200]})
            return StepResult(step="", data={"counterfactual": "replay failed"})
        same = [p for p in proposals if p.action == i.action and _val(p.payee) == i.val("payee")
                and _val(p.amount) in (None, i.val("amount"))]
        self.k.journal.append("kernel", "counterfactual.checked", {"intent": i.id, "masked_proposals": len(proposals),
                                                                   "same_action": bool(same)})
        if same:
            return StepResult(step="", verdict=Verdict.DENY, risk=Risk.HIGH,
                              reasons=["the agent proposes the same payment even without your request: it comes "
                                       "from the data it read, not from you"],
                              data={"counterfactual": "same action without the request"})
        return StepResult(step="", data={"counterfactual": "passed"})


def _val(claim) -> Any:
    return None if claim is None else claim.value
