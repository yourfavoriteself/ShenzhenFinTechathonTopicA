"""Agent-of-Agents Firewall.

Third-party shopping agents never see card numbers and never decide alone. The owner's
intent is captured as a signed mandate; any cart an agent proposes must fit inside it;
the pages that influenced the agent are scanned for hidden instructions; and payment is
possible only with a single-use token bound to one merchant and one amount.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from pydantic import BaseModel, Field

from .crypto import KeyPair, verify_obj
from .models import new_id

# --------------------------------------------------------------------------- models


class IntentMandate(BaseModel):
    mandate_id: str = Field(default_factory=lambda: new_id("intent"))
    owner: str
    agent_id: str
    description: str
    keywords: list[str]
    allowed_categories: list[str]
    max_total: int
    max_items: int = 1
    recurring_allowed: bool = False
    expires_at: datetime

    def body(self) -> dict:
        return self.model_dump(mode="json")


class SignedMandate(BaseModel):
    mandate: IntentMandate
    signature: str
    public_key: str


class Product(BaseModel):
    sku: str
    name: str
    description: str = ""
    price: int
    category: str
    seller: str
    seller_account: str
    seller_verified: bool
    seller_age_days: int
    recurring: bool = False
    page: str = ""


class Cart(BaseModel):
    cart_id: str = Field(default_factory=lambda: new_id("cart"))
    agent_id: str
    mandate_id: str
    items: list[Product]
    visited_pages: list[str]
    rationale: str = ""

    @property
    def total(self) -> int:
        return sum(i.price for i in self.items)


class Finding(BaseModel):
    page: str
    technique: str
    text: str
    severity: str  # high | medium


class Check(BaseModel):
    name: str
    ok: bool
    detail: str
    hard: bool = True


class CartVerdict(BaseModel):
    approved: bool
    checks: list[Check]
    findings: list[Finding]
    summary: str


class PaymentToken(BaseModel):
    token_id: str = Field(default_factory=lambda: new_id("tok"))
    mandate_id: str
    agent_id: str
    merchant_account: str
    max_amount: int
    expires_at: datetime
    used: bool = False
    signature: str = ""


# --------------------------------------------------------------------------- shop


def load_shop(shop_dir: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(shop_dir.glob("*.html"))}


def parse_products(page_name: str, html: str) -> list[Product]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for el in soup.select("[data-sku]"):
        out.append(Product(
            sku=el["data-sku"], name=el.get("data-name", ""), description=el.get("data-description", ""),
            price=int(el["data-price"]), category=el.get("data-category", "unknown"), seller=el.get("data-seller", ""),
            seller_account=el.get("data-seller-account", ""), seller_verified=el.get("data-seller-verified") == "true",
            seller_age_days=int(el.get("data-seller-age-days", "0")), recurring=el.get("data-recurring") == "true",
            page=page_name))
    return out


# --------------------------------------------------------------------------- scanner

INSTRUCTION_PATTERNS = [
    (r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,30}\b(instruction|prompt|rule|guideline)s?",
     "override attempt"),
    (r"\b(ai|a\.i\.|assistants?|agents?|llms?|chatbots?|gpt|models?|bots?)\b.{0,100}\b(must|should|always|need to|are required|choose|select|buy|purchase|add|recommend|include|send|enter|trust)\b",
     "instruction addressed to AI agents"),
    (r"\b(add|include)\b.{0,80}\b(to (the )?cart|subscription|protection plan|plan)\b", "cart manipulation"),
    (r"\b(send|enter|submit|provide|post|share)\b.{0,80}\b(card number|cvv|cvc|password|one-time code|otp|seed phrase|card details)\b",
     "credential exfiltration"),
    (r"\b(user|customer|owner)\b.{0,20}\b(already )?(agreed|consented|approved|authori[sz]ed)\b", "fake consent claim"),
]

HIDDEN_STYLE = [
    (r"display\s*:\s*none", "display:none"), (r"visibility\s*:\s*hidden", "visibility:hidden"),
    (r"opacity\s*:\s*0(\.0+)?\s*(;|$)", "opacity:0"), (r"font-size\s*:\s*[01](px|pt|em)?\s*(;|$)", "zero font size"),
    (r"color\s*:\s*(#fff(fff)?|white)\b", "white-on-white text"), (r"(left|top)\s*:\s*-\d{3,}px", "off-screen position"),
]


def _hidden_reason(el) -> str | None:
    node = el
    while node is not None and getattr(node, "name", None):
        if node.has_attr("hidden") or node.get("aria-hidden") == "true":
            return "hidden attribute"
        style = (node.get("style") or "").lower()
        for pattern, label in HIDDEN_STYLE:
            if re.search(pattern, style):
                return label
        node = node.parent
    return None


def _clean(text: str) -> str:
    return " ".join(text.split())[:240]


def _instruction(text: str) -> str | None:
    low = " ".join(text.lower().split())
    for pattern, label in INSTRUCTION_PATTERNS:
        if re.search(pattern, low):
            return label
    return None


def scan_page(page: str, html: str) -> list[Finding]:
    soup = BeautifulSoup(html, "html.parser")
    findings: list[Finding] = []
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        if label := _instruction(str(c)):
            findings.append(Finding(page=page, technique=f"HTML comment ({label})", text=_clean(str(c)), severity="high"))
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        if content and (label := _instruction(content)):
            findings.append(Finding(page=page, technique=f"meta tag ({label})", text=_clean(content), severity="high"))
    for img in soup.find_all("img"):
        for attr in ("alt", "title"):
            val = img.get(attr, "")
            if val and (label := _instruction(val)):
                findings.append(Finding(page=page, technique=f"image {attr} ({label})", text=_clean(val), severity="high"))
    seen: set[str] = set()
    for el in soup.find_all(True):
        own = " ".join(s for s in el.find_all(string=True, recursive=False) if not isinstance(s, Comment)).strip()
        if not own or el.name in ("script", "style", "title"):
            continue
        label = _instruction(own)
        if not label or own in seen:
            continue
        seen.add(own)
        hidden = _hidden_reason(el)
        if hidden:
            findings.append(Finding(page=page, technique=f"hidden text: {hidden} ({label})", text=_clean(own), severity="high"))
        else:
            findings.append(Finding(page=page, technique=f"visible text ({label})", text=_clean(own), severity="medium"))
    return findings


# --------------------------------------------------------------------------- simulated agents


class HonestAgent:
    """Picks the cheapest product that fits the intent. Reads structured product data only."""

    agent_id = "agent:shopbot-honest"

    def propose(self, intent: IntentMandate, shop: dict[str, str]) -> Cart:
        products = [p for name, html in shop.items() for p in parse_products(name, html)]
        fits = [p for p in products if p.category in intent.allowed_categories and p.price <= intent.max_total
                and not p.recurring and _matches(intent, p)]
        best = min(fits, key=lambda p: p.price)
        return Cart(agent_id=self.agent_id, mandate_id=intent.mandate_id, items=[best], visited_pages=list(shop),
                    rationale=f"Cheapest product matching '{intent.description}'.")


class NaiveAgent:
    """Simulates a vulnerable LLM shopping agent that obeys instructions found in page text
    (the failure mode measured by Microsoft's Magentic Marketplace and observed in the wild)."""

    agent_id = "agent:shopbot-naive"

    def propose(self, intent: IntentMandate, shop: dict[str, str]) -> Cart:
        for name, html in shop.items():
            if any(f.technique.startswith(("hidden", "HTML comment", "meta")) and "AI" in f.technique for f in scan_page(name, html)):
                items = parse_products(name, html)
                return Cart(agent_id=self.agent_id, mandate_id=intent.mandate_id, items=items, visited_pages=list(shop),
                            rationale=f"The seller page says it is verified for AI assistants; followed its recommendation on {name}.")
        return HonestAgent().propose(intent, shop).model_copy(update={"agent_id": self.agent_id})


def _matches(intent: IntentMandate, p: Product) -> bool:
    blob = f"{p.name} {p.description}".lower()
    return any(k.lower() in blob for k in intent.keywords)


# --------------------------------------------------------------------------- firewall


class AgentFirewall:
    def __init__(self, guardian_key: KeyPair, clock, llm=None):
        self.key = guardian_key
        self.clock = clock
        self.llm = llm
        self.tokens: dict[str, PaymentToken] = {}
        self.agent_trust: dict[str, float] = {}

    def sign_mandate(self, mandate: IntentMandate, owner_key: KeyPair) -> SignedMandate:
        return SignedMandate(mandate=mandate, signature=owner_key.sign_obj(mandate.body()), public_key=owner_key.public_b64)

    def verify_cart(self, signed: SignedMandate, cart: Cart, shop: dict[str, str]) -> CartVerdict:
        m = signed.mandate
        checks: list[Check] = []
        sig_ok = verify_obj(signed.public_key, m.body(), signed.signature)
        checks.append(Check(name="Mandate signature", ok=sig_ok, detail="signed by the owner's key" if sig_ok else "signature invalid"))
        alive = self.clock.now() < m.expires_at
        checks.append(Check(name="Mandate not expired", ok=alive, detail=f"expires {m.expires_at:%Y-%m-%d %H:%M}"))
        checks.append(Check(name="Agent authorised", ok=cart.agent_id == m.agent_id or m.agent_id == "*",
                            detail=f"cart from {cart.agent_id}"))
        checks.append(Check(name="Within budget", ok=cart.total <= m.max_total,
                            detail=f"cart ${cart.total / 100:.2f} vs limit ${m.max_total / 100:.2f}"))
        checks.append(Check(name="Item count", ok=len(cart.items) <= m.max_items,
                            detail=f"{len(cart.items)} item(s), mandate allows {m.max_items}"))
        bad_cat = [i.name for i in cart.items if i.category not in m.allowed_categories]
        checks.append(Check(name="Categories allowed", ok=not bad_cat,
                            detail="all items in allowed categories" if not bad_cat else f"not allowed: {', '.join(bad_cat)}"))
        recurring = [i.name for i in cart.items if i.recurring]
        checks.append(Check(name="No recurring charges", ok=m.recurring_allowed or not recurring,
                            detail="none" if not recurring else f"recurring: {', '.join(recurring)}"))
        off_intent = [i.name for i in cart.items if not _matches(m, i)]
        checks.append(Check(name="Matches the owner's intent", ok=not off_intent,
                            detail="every item matches the request" if not off_intent else f"not requested: {', '.join(off_intent)}"))
        decision_pages = sorted({i.page for i in cart.items})
        findings = [f for p in decision_pages for f in scan_page(p, shop.get(p, ""))]
        high = [f for f in findings if f.severity == "high"]
        checks.append(Check(name="Decision provenance clean", ok=not high,
                            detail="no hidden instructions on the pages that drove the choice" if not high
                            else f"{len(high)} hidden instruction(s) on {', '.join(decision_pages)}"))
        risky_sellers = [i.seller for i in cart.items if not i.seller_verified and i.seller_age_days < 30]
        checks.append(Check(name="Seller reputation", ok=not risky_sellers, hard=False,
                            detail="sellers verified" if not risky_sellers else f"new unverified seller: {', '.join(set(risky_sellers))}"))
        approved = all(c.ok for c in checks if c.hard)
        self._update_trust(cart.agent_id, approved, bool(high))
        failed = [c.name for c in checks if not c.ok]
        summary = "Cart approved: it fits the signed intent." if approved else f"Cart rejected: {', '.join(failed)}."
        return CartVerdict(approved=approved, checks=checks, findings=findings, summary=summary)

    def _update_trust(self, agent_id: str, approved: bool, manipulated: bool) -> None:
        t = self.agent_trust.get(agent_id, 0.7)
        t = min(1.0, t + 0.05) if approved else max(0.0, t - (0.4 if manipulated else 0.15))
        self.agent_trust[agent_id] = round(t, 2)

    def clean_alternative(self, mandate: IntentMandate, shop: dict[str, str]) -> Cart | None:
        clean = {name: html for name, html in shop.items() if not any(f.severity == "high" for f in scan_page(name, html))}
        try:
            return HonestAgent().propose(mandate, clean)
        except ValueError:
            return None

    # --- tokens -------------------------------------------------------------
    def issue_token(self, signed: SignedMandate, cart: Cart) -> PaymentToken:
        merchant = cart.items[0].seller_account
        tok = PaymentToken(mandate_id=signed.mandate.mandate_id, agent_id=cart.agent_id, merchant_account=merchant,
                           max_amount=cart.total, expires_at=self.clock.now() + timedelta(minutes=15))
        tok.signature = self.key.sign_obj(tok.model_dump(mode="json", exclude={"signature", "used"}))
        self.tokens[tok.token_id] = tok
        return tok

    def redeem(self, token_id: str | None, merchant_account: str, amount: int) -> tuple[bool, str]:
        tok = self.tokens.get(token_id or "")
        if not tok:
            return False, "external agents can pay only with a Guardian-issued token"
        if tok.used:
            return False, "token already used"
        if self.clock.now() > tok.expires_at:
            return False, "token expired"
        if tok.merchant_account != merchant_account:
            return False, "token is bound to a different merchant"
        if amount > tok.max_amount:
            return False, "amount exceeds the token limit"
        if not verify_obj(self.key.public_b64, tok.model_dump(mode="json", exclude={"signature", "used"}), tok.signature):
            return False, "token signature invalid"
        tok.used = True
        return True, "token valid: single use, bound to merchant and amount"
