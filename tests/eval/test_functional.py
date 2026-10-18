"""The functional evaluation set runs as tests too: every task in the client's own words passes on the stub."""

import pytest

from evalset.tasks import TASKS, Chat


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task(task):
    task.run(Chat())
