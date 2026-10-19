"""Render the README images from evidence/*.json and the demo video.

    python docs/brand.py          ->  docs/media/hero.png, ledger.png, video_poster.png
    python docs/brand.py --clips  ->  docs/media/clips/*.gif, the 3 x 3 board of use cases (needs ffmpeg)
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"

BASE = """
* { box-sizing: border-box; margin: 0; }
body { background: #ffffff; color: #101828; font-family: Inter, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
.logo { display: grid; place-items: center; background: #c2361f; color: #fff; border-radius: 22%;
        font-family: "Noto Sans CJK SC", "WenQuanYi Zen Hei", sans-serif; font-weight: 600; }
.pill { display: inline-block; padding: 6px 14px; border-radius: 999px; font-size: 16px; font-weight: 500; }
"""


def _ev(name: str) -> dict:
    return json.loads((ROOT / "evidence" / name).read_text())


def _num(n: float | int) -> str:
    s = f"{n:,}".replace(",", " ")
    return s


def hero() -> str:
    return f"""<html><head><style>{BASE}
body {{ width: 1280px; height: 400px; position: relative; overflow: hidden; background: #f4f5f7; }}
.blob {{ position: absolute; border-radius: 28px; }}
.b1 {{ right: 300px; top: 64px; width: 190px; height: 120px; background: #eef4ff; border: 1.5px solid #c7d7fe; }}
.b2 {{ right: 80px; top: 64px; width: 200px; height: 120px; background: #f3f0ff; border: 1.5px solid #d4ccf9; }}
.b3 {{ right: 80px; top: 204px; width: 410px; height: 120px; background: #e8f6f3; border: 1.5px solid #b8e0d6; }}
.blob span {{ position: absolute; left: 22px; top: 18px; font-size: 17px; font-weight: 600; color: #344054; }}
.blob i {{ position: absolute; left: 22px; top: 48px; font-style: normal; font-size: 15px; color: #667085; line-height: 1.45; }}
.logo {{ position: absolute; left: 80px; top: 80px; width: 72px; height: 72px; font-size: 44px; }}
.txt {{ position: absolute; left: 80px; top: 176px; width: 640px; }}
h1 {{ font-size: 64px; font-weight: 700; letter-spacing: -.03em; line-height: 1; }}
.sub {{ font-size: 23px; line-height: 1.4; color: #475467; margin-top: 16px; }}
.meta {{ position: absolute; left: 80px; bottom: 34px; display: flex; gap: 10px; }}
.meta .pill {{ background: #ffffff; border: 1px solid #e4e7ec; color: #344054; }}
</style></head><body>
<div class="logo">守</div>
<div class="txt"><h1>Guardian</h1>
<div class="sub">Your AI assistant pays, cancels and budgets.<br>The bank kernel lets it do only what you signed.</div></div>
<div class="blob b1"><span>Agents</span><i>untrusted,<br>propose only</i></div>
<div class="blob b2"><span>Bank kernel</span><i>decides without<br>any model</i></div>
<div class="blob b3"><span>Your device signs the card</span><i>what you see is exactly what is executed,<br>every step in a signed journal</i></div>
<div class="meta"><span class="pill">Kseniia Kuzmina</span><span class="pill">Nikita Stepanov</span>
<span class="pill">Shenzhen FinTechathon 2026, Topic A</span></div>
</body></html>"""


def ledger() -> str:
    rt, evl, cost, demo, bud, tr = (_ev("redteam.json"), _ev("eval_report.json"), _ev("costs.json"),
                                    _ev("demo_summary.json"), _ev("budget_report.json"), _ev("trust_report.json"))
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
        ("Faulty agent: actions executed, then quarantined", f"{tr['faulty_actions']['executed']} / {tr['faulty_actions']['proposed']} · {tr['strikes']} strikes", "evidence/trust_report.json"),
        ("Work on levels 0 and 1 (no large model)", f"{demo['cost']['share_level_0_1']} %", "evidence/demo_summary.json"),
        ("Signed journal entries in the demo month", f"{demo['journal']['entries']}", "evidence/demo_journal.jsonl"),
    ]
    trs = "".join(f"<tr><td class='k'>{k}<span class='src'>{s}</span></td><td class='v'>{v}</td></tr>" for k, v, s in rows)
    return f"""<html><head><style>{BASE}
body {{ width: 1280px; padding: 44px 64px 40px; background: #f4f5f7; }}
.head {{ display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }}
.logo {{ width: 44px; height: 44px; font-size: 26px; }}
h2 {{ font-size: 32px; font-weight: 700; letter-spacing: -.02em; }}
.head .pill {{ margin-left: auto; background: #ffffff; border: 1px solid #e4e7ec; color: #475467; font-size: 15px; }}
.box {{ background: #ffffff; border: 1px solid #e4e7ec; border-radius: 16px; padding: 4px 28px; }}
table {{ width: 100%; border-collapse: collapse; }}
td {{ padding: 16px 0 14px; border-bottom: 1px solid #eaecf0; vertical-align: middle; }}
tr:last-child td {{ border-bottom: none; }}
td.k {{ font-size: 20px; font-weight: 500; }}
td.k .src {{ display: block; font: 13px "DejaVu Sans Mono", monospace; color: #98a2b3; margin-top: 4px; font-weight: 400; }}
td.v {{ text-align: right; white-space: nowrap; padding-left: 16px; }}
td.v {{ font-size: 26px; font-weight: 600; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }}
.foot {{ margin-top: 16px; font-size: 15px; color: #667085; }}
</style></head><body>
<div class="head"><div class="logo">守</div><h2>Results</h2><span class="pill">every number is reproduced by make evidence</span></div>
<div class="box"><table>{trs}</table></div>
<div class="foot">Synthetic sandbox bank, fixed seed. Model cost is estimated from token counts of the real prompts until live runs.</div>
</body></html>"""


# Use cases for the README board: (file, first caption, caption that ends the clip, speed up).
# Times are read from guardian_demo_captions.json, so the clips follow a new recording.
CLIPS = [
    ("01_cash_gap", "Cash gap:", "Budget:", 2.0), ("02_budget_goal", "Budget:", "Leaks:", 2.5),
    ("03_leaks", "Leaks:", "Cancel a subscription:", 1.0), ("04_cancel", "Cancel a subscription:", "Pay:", 2.5),
    ("05_pay_monthly", "Pay:", "Double charge:", 1.0), ("06_double_charge", "Double charge:", "Fake bill:", 1.0),
    ("07_fake_bill", "Fake bill:", "Payee risk:", 1.5), ("08_payee_risk", "Payee risk:", "Scam call:", 1.0),
    ("09_scam_call", "Scam call:", "One month later.", 3.0), ("10_earned_trust", "Reward:", "Trust:", 2.0),
]
CHAT = "crop=720:690:16:72"   # the chat column of the 1440 x 900 recording


def clips(video: Path = MEDIA / "guardian_demo.mp4") -> None:
    out = MEDIA / "clips"
    out.mkdir(exist_ok=True)
    caps = json.loads((MEDIA / "guardian_demo_captions.json").read_text())["captions"]
    at = lambda prefix: next(c["t"] for c in caps if c["text"].startswith(prefix))
    for name, first, last, speed in CLIPS:
        start, end = at(first), at(last)
        vf = (f"{CHAT},setpts=PTS/{speed},fps=8,scale=480:-1:flags=lanczos,split[a][b];"
              "[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(end - start), "-i", str(video),
                        "-vf", vf, str(out / f"{name}.gif")], check=True)


def poster(video: Path = MEDIA / "guardian_demo.mp4") -> str:
    frame = Path("/tmp/guardian_poster_frame.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "21", "-i", str(video), "-frames:v", "1", str(frame)],
                   check=True)
    src = "data:image/png;base64," + base64.b64encode(frame.read_bytes()).decode()
    caps = json.loads((MEDIA / "guardian_demo_captions.json").read_text())
    mins, secs = divmod(round(caps["duration_s"]), 60)
    return f"""<html><head><style>{BASE}
body {{ width: 1280px; height: 720px; position: relative; overflow: hidden; background: #101828; }}
img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .5; }}
.shade {{ position: absolute; inset: 0; background: linear-gradient(180deg, rgba(16,24,40,.1) 30%, rgba(16,24,40,.92) 85%); }}
.play {{ position: absolute; left: 50%; top: 44%; transform: translate(-50%, -50%); width: 120px; height: 120px; border-radius: 50%;
         background: #ffffff; box-shadow: 0 8px 30px rgba(0,0,0,.35); }}
.play::after {{ content: ""; position: absolute; left: 48px; top: 35px; border-left: 38px solid #101828;
                border-top: 25px solid transparent; border-bottom: 25px solid transparent; }}
.t {{ position: absolute; left: 0; right: 0; top: 74%; text-align: center; color: #ffffff; }}
.t b {{ display: block; font-size: 40px; font-weight: 700; letter-spacing: -.02em; margin-bottom: 12px; }}
.t .pill {{ background: rgba(255,255,255,.14); color: #e4e7ec; font-size: 18px; }}
</style></head><body><img src="{src}"><div class="shade"></div><div class="play"></div>
<div class="t"><b>Watch the demo</b><span class="pill">{mins}:{secs:02d} · recorded from the real interface · C1 to C10</span></div></body></html>"""


def render() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, html, h in (("hero", hero(), 400), ("ledger", ledger(), None), ("video_poster", poster(), 720)):
            page = b.new_page(viewport={"width": 1280, "height": h or 800}, device_scale_factor=2)
            page.goto("about:blank")
            page.set_content(html, wait_until="load")
            page.screenshot(path=str(MEDIA / f"{name}.png"), full_page=h is None)
            page.close()
        b.close()


if __name__ == "__main__":
    if "--clips" in sys.argv:
        clips()
        print(MEDIA / "clips")
    else:
        render()
        print(MEDIA / "hero.png", MEDIA / "ledger.png", MEDIA / "video_poster.png")
