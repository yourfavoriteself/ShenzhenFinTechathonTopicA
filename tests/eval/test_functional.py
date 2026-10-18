"""The functional evaluation set runs as tests too: every task in the client's own words passes on the stub."""

import pytest

from evalset.tasks import TASKS, Chat


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task(task):
    task.run(Chat())


def test_cost_report_next_month_is_cheaper_and_repeat_payment_needs_no_model():
    from scripts.cost_report import report
    r = report("stub")
    assert r["next_month"]["cost_rub"] < r["first_month"]["cost_rub"]
    assert r["next_month"]["big_model_calls"] == 0 < r["first_month"]["big_model_calls"]
    melodify = r["next_month"]["tasks"][0]
    assert melodify["task"] == "Pay for Melodify" and melodify["calls"] == 0
