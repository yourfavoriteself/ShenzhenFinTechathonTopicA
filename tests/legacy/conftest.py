import os

import pytest

os.environ["GUARDIAN_LLM"] = "off"

from guardian.profile import build_demo, pay_day  # noqa: E402


@pytest.fixture
def g():
    guardian = build_demo()
    pay_day(guardian)
    return guardian
