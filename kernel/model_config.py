"""Model and provider settings for the LLM gateway, read from config/models.yaml and the environment.

The file holds model names, prices and the *names* of the environment variables with keys and
endpoints; the keys themselves live only in the environment. Environment variables override the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
LEVELS = (1, 2, 3)


class ConfigError(Exception):
    pass


class MissingKey(ConfigError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    provider: str          # anthropic | openai_compat
    model: str
    in_usd: float          # per million input tokens
    out_usd: float         # per million output tokens
    effort: bool = False   # the model accepts the effort setting

    @property
    def supports_effort(self) -> bool:
        return self.effort


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    api_key_env: str
    base_url_env: str | None = None
    default_base_url: str | None = None

    def api_key(self, env: Mapping[str, str] | None = None) -> str:
        key = (env if env is not None else os.environ).get(self.api_key_env, "")
        if not key:
            raise MissingKey(f"provider {self.name}: set {self.api_key_env} in the environment or .env")
        return key

    def base_url(self, env: Mapping[str, str] | None = None) -> str | None:
        value = (env if env is not None else os.environ).get(self.base_url_env) if self.base_url_env else None
        return value or self.default_base_url


@dataclass(frozen=True)
class ModelConfig:
    levels: dict[int, ModelSpec]
    providers: dict[str, ProviderSpec]
    cache_write_factor: float
    cache_read_factor: float
    legacy: dict[str, str]
    source: dict[int, str]     # where each level came from: "file" or "env"


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes", "on"}


def load(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> ModelConfig:
    env = os.environ if env is None else env
    path = Path(path or env.get("GUARDIAN_MODELS_FILE") or DEFAULT_FILE)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as e:
        raise ConfigError(f"cannot read model config {path}: {e}") from e

    providers = {name: ProviderSpec(name, p["api_key_env"], p.get("base_url_env"), p.get("default_base_url"))
                 for name, p in (raw.get("providers") or {}).items()}
    levels: dict[int, ModelSpec] = {}
    source: dict[int, str] = {}
    for lvl in LEVELS:
        f = (raw.get("levels") or {}).get(lvl) or {}
        spec = ModelSpec(f.get("provider", ""), f.get("model", ""), float(f.get("in_usd", 0)),
                         float(f.get("out_usd", 0)), _bool(f.get("effort", False)))
        spec, overridden = _from_env(lvl, spec, env)
        if not spec.provider or not spec.model:
            raise ConfigError(f"level {lvl}: provider and model are required ({path})")
        if spec.provider not in providers:
            raise ConfigError(f"level {lvl}: unknown provider {spec.provider!r}; known: {sorted(providers)}")
        levels[lvl] = spec
        source[lvl] = "env" if overridden else "file"

    cache = raw.get("cache") or {}
    legacy = {k: str(v) for k, v in (raw.get("legacy_prototype") or {}).items()}
    return ModelConfig(levels, providers, float(cache.get("write_factor", 1.25)),
                       float(cache.get("read_factor", 0.1)), legacy, source)


def _from_env(lvl: int, spec: ModelSpec, env: Mapping[str, str]) -> tuple[ModelSpec, bool]:
    changes: dict[str, Any] = {}
    if v := env.get(f"GUARDIAN_MODEL_L{lvl}"):
        if ":" in v:   # short form provider:model[:in_usd[:out_usd]]
            provider, model, *prices = v.split(":")
            changes.update(provider=provider, model=model)
            if prices:
                changes["in_usd"] = float(prices[0])
            if len(prices) > 1:
                changes["out_usd"] = float(prices[1])
        else:
            changes["model"] = v
    for var, field, cast in ((f"GUARDIAN_PROVIDER_L{lvl}", "provider", str),
                             (f"GUARDIAN_PRICE_L{lvl}_IN", "in_usd", float),
                             (f"GUARDIAN_PRICE_L{lvl}_OUT", "out_usd", float),
                             (f"GUARDIAN_EFFORT_L{lvl}", "effort", _bool)):
        if (v := env.get(var)) not in (None, ""):
            changes[field] = cast(v)
    return (replace(spec, **changes), True) if changes else (spec, False)
