"""Optional LLM client (any OpenAI-compatible endpoint).

The LLM is treated as an untrusted helper: it explains, classifies and drafts text.
It never moves money and its output never bypasses the policy engine. Every caller has a
deterministic fallback, so the whole system runs offline when no key is configured.

The model and the names of the key and endpoint variables come from the legacy_prototype section of
config/models.yaml (by default OPENAI_BASE_URL and OPENAI_API_KEY); GUARDIAN_LLM_MODEL overrides the
model, GUARDIAN_LLM=off forces offline mode.
"""

from __future__ import annotations

import json
import os

import httpx


class LLM:
    def __init__(self):
        from kernel.model_config import load

        cfg = load().legacy
        self.base = os.getenv(cfg.get("base_url_env", "OPENAI_BASE_URL"), "").rstrip("/")
        self.key = os.getenv(cfg.get("api_key_env", "OPENAI_API_KEY"), "")
        self.model = os.getenv("GUARDIAN_LLM_MODEL") or cfg.get("model", "")
        self.enabled = os.getenv("GUARDIAN_LLM", "auto").lower() != "off" and bool(self.base and self.key)
        self.calls = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return self.enabled

    def chat(self, system: str, user: str, max_tokens: int = 400, json_mode: bool = False) -> str | None:
        if not self.enabled:
            return None
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            self.calls += 1
            r = httpx.post(f"{self.base}/chat/completions", json=body, timeout=30,
                           headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            self.failures += 1
            return None

    def chat_json(self, system: str, user: str, max_tokens: int = 400) -> dict | None:
        out = self.chat(system, user, max_tokens=max_tokens, json_mode=True)
        if not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None


def quote_untrusted(text: str) -> str:
    """Wrap untrusted content so the model treats it as data, not instructions."""
    safe = text.replace("<<<", "«").replace(">>>", "»")
    return f"<<<UNTRUSTED_DATA\n{safe}\nUNTRUSTED_DATA>>>"
