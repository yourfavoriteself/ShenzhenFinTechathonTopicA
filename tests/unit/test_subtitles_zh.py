"""Chinese subtitles: every caption and title card of the recorded video has a translation."""

import json
from pathlib import Path

from scripts.record_demo import ZH, srt_zh

ROOT = Path(__file__).resolve().parents[2]
CAPTIONS = ROOT / "docs" / "media" / "guardian_demo_captions.json"


def test_every_line_of_the_video_has_chinese():
    zh = json.loads(ZH.read_text(encoding="utf-8"))
    meta = json.loads(CAPTIONS.read_text(encoding="utf-8"))
    lines = [c[k] for c in meta["captions"] for k in ("title", "body") if c[k]]
    lines += [x for s in meta["slates"] for x in [s["title"], *s["lines"]]]
    assert not [x for x in lines if x not in zh]
    out = srt_zh(meta)
    assert out.count(" --> ") == len(meta["captions"]) + len(meta["slates"]) and "守护者" in out
