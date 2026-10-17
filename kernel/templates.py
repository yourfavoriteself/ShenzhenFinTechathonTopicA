"""Payment templates: a task solved once is repeated by code (model level 0).

After a confirmed and verified payment the kernel saves who was paid, how much and from where.
Next time the dispatcher matches the client's words against templates without any model; the
amount and payee are re-published as bank values, so the pipeline still checks everything.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from .mandate import Action, new_id


class Template(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tpl"))
    client_id: str
    action: Action
    payee: str
    payee_name: str
    amount: int
    funding_source: str
    keywords: list[str]
    created: datetime
    uses: int = 0
    mandate_id: str | None = None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9]+", text.lower()))


class TemplateStore:
    def __init__(self):
        self.items: dict[str, Template] = {}

    def save(self, t: Template) -> Template:
        for old in list(self.items.values()):
            if old.client_id == t.client_id and old.action == t.action and old.payee == t.payee:
                t.id, t.uses = old.id, old.uses
        self.items[t.id] = t
        return t

    def for_client(self, client_id: str) -> list[Template]:
        return [t for t in self.items.values() if t.client_id == client_id]

    def match(self, client_id: str, action: Action, text: str) -> Template | None:
        words = _words(text)
        for t in self.for_client(client_id):
            if t.action == action and any(k in words for k in t.keywords):
                return t
        return None
