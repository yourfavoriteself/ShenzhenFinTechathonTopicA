"""Finds instructions aimed at AI agents and pressure tactics inside untrusted text: bills, emails,
web pages. Findings never unlock anything; they only add reasons to stop or ask."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment
from pydantic import BaseModel

INSTRUCTION_PATTERNS = [
    (r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,30}"
     r"\b(instruction|prompt|rule|guideline)s?", "override attempt"),
    (r"\b(ai|a\.i\.|assistants?|agents?|llms?|chatbots?|gpt|models?|bots?)\b.{0,100}\b(must|should|always|need to|"
     r"are required|choose|select|buy|purchase|add|recommend|include|send|enter|trust|pay)\b",
     "instruction addressed to AI agents"),
    (r"\b(send|enter|submit|provide|post|share)\b.{0,80}\b(card number|cvv|cvc|password|one-time code|otp|"
     r"seed phrase|card details)\b", "credential exfiltration"),
    (r"\b(user|customer|owner|client)\b.{0,20}\b(has )?(already )?(agreed|consented|approved|authori[sz]ed)\b",
     "fake consent claim"),
    (r"\b(do not|don't|without)\b.{0,30}\b(ask|asking|confirm|confirmation|notify|telling)\b", "confirmation bypass"),
]

URGENCY_PATTERNS = [r"\burgent\b", r"\bimmediately\b", r"within \d+ (hours?|minutes?)", r"final notice",
                    r"disconnect", r"last chance", r"account will be (blocked|closed|suspended)", r"срочно",
                    r"немедленно", r"отключ"]

HIDDEN_STYLE = [
    (r"display\s*:\s*none", "display:none"), (r"visibility\s*:\s*hidden", "visibility:hidden"),
    (r"opacity\s*:\s*0(\.0+)?\s*(;|$)", "opacity:0"), (r"font-size\s*:\s*[01](px|pt|em)?\s*(;|$)", "zero font size"),
    (r"color\s*:\s*(#fff(fff)?|white)\b", "white-on-white text"), (r"(left|top)\s*:\s*-\d{3,}px", "off-screen position"),
]


class Finding(BaseModel):
    where: str
    technique: str
    text: str
    severity: str             # high | medium


def instruction(text: str) -> str | None:
    low = " ".join(text.lower().split())
    for pattern, label in INSTRUCTION_PATTERNS:
        if re.search(pattern, low):
            return label
    return None


def urgency(text: str) -> list[str]:
    low = text.lower()
    return [m.group(0) for p in URGENCY_PATTERNS if (m := re.search(p, low))]


def _clean(text: str) -> str:
    return " ".join(text.split())[:240]


def scan_text(text: str, where: str = "document") -> list[Finding]:
    """Plain text or e-mail body: hidden HTML comments are high severity, visible lines medium."""
    out: list[Finding] = []
    for c in re.findall(r"<!--(.*?)-->", text, flags=re.S):
        if label := instruction(c):
            out.append(Finding(where=where, technique=f"hidden comment ({label})", text=_clean(c), severity="high"))
    visible = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    for line in visible.splitlines():
        if line.strip() and (label := instruction(line)):
            out.append(Finding(where=where, technique=f"visible text ({label})", text=_clean(line), severity="medium"))
    return out


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


def scan_html(html: str, where: str = "page") -> list[Finding]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Finding] = []
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        if label := instruction(str(c)):
            out.append(Finding(where=where, technique=f"HTML comment ({label})", text=_clean(str(c)), severity="high"))
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        if content and (label := instruction(content)):
            out.append(Finding(where=where, technique=f"meta tag ({label})", text=_clean(content), severity="high"))
    for img in soup.find_all("img"):
        for attr in ("alt", "title"):
            val = img.get(attr, "")
            if val and (label := instruction(val)):
                out.append(Finding(where=where, technique=f"image {attr} ({label})", text=_clean(val), severity="high"))
    seen: set[str] = set()
    for el in soup.find_all(True):
        own = " ".join(s for s in el.find_all(string=True, recursive=False) if not isinstance(s, Comment)).strip()
        if not own or el.name in ("script", "style", "title") or own in seen:
            continue
        if label := instruction(own):
            seen.add(own)
            hidden = _hidden_reason(el)
            out.append(Finding(where=where, technique=f"{'hidden text: ' + hidden if hidden else 'visible text'} "
                                                      f"({label})", text=_clean(own),
                               severity="high" if hidden else "medium"))
    return out
