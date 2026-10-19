"""The Usage example in README.md runs and prints exactly the sample output shown under it."""

import contextlib
import io
import re
from pathlib import Path

README = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")


def test_usage_example_prints_the_sample_output():
    usage = README[README.index("### Usage"):]
    code = re.search(r"```python\n(.*?)```", usage, re.S).group(1)
    sample = re.search(r"Sample output:\n\n```\n(.*?)```", usage, re.S).group(1)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "README.md", "exec"), {})
    assert out.getvalue() == sample
