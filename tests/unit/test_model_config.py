"""Models, prices and key variable names come from config/models.yaml and the environment, not from code."""

import re
from pathlib import Path

import pytest

from kernel import model_config
from kernel.llm_gateway import LLMGateway, MissingKey
from kernel.model_config import ConfigError, load

ROOT = Path(__file__).resolve().parents[2]


def test_file_defines_every_level_and_provider():
    c = load(env={})
    assert set(c.levels) == {1, 2, 3} and c.source == {1: "file", 2: "file", 3: "file"}
    assert {s.provider for s in c.levels.values()} <= set(c.providers)
    assert c.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"
    assert c.cache_read_factor < 1 < c.cache_write_factor


def test_model_name_from_the_environment_wins():
    c = load(env={"GUARDIAN_MODEL_L1": "claude-haiku-4-5-20251001", "GUARDIAN_PRICE_L2_OUT": "20"})
    base = load(env={})
    assert c.levels[1].model == "claude-haiku-4-5-20251001" and c.levels[1].in_usd == base.levels[1].in_usd
    assert c.levels[2].out_usd == 20 and c.source == {1: "env", 2: "env", 3: "file"}


def test_short_form_switches_provider_and_prices():
    c = load(env={"GUARDIAN_MODEL_L1": "openai_compat:deepseek-chat:0.3:1.2", "GUARDIAN_EFFORT_L1": "false"})
    s = c.levels[1]
    assert (s.provider, s.model, s.in_usd, s.out_usd, s.effort) == ("openai_compat", "deepseek-chat", 0.3, 1.2, False)


def test_empty_overrides_from_env_file_are_ignored():
    assert load(env={"GUARDIAN_MODEL_L1": "", "GUARDIAN_PROVIDER_L1": ""}).levels == load(env={}).levels


def test_another_file_and_bad_files(tmp_path):
    other = tmp_path / "m.yaml"
    other.write_text("providers:\n  p: {api_key_env: P_KEY}\nlevels:\n"
                     + "".join(f"  {i}: {{provider: p, model: m{i}, in_usd: 1, out_usd: 2}}\n" for i in (1, 2, 3)))
    assert load(env={"GUARDIAN_MODELS_FILE": str(other)}).levels[3].model == "m3"
    with pytest.raises(ConfigError, match="unknown provider"):
        load(other, env={"GUARDIAN_PROVIDER_L2": "nowhere"})
    with pytest.raises(ConfigError, match="cannot read"):
        load(tmp_path / "missing.yaml", env={})


def test_keys_and_endpoints_come_only_from_the_environment():
    p = load(env={}).providers["anthropic"]
    with pytest.raises(MissingKey, match="ANTHROPIC_API_KEY"):
        p.api_key({})
    assert p.api_key({"ANTHROPIC_API_KEY": "k"}) == "k"
    assert p.base_url({"ANTHROPIC_BASE_URL": "https://gw.example"}) == "https://gw.example"
    assert p.base_url({"ANTHROPIC_BASE_URL": ""}) == p.default_base_url


def test_live_call_without_a_key_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("GUARDIAN_MODEL_L1", "openai_compat:deepseek-chat:0.3:1.2")
    g = LLMGateway("live", runs_dir=tmp_path / "runs", spend_path=tmp_path / "spend.json")
    with pytest.raises(MissingKey, match="OPENAI_COMPAT_API_KEY"):
        g.call("reader", "Classify.", "Melodify 299")


def test_no_model_names_or_keys_in_code():
    code = (ROOT / "kernel" / "llm_gateway.py").read_text() + (ROOT / "guardian" / "llm.py").read_text()
    assert not re.search(r"claude-|gpt-|deepseek-|qwen", code)
    config = (ROOT / "config" / "models.yaml").read_text()
    assert not re.search(r"sk-[A-Za-z0-9]|api_key\s*:", config)
    assert model_config.DEFAULT_FILE == ROOT / "config" / "models.yaml"


def test_a_slow_compatible_endpoint_is_a_model_failure_not_a_crash(monkeypatch):
    import httpx
    from kernel import llm_gateway
    from kernel.llm_gateway import LLMFailure

    def slow(*a, **kw):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(llm_gateway.httpx, "post", slow)
    spec = model_config.ModelSpec(provider="openai_compat", model="m", in_usd=1, out_usd=1)
    provider = model_config.ProviderSpec(name="openai_compat", api_key_env="K", base_url_env="U")
    monkeypatch.setenv("K", "k")
    monkeypatch.setenv("U", "https://llm.example/v1")
    with pytest.raises(LLMFailure, match="ReadTimeout"):
        llm_gateway._call_openai_compat(spec, {"system": "s", "user": "u", "max_tokens": 10}, provider)
