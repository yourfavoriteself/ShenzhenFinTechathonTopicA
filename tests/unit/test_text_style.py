"""House style: no em or en dashes and no minus sign characters in anything a reader sees (docs, UI, logs, code)."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT = {".md", ".py", ".js", ".html", ".css", ".json", ".jsonl", ".log", ".txt", ".toml", ".yml", ".yaml", ".sol",
        ".drawio", ".sql", ".cfg", ".example"}
BANNED = {"\u2014": "em dash", "\u2013": "en dash", "\u2212": "minus sign", "\u2015": "horizontal bar"}


def _files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        return [ROOT / f for f in out]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if ".git" not in p.parts and "node_modules" not in p.parts]


def test_no_dashes_in_text_files():
    found = []
    for p in _files():
        if p.suffix not in TEXT or not p.is_file():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for ch, name in BANNED.items():
                if ch in line:
                    found.append(f"{p.relative_to(ROOT)}:{n}: {name}")
    assert not found, "\n".join(found[:30])
