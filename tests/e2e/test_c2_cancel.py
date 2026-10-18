"""C2: "cancel the subscription that charges 399 ₽", end to end on the sandbox bank and merchant portal."""

import re

import pytest

from agents.base import claim, propose, session
from agents import dispatcher as D
from app.service import GuardianService
from kernel.intent import IntentProposal
from kernel.llm_gateway import LLMGateway
from kernel.mandate import Action
from kernel.passport import Role
from kernel.pipeline import Outcome
from kernel.provenance import untrusted
from sandbox.sites.portal import CARD_NUMBERS, DEMO_LOGINS

from .conftest import Client, of

REQUEST = "Отмени подписку, по которой списывается 399 ₽"
LOGIN, PASSWORD = DEMO_LOGINS[("tok_streambox", "card_1234")]


def sms_code(svc):
    return re.search(r"code: (\d+)", svc.portal.sms[-1].text).group(1)


def cancel_streambox(svc, dev, card="*1234", remember=False, wrong_first=False):
    c = Client(svc, dev)
    items = c.say(REQUEST)
    ask = of(items, "options")[-1]
    assert len(ask.data["options"]) == 2, "StreamBox charges 399 ₽ on two cards: the kernel must ask which"
    items = c.option(items, card)
    card_item = of(items, "card")[-1]
    items = c.yes(items)
    login = of(items, "login")[-1]
    cid = login.data["cancellation"]
    if wrong_first:
        items = svc.cancel_login(cid, LOGIN, "not-my-password")
        assert of(items, "login")[-1].data["error"] == "wrong login or password"
    items = svc.cancel_login(cid, LOGIN, PASSWORD, remember=remember)
    assert of(items, "code"), "the merchant asks for an SMS code"
    items = svc.cancel_code(cid, sms_code(svc))
    return card_item, login, items, cid


def test_c2_cancels_the_right_subscription_and_verifies_it(svc, dev):
    card_item, login, items, cid = cancel_streambox(svc, dev, wrong_first=True)
    assert card_item.data["summary"] == "Cancel StreamBox, 399 ₽ a month, card *1234, charged since March?"
    assert login.data["domain_check"]["ok"] and login.data["domain"] == "streambox.example"
    done = of(items, "receipt")[-1]
    assert done.data["status"] == "verified" and len(done.data["verified"]) == 3

    bank, client = svc.bank, svc.client
    cofs = {c.card_id: c.active for c in bank.cof.values() if c.merchant == "tok_streambox"}
    assert cofs == {"card_1234": False, "card_5678": True}
    assert bank.is_blocked(client, "tok_streambox", "card_1234")
    assert not bank.is_blocked(client, "tok_streambox", "card_5678")
    assert any(d.sender_domain == "streambox.example" and "cancelled" in d.subject for d in svc.inbox())
    assert "cancellation.verified" in [e.event for e in svc.k.journal.entries]

    t0 = svc.clock.now()
    svc.advance(31)
    sb = [t for t in bank.transactions(client, since=t0) if t.counterparty == "tok_streambox"]
    assert {t.card_id for t in sb} == {"card_5678"}, "only the other StreamBox subscription keeps charging"


def test_c2_secrets_and_card_numbers_never_reach_any_model(fresh_world, tmp_path):
    gw = LLMGateway("stub", spend_path=tmp_path / "spend.json", runs_dir=tmp_path / "runs")
    svc = GuardianService(fresh_world(), gw)
    from kernel.crypto import DeviceKey
    dev = DeviceKey(seed="e2e:leak")
    svc.register_device(dev.alg, dev.public_b64)
    cancel_streambox(svc, dev, remember=True, wrong_first=True)
    code = sms_code(svc)
    Client(svc, dev).say("Pay my bills")
    Client(svc, dev).say("Find leaks in my subscriptions")
    assert gw.sent, "the run must have used models (critic, reader, explainer)"
    secrets = [PASSWORD, "not-my-password", code, *CARD_NUMBERS.values(), *(v[1] for v in DEMO_LOGINS.values())]
    for p in gw.sent:
        for s in secrets:
            assert s not in p["text"], f"{p['role']} prompt carried a secret"
        assert not re.search(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b", p["text"])
    assert {v["name"] for v in svc.k.vault.describe(svc.client)} == {"streambox.card_1234.login",
                                                                      "streambox.card_1234.password"}


def test_remembered_sign_in_skips_the_form(svc, dev):
    cancel_streambox(svc, dev, remember=True)
    from kernel.mandate import Grant
    grant = Grant(mandate_id="m", holder="x", action=Action.CANCEL_SUBSCRIPTION, payee="tok_streambox",
                  funding_source="card_1234", domain="streambox.example", exp=svc.clock.now())
    task = svc.k.open_task(svc.client, "cancel StreamBox")
    intent = svc.k.resolve(IntentProposal(action=Action.CANCEL_SUBSCRIPTION), svc.k.passports.issue(
        Role.DISPATCHER, "kernel-code", 0, "x", svc.client, task.task_id).passport)
    c = svc.cancels.open(grant, intent)
    assert svc.cancels.has_credentials(c)
    assert svc.cancels.sign_in(c.id).status == "sms_needed"


def _proposal(svc, action, amount=None, domain=None):
    k = svc.k
    p = IntentProposal(action=action,
                       payee=claim({"value": "tok_streambox", "ref": k.bank_value("bank:payee:tok_streambox",
                                                                                  "tok_streambox", "statement")}),
                       funding_source=claim({"value": "card_1234", "ref": k.bank_value("bank:card:card_1234",
                                                                                        "card_1234", "cards")}))
    if amount:
        p.amount = claim({"value": amount, "ref": k.bank_value(f"bank:offer:{amount}", amount, "merchant offer")})
    if domain:
        ref = k.publish(f"web:{domain}", untrusted(domain, "web search result"))
        p.domain = claim({"value": domain, "ref": ref})
    return p


def test_critic_rejects_accepting_a_retention_discount(svc):
    task = svc.k.open_task(svc.client, REQUEST, "cancel")
    s = session(svc.k, svc.gw, Role.DISPATCHER, 0, D.SYSTEM, svc.client, task.task_id)
    d = propose(svc.k, s, _proposal(svc, Action.PAY, amount=19_900))
    assert d.outcome == Outcome.DENIED
    critic = next(x for x in d.steps if x.step == "critic")
    assert critic.data["critic"] == "inconsistent"


def test_lookalike_domain_is_refused(svc):
    chk = svc.cancels.check_domain("tok_streambox", "streambox-cancel.help")
    assert not chk.ok and chk.imitates == "StreamBox" and not chk.matches
    assert svc.cancels.check_domain("tok_streambox", "streambox.example").ok
    task = svc.k.open_task(svc.client, REQUEST, "cancel")
    s = session(svc.k, svc.gw, Role.DISPATCHER, 0, D.SYSTEM, svc.client, task.task_id)
    d = propose(svc.k, s, _proposal(svc, Action.CANCEL_SUBSCRIPTION, domain="streambox-cancel.help"))
    assert d.outcome == Outcome.DENIED
    assert any("not the official domain" in r for r in d.reasons)


def test_c2_record_then_replay(fresh_world, tmp_path):
    from kernel.crypto import DeviceKey

    def run(mode, provider):
        gw = LLMGateway(mode, spend_path=tmp_path / "spend.json", runs_dir=tmp_path / "runs")
        svc = GuardianService(fresh_world(), gw)
        gw.providers["anthropic"] = lambda spec, req: provider(gw, req)
        dev = DeviceKey(seed="e2e:rr")
        svc.register_device(dev.alg, dev.public_b64)
        *_, items, _ = cancel_streambox(svc, dev)
        return gw, of(items, "receipt")[-1].data["status"]

    def recorder(gw, req):
        text = gw.stubs[req["role"]](req)
        return {"text": text, "input_tokens": len(req["system"] + req["user"]) // 4, "output_tokens": len(text) // 4}

    rec, s1 = run("record", recorder)
    rep, s2 = run("replay", lambda gw, req: pytest.fail("replay must not call the provider"))
    assert s1 == s2 == "verified"
    assert rec.log and all(r.source == "live" for r in rec.log)
    assert rep.log and all(r.source == "replay" for r in rep.log)
