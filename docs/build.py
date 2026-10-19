"""Render the submission documents from Markdown to PDF (pandoc + Chromium).

    python docs/build.py docs/technical_documentation.md docs/security_report.src.md
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: "Noto Sans", "Noto Sans CJK SC", "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.45;
       color: #1f2937; max-width: none; margin: 0; padding: 0; }
header#title-block-header { border-bottom: 3px solid #10b981; margin-bottom: 18px; padding-bottom: 8px; }
h1.title { font-size: 24pt; margin: 0 0 4px; color: #064e3b; }
p.subtitle { font-size: 12pt; color: #4b5563; margin: 0; }
p.author { font-size: 11pt; font-weight: 600; color: #065f46; margin: 6px 0 0; }
h1 { font-size: 15pt; color: #064e3b; margin: 20px 0 8px; break-after: avoid; }
h2 { font-size: 12.5pt; color: #065f46; margin: 14px 0 6px; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9pt; break-inside: auto; }
th, td { border: 1px solid #d1d5db; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #ecfdf5; }
tr { break-inside: avoid; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.8pt; background: #f3f4f6; padding: 0 2px; }
pre { background: #f3f4f6; padding: 8px; font-size: 8.3pt; white-space: pre-wrap; border-radius: 4px; }
pre code { background: none; }
img { max-width: 100%; }
figure { margin: 8px 0; text-align: center; }
figcaption { font-size: 9pt; color: #6b7280; }
blockquote { border-left: 3px solid #10b981; margin: 8px 0; padding: 2px 10px; color: #374151; }
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
                 footer_template='<div style="font-size:8px;color:#9ca3af;width:100%;text-align:center">'
                                 'Guardian · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                 margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        b.close()
    html.unlink()
    css.unlink()
    return pdf


if __name__ == "__main__":
    for a in sys.argv[1:]:
        md = fill_security(Path(a)) if a.endswith(".src.md") else Path(a)
        print(build(md))
