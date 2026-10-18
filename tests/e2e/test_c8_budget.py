"""C8: goal planning, salary split on payday, freed money to the goal, month review, forecast quality."""

import re

from kernel.trust import Category
from sandbox.sites.portal import DEMO_LOGINS

from .conftest import Client, of

GOAL = "Save up 60k for a vacation by July, do not touch the gym"


def set_goal(c: Client):
    items = c.say(GOAL)
    g = of(items, "goal")[-1]
    c.option([g], "Save ")
    return g


def test_goal_plan_keeps_bills_and_the_untouchable_category(client):
    g = of(client.say(GOAL), "goal")[-1].data
    assert g["target"] == "60 000 ₽" and g["deadline"] == "2027-07-01" and g["untouchable"] == "sport"
    assert g["feasible"] and all(m["ok"] for m in g["months"]) and len(g["months"]) == 9
    assert g["monthly"] == "6 700 ₽" and "/ 9 payments" in g["calc"]
    assert not any(c["name"] == "Health & sport" for c in g["cuts"])


def test_russian_goal_request(client):
    g = of(client.say("Накопи 60 000 к июлю, спортзал не трогай"), "goal")[-1].data
    assert g["target"] == "60 000 ₽" and g["untouchable"] == "sport"


def test_payday_split_and_goal_transfer_under_a_standing_permission(svc, client):
    set_goal(client)
    svc.apply_budget()
    items = svc.advance(9)
    sal = of(items, "salary")[-1].data
    names = [r["name"] for r in sal["rows"]]
    assert names[:2] == ["Bills", "Subscriptions"] and "vacation" in names
    assert any("will not cover" in n for n in sal["notes"])          # rent on the 20th falls before the next salary
    card = of(items, "card")[-1]
    assert card.data["card"]["standing_offer"] is not None
    save0 = svc.bank.accounts["acc_save"].balance
    client.yes(items, standing=True)
    assert svc.bank.accounts["acc_save"].balance - save0 == 670_000
    assert svc.goals[0].goal.saved == 670_000
    items = svc.advance(30)                       # trust level 1 lets the agent move only 1 000 ₽ alone: card again
    card = [i for i in of(items, "card") if "Savings" in i.text][-1]
    assert any("trust level 1" in r for s in card.data["steps"] for r in s["reasons"])
    client.yes(items)
    svc.k.trust.categories[Category.OWN_TRANSFERS].level = 2     # earned after five verified transfers
    items = svc.advance(30)                       # next 25th: within the standing permission, a notification
    r = [i for i in of(items, "receipt") if "Savings" in i.text][-1]
    assert r.data["notify_only"] and svc.goals[0].goal.saved == 3 * 670_000
    assert not [i for i in of(items, "card") if "Savings" in i.text]


def test_cancelled_subscription_speeds_up_the_goal(svc, client):
    set_goal(client)
    items = client.say("Cancel the 399 ₽ StreamBox subscription on card 1234")
    items = client.yes(items if of(items, "card") else client.option(items, "*1234"))
    cid = of(items, "login")[-1].data["cancellation"]
    svc.cancel_login(cid, *DEMO_LOGINS[("tok_streambox", "card_1234")])
    items = svc.cancel_code(cid, re.search(r"code: (\d+)", svc.portal.sms[-1].text).group(1))
    boost = of(items, "boost")[-1]
    assert "+399 ₽ a month" in boost.text and "days earlier" in boost.text
    client.option([boost], "Add 399")
    assert svc.goals[0].monthly == 670_000 + 39_900


def test_month_review_finds_spikes_and_late_night_spending(client):
    r = of(client.say("Итоги месяца"), "review")[-1].data
    assert r["month"] == "2026-09" and r["rows"]
    assert any("after 23:00" in a for a in r["anomalies"])


def test_forecast_quality_is_measured_without_peeking(client):
    q = of(client.say("How accurate is your forecast?"), "quality")[-1].data
    f, g = q["forecast"], q["gaps"]
    assert len(f["train"]) == 9 and f["test"] == ["2026-07", "2026-08", "2026-09"]
    assert not set(f["train"]) & set(f["test"])
    assert 0 < f["overall_wape"] < 40
    assert g["caught_in_time"] == len(g["actual"]) >= 1
