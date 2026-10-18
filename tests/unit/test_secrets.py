import pytest

from kernel.clock import SimClock
from kernel.llm_gateway import LLMGateway, SecretLeak
from kernel.secrets import SecretError, Vault, ref, refs_in

C = "client_1"


@pytest.fixture
def vault():
    return Vault(SimClock())


def test_reference_is_filled_only_on_its_domain(vault):
    r = vault.put(C, "streambox.password", "streambox.example", "pa55-word!", remember=True)
    assert r == "{{secret:streambox.password}}" and refs_in(r) == ["streambox.password"]
    assert vault.fill(C, r, "streambox.example") == "pa55-word!"
    with pytest.raises(SecretError, match="bound to streambox.example"):
        vault.fill(C, r, "melodify.example")
    with pytest.raises(SecretError):
        vault.fill("client_2", r, "streambox.example")


def test_one_time_secret_is_destroyed_after_use(vault):
    r = vault.put(C, "streambox.sms", "streambox.example", "4821")
    assert vault.fill(C, r, "streambox.example") == "4821"
    assert not vault.has(C, "streambox.sms")
    with pytest.raises(SecretError):
        vault.fill(C, r, "streambox.example")


def test_values_never_show_in_listing_or_repr(vault):
    vault.put(C, "streambox.password", "streambox.example", "pa55-word!", remember=True)
    assert "pa55" not in repr(vault) and "pa55" not in str(vault.describe(C))
    assert vault.describe(C) == [{"name": "streambox.password", "domain": "streambox.example",
                                  "remember": True, "uses": 0}]


def test_tampered_binding_fails_integrity(vault):
    vault.put(C, "x.password", "streambox.example", "secret-1", remember=True)
    vault._entries[(C, "x.password")].domain = "evil.example"
    with pytest.raises(SecretError, match="integrity"):
        vault.fill(C, ref("x.password"), "evil.example")


def test_vault_values_are_blocked_from_model_prompts(vault, tmp_path):
    gw = LLMGateway("stub", runs_dir=tmp_path / "runs", spend_path=tmp_path / "spend.json")
    gw.register_stub("x", lambda req: "ok")
    vault.listeners.append(gw.guard.register)
    vault.put(C, "streambox.password", "streambox.example", "pa55-word!", remember=True)
    assert gw.call("x", "sys", "use {{secret:streambox.password}}").text == "ok"
    with pytest.raises(SecretLeak):
        gw.call("x", "sys", "the password is pa55-word!")


def test_forget_one_time_keeps_remembered(vault):
    vault.put(C, "a", "streambox.example", "aaaa", remember=True)
    vault.put(C, "b", "streambox.example", "bbbb")
    assert vault.forget(C, one_time_only=True) == 1
    assert vault.has(C, "a") and not vault.has(C, "b")
