"""The functional evaluation set runs as tests too: every task in the client's own words passes on the stub."""

import pytest

from evalset.tasks import TASKS, Chat


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task(task):
    task.run(Chat())


def test_cost_report_next_month_is_cheaper_and_repeat_payment_needs_no_model():
    from scripts.cost_report import report
    r = report("stub")
    assert r["next_month"]["cost_usd"] < r["first_month"]["cost_usd"]
    assert r["next_month"]["big_model_calls"] == 0 < r["first_month"]["big_model_calls"]
    melodify = r["next_month"]["tasks"][0]
    assert melodify["task"] == "Pay for Melodify" and melodify["calls"] == 0


def test_demo_run_goes_through_the_whole_month(tmp_path):
    from scripts.demo_run import Demo
    from kernel.llm_gateway import LLMGateway
    d = Demo(LLMGateway("stub", spend_path=tmp_path / "spend.json", runs_dir=tmp_path / "runs"))
    d.run()
    log = "\n".join(d.lines)
    for s in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10"):
        assert f" {s} " in log or f" {s}\n" in log or f"{s} +" in log, s
    assert "StreamBox is cancelled and the kernel verified it" in log and "It looks like a fake bill" in log
    assert "money mule" in log and "A bank analyst stopped the transfer" in log
    assert d.svc.k.journal.verify().ok
    assert not [t for t in d.svc.bank.transactions(d.svc.client) if t.counterparty.startswith("acct:")]
