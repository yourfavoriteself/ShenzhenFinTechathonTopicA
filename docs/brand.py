"""Render the README images (hero and the ledger of results) from evidence/*.json.

    python docs/brand.py  ->  docs/media/hero.png, docs/media/ledger.png
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"

BASE = """
* { box-sizing: border-box; margin: 0; }
body { background: #f6f1e7; color: #1b1712; font-family: "Noto Sans", "Segoe UI", sans-serif; }
.serif { font-family: Georgia, "Noto Serif", "DejaVu Serif", serif; }
.seal { display: grid; place-items: center; background: #b8321c; color: #fff8ee; border-radius: 4px;
        font-family: "Noto Serif CJK SC", "WenQuanYi Zen Hei", serif; font-weight: 700; }
"""


def _ev(name: str) -> dict:
    return json.loads((ROOT / "evidence" / name).read_text())


def _num(n: float | int) -> str:
    s = f"{n:,}".replace(",", " ")
    return s


def hero() -> str:
    return f"""<html><head><style>{BASE}
body {{ width: 1280px; height: 400px; position: relative; overflow: hidden; }}
.top {{ position: absolute; left: 0; right: 0; top: 0; height: 10px; background: #1b1712; }}
.seal {{ position: absolute; left: 80px; top: 92px; width: 150px; height: 150px; font-size: 96px;
         box-shadow: inset 0 0 0 9px #b8321c, inset 0 0 0 13px #fff8ee; }}
.txt {{ position: absolute; left: 280px; top: 78px; right: 80px; }}
h1 {{ font-size: 92px; font-weight: 600; line-height: 1; letter-spacing: .005em; }}
.rule {{ height: 2px; background: #1b1712; width: 560px; margin: 22px 0 18px; }}
.sub {{ font-size: 30px; font-style: italic; line-height: 1.3; }}
.meta {{ position: absolute; left: 80px; right: 80px; bottom: 34px; display: flex; justify-content: space-between;
         font-size: 17px; letter-spacing: .14em; text-transform: uppercase; color: #6f675b; }}
.meta b {{ color: #b8321c; font-weight: 700; }}
.grid {{ position: absolute; right: -40px; top: 40px; width: 360px; height: 300px; opacity: .55;
         background-image: linear-gradient(#e2d9c8 1px, transparent 1px); background-size: 100% 30px; }}
</style></head><body>
<div class="top"></div><div class="grid"></div>
<div class="seal">守</div>
<div class="txt"><h1 class="serif">Guardian</h1><div class="rule"></div>
<div class="sub serif">Your AI assistant pays, cancels and budgets.<br>The bank kernel lets it do only what you signed.</div></div>
<div class="meta"><span><b>Kseniia Kuzmina</b> &nbsp;·&nbsp; <b>Nikita Stepanov</b></span><span>Shenzhen FinTechathon 2026 · Topic A</span></div>
</body></html>"""


def ledger() -> str:
    rt, evl, cost, demo, bud = (_ev("redteam.json"), _ev("eval_report.json"), _ev("costs.json"),
                                _ev("demo_summary.json"), _ev("budget_report.json"))
    st, ad, rr = rt["static"], rt["adaptive"], rt["retention"]
    lost = st["without_kernel"]["lost_kop"] // 100
    rows = [
        ("Static attacks stopped", f"{st['blocked']} / {st['attacks']}", "evidence/redteam.json"),
        ("Memory planting attacks stopped", f"{rr['blocked']} / {rr['attacks']}", "evidence/redteam.json"),
        ("Adaptive attacker, payments outside the mandate", f"{ad['with_kernel']['out_of_mandate_successes']} / {ad['with_kernel']['attempts']}", "evidence/redteam.json"),
        ("Same attacks, agent without the kernel, lost", f"{_num(lost)} ₽", "evidence/redteam.json"),
        ("Functional tasks, EN RU ZH", f"{evl['functional']['passed']} / {evl['functional']['tasks']}", "evidence/eval_report.json"),
        ("Planted subscription leaks found", f"{bud['leaks']['found']} / {bud['leaks']['planted']}", "evidence/budget_report.json"),
        ("Model cost, one client, first and next month", f"{cost['first_month']['cost_rub']:.2f} › {cost['next_month']['cost_rub']:.2f} ₽", "evidence/costs.json"),
        ("Work on levels 0 and 1 (no large model)", f"{demo['cost']['share_level_0_1']} %", "evidence/demo_summary.json"),
        ("Signed journal entries in the demo month", f"{demo['journal']['entries']}", "evidence/demo_journal.jsonl"),
    ]
    trs = "".join(f"<tr><td class='n'>{i:02d}</td><td class='k'>{k}<span class='src'>{s}</span></td><td class='lead'></td>"
                  f"<td class='v serif'>{v}</td></tr>" for i, (k, v, s) in enumerate(rows, 1))
    return f"""<html><head><style>{BASE}
body {{ width: 1280px; padding: 46px 70px 40px; }}
.head {{ display: flex; align-items: center; gap: 22px; border-bottom: 2px solid #1b1712; padding-bottom: 16px; }}
.seal {{ width: 58px; height: 58px; font-size: 36px; box-shadow: inset 0 0 0 4px #b8321c, inset 0 0 0 6px #fff8ee; }}
h2 {{ font-size: 38px; font-weight: 600; }}
.head small {{ margin-left: auto; font-size: 15px; letter-spacing: .14em; text-transform: uppercase; color: #6f675b; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
td {{ padding: 15px 0 13px; border-bottom: 1px solid #d9cfbd; vertical-align: baseline; }}
td.n {{ min-width: 54px; padding-right: 14px; color: #b8321c; font: 700 16px Georgia, serif; }}
td.k {{ font-size: 21px; white-space: nowrap; }}
td.k .src {{ display: block; font: 13px "DejaVu Sans Mono", monospace; color: #8a8173; margin-top: 3px; }}
td.lead {{ width: 100%; border-bottom: 1px solid #d9cfbd; background: radial-gradient(circle, #b9ad97 1.2px, transparent 1.6px) 0 70% / 12px 12px repeat-x; }}
td.v {{ font-size: 30px; font-weight: 600; text-align: right; white-space: nowrap; padding-left: 16px; }}
.foot {{ margin-top: 18px; font-size: 15px; color: #6f675b; font-style: italic; }}
</style></head><body>
<div class="head"><div class="seal">守</div><h2 class="serif">The ledger</h2><small>every number is reproduced by make evidence</small></div>
<table>{trs}</table>
<div class="foot serif">Synthetic sandbox bank, fixed seed. Model cost is estimated from token counts of the real prompts until live runs.</div>
</body></html>"""


def render() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, html, h in (("hero", hero(), 400), ("ledger", ledger(), None)):
            page = b.new_page(viewport={"width": 1280, "height": h or 800}, device_scale_factor=2)
            page.set_content(html)
            page.screenshot(path=str(MEDIA / f"{name}.png"), full_page=h is None)
            page.close()
        b.close()


if __name__ == "__main__":
    render()
    print(MEDIA / "hero.png", MEDIA / "ledger.png")
