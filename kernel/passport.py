"""Agent passports: Know Your Agent for every call into the kernel.

Each agent session gets a passport signed by the kernel: role, model and level, hash of its
system instructions, recipe version and a session key. Every request is signed with the session
key, carries a fresh nonce and is checked against the role's allowed endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .clock import SimClock
from .crypto import KeyPair, canonical, sha256_hex, verify, verify_obj
from .mandate import new_id


class Role(str, Enum):
    DISPATCHER = "dispatcher"
    PLANNER = "planner"
    READER = "reader"
    BROWSER = "browser"
    CRITIC = "critic"
    EXECUTOR = "executor"


ROLE_ENDPOINTS: dict[Role, set[str]] = {
    Role.DISPATCHER: {"route", "lookup_templates", "propose_intent"},
    Role.PLANNER: {"propose_intent", "query_data", "request_grant"},
    Role.READER: {"submit_extraction"},
    Role.BROWSER: {"browser_command", "report_state"},
    Role.CRITIC: set(),
    Role.EXECUTOR: {"execute"},
}


class Passport(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ps"))
    role: Role
    model: str
    model_level: int
    instructions_hash: str
    recipe_version: str | None = None
    client_id: str
    task_id: str
    session_key: str
    issued_at: datetime
    exp: datetime
    kernel_sig: str = ""

    def body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"kernel_sig"})


class SignedRequest(BaseModel):
    passport_id: str
    endpoint: str
    body: dict[str, Any]
    nonce: str
    ts: datetime
    sig: str = ""

    def signed_part(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"sig"})


class AuthError(Exception):
    pass


class AgentSession:
    """What an agent process holds: its passport and session key. No other secrets."""

    def __init__(self, passport: Passport, key: KeyPair, clock: SimClock):
        self.passport = passport
        self._key = key
        self._clock = clock

    def request(self, endpoint: str, body: dict[str, Any]) -> SignedRequest:
        req = SignedRequest(passport_id=self.passport.id, endpoint=endpoint, body=body,
                            nonce=new_id("n"), ts=self._clock.now())
        req.sig = self._key.sign_obj(req.signed_part())
        return req


class PassportRegistry:
    MAX_SKEW = timedelta(minutes=5)

    def __init__(self, kernel_key: KeyPair, clock: SimClock):
        self._key = kernel_key
        self._clock = clock
        self._passports: dict[str, Passport] = {}
        self._revoked: set[str] = set()
        self._nonces: set[str] = set()

    def issue(self, role: Role, model: str, model_level: int, instructions: str, client_id: str, task_id: str,
              recipe_version: str | None = None, ttl: timedelta = timedelta(hours=1)) -> AgentSession:
        session_key = KeyPair()
        now = self._clock.now()
        p = Passport(role=role, model=model, model_level=model_level,
                     instructions_hash=sha256_hex(instructions.encode()), recipe_version=recipe_version,
                     client_id=client_id, task_id=task_id, session_key=session_key.public_b64,
                     issued_at=now, exp=now + ttl)
        p.kernel_sig = self._key.sign_obj(p.body())
        self._passports[p.id] = p
        return AgentSession(p, session_key, self._clock)

    def revoke(self, passport_id: str) -> None:
        self._revoked.add(passport_id)

    def revoke_agent(self, agent: str, key: Any) -> list[str]:
        """Revoke every live passport of one agent (same role, model and instructions)."""
        now = self._clock.now()
        ids = [p.id for p in self._passports.values()
               if key(p) == agent and p.id not in self._revoked and p.exp > now]
        self._revoked.update(ids)
        return ids

    def get(self, passport_id: str) -> Passport | None:
        return self._passports.get(passport_id)

    def check_passport(self, p: Passport) -> None:
        if not verify_obj(self._key.public_b64, p.body(), p.kernel_sig):
            raise AuthError("passport not signed by the kernel")
        if self._passports.get(p.id) != p:
            raise AuthError("unknown passport")
        if p.id in self._revoked:
            raise AuthError("passport revoked")
        if not (p.issued_at <= self._clock.now() <= p.exp):
            raise AuthError("passport expired")

    def authenticate(self, req: SignedRequest, presented: Passport | None = None) -> Passport:
        p = presented or self._passports.get(req.passport_id)
        if p is None:
            raise AuthError("unknown passport")
        if p.id != req.passport_id:
            raise AuthError("request signed for another passport")
        self.check_passport(p)
        if req.endpoint not in ROLE_ENDPOINTS[p.role]:
            raise AuthError(f"role {p.role.value} may not call {req.endpoint}")
        if not verify(p.session_key, canonical(req.signed_part()), req.sig):
            raise AuthError("request not signed with the session key")
        if abs(self._clock.now() - req.ts) > self.MAX_SKEW:
            raise AuthError("stale request")
        if req.nonce in self._nonces:
            raise AuthError("replayed request")
        self._nonces.add(req.nonce)
        return p
