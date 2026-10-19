"""Render the submission documents from Markdown to PDF (pandoc + Chromium).

    python docs/build.py docs/technical_documentation.md docs/security_report.src.md
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CSS = """
@page { size: A4; }
body { font-family: "Noto Sans", "Noto Sans CJK SC", "DejaVu Sans", sans-serif; font-size: 10.3pt; line-height: 1.5;
       color: #1b1712; max-width: none; margin: 0; padding: 0; }
header#title-block-header { position: relative; border-top: 6px solid #1b1712; border-bottom: 1px solid #1b1712;
       margin-bottom: 22px; padding: 16px 0 12px 74px; }
header#title-block-header::before { content: "\\5b88"; position: absolute; left: 0; top: 18px; width: 56px; height: 56px;
       display: grid; place-items: center; background: #b8321c; color: #fff8ee; border-radius: 3px;
       font: 700 34px/1 "Noto Serif CJK SC", "WenQuanYi Zen Hei", serif; box-shadow: inset 0 0 0 3px #b8321c, inset 0 0 0 5px #fff8ee; }
h1, h2, h1.title { font-family: Georgia, "Noto Serif", "DejaVu Serif", serif; font-weight: 600; }
h1.title { font-size: 25pt; margin: 0 0 4px; color: #1b1712; }
p.subtitle { font-size: 12pt; color: #4a4339; margin: 0; font-style: italic; font-family: Georgia, "Noto Serif", serif; }
p.author { font-size: 9.5pt; font-weight: 600; color: #b8321c; margin: 8px 0 0; letter-spacing: .08em; text-transform: uppercase; }
h1 { font-size: 15.5pt; color: #1b1712; margin: 24px 0 8px; padding-bottom: 3px; border-bottom: 1px solid #d9cfbd; break-after: avoid; }
h2 { font-size: 12.3pt; color: #9e2a16; margin: 16px 0 6px; break-after: avoid; }
a { color: #9e2a16; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 9pt; break-inside: auto;
        border-top: 1.5px solid #1b1712; border-bottom: 1.5px solid #1b1712; }
th, td { border: none; border-bottom: 1px solid #e2d9c8; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: transparent; border-bottom: 1px solid #1b1712; font-size: 8.3pt; text-transform: uppercase; letter-spacing: .05em; color: #4a4339; }
tr { break-inside: avoid; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: #f1ebe0; padding: 0 2px; }
pre { background: #1b1712; color: #f6f1e7; padding: 9px 11px; font-size: 8.2pt; white-space: pre-wrap; border-radius: 2px; }
pre code { background: none; color: inherit; }
img { max-width: 100%; }
figure { margin: 10px 0; text-align: center; }
figcaption { font-size: 9pt; color: #6f675b; font-style: italic; font-family: Georgia, serif; }
blockquote { border-left: 4px solid #b8321c; margin: 10px 0; padding: 4px 12px; color: #1b1712; background: #f4ece0; }
"""


def _table(rows: list[list], head: list[str], widths: list[int] | None = None) -> str:
    sep = "|" + "|".join("-" * w for w in (widths or [3] * len(head))) + "|"
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(head) + " |", sep] + ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])


def fill_security(src: Path) -> Path:
    """security_report.src.md + evidence/*.json -> security_report.md, so every number matches the evidence."""
    root = src.resolve().parent.parent
    sys.path.insert(0, str(root))
    from app.sections import CODE, VI_AP2
    from kernel.money import rub
    rt = json.loads((root / "evidence/redteam.json").read_text())
    ev = json.loads((root / "evidence/eval_report.json").read_text())
    st, rr, ad, lg = rt["static"], rt["retention"], rt["adaptive"], rt["legacy_prototype"]
    attacks = _table([[a["id"], a["title"], a["outcome"], ", ".join(a["stopped_by"]), rub(a["without_kernel"]["lost_kop"])]
                      for a in rt["attacks"]], ["#", "Attack", "Outcome", "Stopped by", "Lost without the kernel"],
                     [2, 14, 3, 7, 4])
    adaptive = _table([[g["title"], g["attempts"], g["successes"], " ".join(map(str, g["success_curve"])),
                        ", ".join(f"{k} {v}" for k, v in g["outcomes"].items())] for g in ad["goals"]],
                      ["Goal", "Attempts", "Outside the mandate", "Per round", "Kernel outcomes"], [8, 2, 3, 5, 6])
    outcomes: dict[str, int] = {}
    for a in rt["attacks"]:
        outcomes[a["outcome"]] = outcomes.get(a["outcome"], 0) + 1
    vals = {
        "STATIC_N": st["attacks"], "STATIC_STOPPED": st["blocked"], "STATIC_WK": st["without_kernel"]["succeeded"],
        "STATIC_WK_LOST": rub(st["without_kernel"]["lost_kop"]), "RET_N": rr["attacks"], "RET_STOPPED": rr["blocked"],
        "RET_WK": rr["without_kernel"]["succeeded"], "RET_WK_LOST": rub(rr["without_kernel"]["lost_kop"]),
        "AD_ROUNDS": ad["rounds"], "AD_WIDTH": ad["width"], "AD_GOALS": len(ad["goals"]),
        "AD_OOM": ad["with_kernel"]["out_of_mandate_successes"], "AD_ATTEMPTS": ad["with_kernel"]["attempts"],
        "LG_STOPPED": lg["prevented"], "LG_BOUNDED": lg["bounded"], "EV_STOPPED": ev["attacks"]["stopped"],
        "EV_N": ev["attacks"]["attacks"], "NOTE": rt["note"], "ATTACKS": attacks, "ADAPTIVE": adaptive,
        "BY_OUTCOME": ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items(), key=lambda x: -x[1])),
        "CODE": _table([list(r) for r in CODE], ["Requirement", "How Guardian meets it", "Where to see it"], [8, 12, 3]),
        "VIAP2": _table([list(r) for r in VI_AP2], ["Their concept", "Guardian"], [4, 10]),
    }
    text = src.read_text()
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", str(v))
    assert "{{" not in text, "unfilled placeholder"
    out = src.with_name(src.name.replace(".src", ""))
    out.write_text(text)
    return out


def build(md: Path) -> Path:
    md = md.resolve()
    html = md.with_suffix(".html")
    css = md.parent / ".build.css"
    css.write_text(CSS)
    subprocess.run(["pandoc", md.name, "-s", "--mathml", "-c", css.name,
                    "-o", html.name], check=True, cwd=md.parent)
    pdf = md.with_suffix(".pdf")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(html.resolve().as_uri())
        page.pdf(path=str(pdf), format="A4", print_background=True, display_header_footer=True,
                 header_template="<span></span>",
                 footer_template='<div style="font:8px Georgia,serif;color:#6f675b;width:100%;margin:0 14mm;display:flex;'
                                 'justify-content:space-between;border-top:0.5px solid #d9cfbd;padding-top:3px">'
                                 '<span style="color:#b8321c;letter-spacing:.1em">GUARDIAN</span>'
                                 '<span><span class="pageNumber"></span> of <span class="totalPages"></span></span></div>',
                 margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        b.close()
    html.unlink()
    css.unlink()
    return pdf


if __name__ == "__main__":
    for a in sys.argv[1:]:
        md = fill_security(Path(a)) if a.endswith(".src.md") else Path(a)
        print(build(md))
