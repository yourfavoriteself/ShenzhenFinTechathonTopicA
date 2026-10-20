"""Record the demo video from the real UI: the "month for you" scenarios C1 to C10 in Chromium.

    python -m scripts.record_demo --out docs/media

Starts the web app on a free port (stub models), drives the page like a client would, records the
screen with Playwright and converts the recording to a constant 30 fps MP4 with ffmpeg. Nothing is
drawn over the interface: the captions become an SRT file, a subtitled copy with the text in a band
under the picture, and the voiceover script docs/video_script.md. Screenshots of the key moments go
next to the video; the presentation uses them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

os.environ.setdefault("GUARDIAN_LLM_MODE", "stub")

W, H = 1440, 900

SLATE_JS = """([title, lines, fade, lead]) => {
  let s = document.getElementById('demo-slate');
  if (!title) {
    if (s) { s.style.opacity = '0'; setTimeout(() => s.remove(), 600); s.id = ''; }
    return;
  }
  if (!s) { s = document.createElement('div'); s.id = 'demo-slate'; document.body.append(s); }
  s.style.cssText = 'position:fixed;inset:0;z-index:10000;background:#f4f5f7;color:#101828;display:flex;flex-direction:column;'
    + 'align-items:flex-start;justify-content:center;padding:0 140px;font-family:Inter,system-ui,sans-serif;text-align:left;'
    + 'transition:opacity .5s ease;opacity:' + (fade ? '0' : '1');
  s.replaceChildren();
  const logo = document.createElement('div'); logo.textContent = '\u5b88';
  logo.style.cssText = 'width:84px;height:84px;display:grid;place-items:center;background:#c2361f;color:#ffffff;border-radius:20px;'
    + 'font:600 50px/1 "Noto Sans CJK SC","WenQuanYi Zen Hei",sans-serif;margin-bottom:36px';
  s.append(logo);
  const h = document.createElement('div'); h.textContent = title;
  h.style.cssText = 'font-size:64px;font-weight:700;letter-spacing:-.03em;margin-bottom:24px';
  s.append(h);
  lines.forEach((l, i) => { const p = document.createElement('div'); p.textContent = l;
    p.style.cssText = lead && i === 0
      ? 'font:600 31px/1.35 Inter,system-ui,sans-serif;color:#101828;margin:0 0 22px;max-width:1150px'
      : 'font:400 25px/1.45 Inter,system-ui,sans-serif;color:#475467;margin:5px 0;max-width:1100px'; s.append(p); });
  if (fade) requestAnimationFrame(() => requestAnimationFrame(() => { s.style.opacity = '1'; }));
}"""

# Only for the recording: hide the suggestion chips, scroll smoothly and fade new messages and tabs in,
# so that nothing on screen jumps between two frames.
RECORDING_CSS = """
#chips { display: none !important; } #feed { padding-bottom: 120px; scroll-behavior: smooth; }
#feed > * { animation: demo-in .35s ease-out both; }
.tab.active { animation: demo-fade .35s ease-out both; }
@keyframes demo-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes demo-fade { from { opacity: 0; } to { opacity: 1; } }
"""
FADE = 0.5            # slate fade, seconds
FPS = 25             # the rate Chromium's screencast delivers; a different rate repeats frames unevenly
BAND = 110            # subtitle band under the picture in the subtitled copy
WPS = 2.5             # narration pace, words per second


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int):
    import uvicorn

    from app.web import create_web_app
    app = create_web_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return server, app
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("web app did not start")


class Film:
    def __init__(self, page, shots: Path, t0: float):
        self.page, self.shots, self.t0 = page, shots, t0
        self.marks: list[dict] = []    # captions: start, end, title, text (for the SRT, subtitles and voiceover)
        self.slates: list[dict] = []

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def _close(self) -> None:
        if self.marks and self.marks[-1]["end"] is None:
            self.marks[-1]["end"] = self.now()

    def caption(self, text: str, title: str = "", hold: float = 0, say: str = "") -> None:
        """A caption is not drawn on the page: it becomes a subtitle and, or as `say`, a line of the voiceover."""
        self._close()
        if text:
            self.marks.append({"t": self.now(), "end": None, "title": title, "text": text, "say": say})
        self.pause(hold)

    def slate(self, title: str, lines: list[str], hold: float, *, fade: bool = True, keep: bool = False,
              say: str = "", lead: bool = False) -> None:
        self._close()
        self.slates.append({"t": self.now(), "title": title, "lines": lines, "say": say})
        self.page.evaluate(SLATE_JS, [title, lines, fade, lead])
        self.pause(hold + (FADE if fade else 0))
        self.slates[-1]["end"] = self.now()
        if not keep:
            self.page.evaluate(SLATE_JS, ["", [], True, False])
            self.pause(FADE)

    def pause(self, s: float) -> None:
        if s:
            self.page.wait_for_timeout(int(s * 1000))

    def shot(self, name: str, chat: bool = True) -> None:
        # one plain screenshot, cropped afterwards: a clipped screenshot makes the screen recording glitch
        path = self.shots / f"{name}.png"
        self.page.screenshot(path=str(path))
        if chat:
            from PIL import Image
            Image.open(path).crop((16, 72, 16 + 720, 72 + 730)).save(self.shots / f"{name}_chat.png")

    def say(self, text: str) -> None:
        self.page.fill("#text", "")
        self.page.locator("#text").press_sequentially(text, delay=18)
        self.pause(0.3)
        self.page.click("#ask button")

    def wait(self, text: str, timeout: float = 15) -> None:
        self.page.wait_for_selector(f"text={text}", timeout=timeout * 1000)

    def show(self, locator) -> None:
        locator.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
        self.pause(0.9)

    def card(self, title: str):
        c = self.page.locator(".card", has_text=title).last
        c.wait_for()
        return c

    def confirm(self, card, standing: bool = False) -> None:
        if standing:
            card.locator(".standing input[type=checkbox]").check()
            self.pause(0.6)
        hold = card.locator("button.hold")
        if hold.count():
            self.show(hold)
            hold.hover()
            self.page.mouse.down()
            self.pause(1.7)
            self.page.mouse.up()
        else:
            card.locator("button", has_text="Confirm").click()

    def click(self, text: str, within=None) -> None:
        b = (within or self.page).locator("button", has_text=text).last
        self.show(b)
        b.click()

    def tab(self, name: str, hold: float) -> None:
        self.page.click(f"aside nav >> text={name}")
        self.pause(hold)


THESIS = ("An AI agent may propose any payment, but money moves only inside what the client signed, "
          "and the bank can prove it.")


def film(f: Film, svc) -> None:
    p = f.page
    f.slate("Guardian", [THESIS,
                         "A bank-side safety kernel for AI payment agents",
                         "Shenzhen FinTechathon 2026 · International Track · Topic A",
                         "Sandbox bank, synthetic data · recorded from the real interface"], 7, fade=False, lead=True,
              say="Guardian. " + THESIS + " Sandbox bank, synthetic data.")

    f.caption("The assistant is a chat. The kernel on the right owns every permission, every dollar and the journal.",
              "A month for you", 5,
              say="The assistant is a chat. The kernel owns every permission, every dollar and the journal.")
    gap = p.locator(".msg.notice", has_text="will be missing").first
    f.show(gap)
    f.caption("On the first morning Guardian sees a cash gap: the rent on 20 Oct does not fit. It offers a fix.", "Cash gap", 5,
              say="On the first morning Guardian sees a cash gap: the rent does not fit. It offers a fix.")
    f.click("from your savings", gap)
    c = f.card("Move money to Current account")
    f.show(c)
    f.caption("Every action is a card: exact payee, amount, source, what it can and cannot do. The device key signs it.",
              "What you see is what you sign", 6,
              say="Every action is a card: exact payee, amount and source. The device key signs it.")
    f.shot("01_card")
    f.confirm(c)
    f.wait("Moved")
    f.caption("Done, and the kernel checked the bank afterwards, not the assistant's word.", "", 3,
              say="Done. The kernel checked the bank, not the assistant's word.")

    f.say("Make me a budget")
    f.wait("Open these envelopes")
    f.caption("C8. A budget from twelve months of history: envelopes, bills and savings.", "Budget", 4)
    f.click("Open these envelopes")
    f.pause(1.5)
    f.say("Save $724.20 for a vacation by July, don't touch the gym")
    f.wait("$81 a month")
    f.caption("A goal is a plan month by month; the gym envelope is not touched.", "Goal", 4)
    f.click("Save $81")
    f.wait('Goal "vacation" is set')
    f.say("How much can I spend today?")
    f.wait("per day", 10)
    f.tab("Budget", 0)
    f.caption("Safe to spend today, after bills, envelopes and the goal.", "", 4)
    f.shot("02_budget")

    f.say("Find leaks in my subscriptions")
    f.wait("$10.23 a month")
    f.show(p.locator(".msg", has_text="$10.23 a month").last)
    f.caption("C3. Leaks: forgotten subscriptions, a price rise and a duplicate, $10.23 a month.", "Leaks", 5)
    f.shot("03_leaks")

    f.say("Cancel the $4.82 subscription")
    f.click("StreamBox, $4.82 a month, card *1234")
    c = f.card("Cancel subscription StreamBox")
    f.caption("C2. Cancelling StreamBox through the merchant's API. The bank also blocks future charges on card *1234.",
              "Cancel a subscription", 4,
              say="Cancelling StreamBox through the merchant's API. The bank also blocks the card.")
    f.confirm(c)
    form = p.locator(".secure", has_text="Sign-in needed").last
    form.wait_for()
    f.show(form)
    f.caption("The password goes into the bank's protected form: the domain and certificate are checked, no model sees it.",
              "Protected form", 5)
    f.shot("04_protected_form")
    form.locator("text=fill demo login").click()
    form.locator("input[name=remember]").check()
    f.pause(0.8)
    form.locator("button", has_text="Sign in").click()
    phone = p.locator(".msg.phone").last
    phone.wait_for()
    code = re.search(r"code: (\d+)", phone.inner_text()).group(1)
    code_form = p.locator(".secure", has_text="sent a code").last
    f.show(code_form)
    code_form.locator("input[name=code]").press_sequentially(code, delay=120)
    code_form.locator("button", has_text="Send code").click()
    f.wait("StreamBox is cancelled and the kernel verified it")
    f.show(p.locator(".msg.receipt", has_text="StreamBox is cancelled").last)
    f.caption("Verified three ways: the merchant, the bank's card block, and no charge when time runs forward.",
              "Verified", 5)
    f.shot("05_verified")
    boost = p.locator(".msg.notice", has_text="vacation").last
    if boost.count():
        f.click("Add $4.82", boost)
        f.pause(1)

    f.say("Pay for Melodify")
    c = f.card("Pay Melodify")
    f.show(c)
    f.caption("C1. Pay Melodify, and allow it monthly: a standing mandate with a ceiling of $5.", "Pay", 4)
    f.confirm(c, standing=True)
    f.wait("Paid Melodify")

    f.say("Was I charged twice?")
    c = f.card("Dispute charge")
    f.caption("C4. The same FreshMart charge twice in two minutes: Guardian opens a dispute.", "Double charge", 3)
    f.confirm(c)
    f.wait("Dispute opened")

    f.say("Pay my bills")
    f.wait("It looks like a fake bill")
    case = p.locator(".msg.case").last
    f.show(case)
    f.caption("C5 + C6. Two bills match the bank's records. The third imitates Mosenergo, has a wrong account, "
              "pressure and a hidden instruction for AI agents.", "Fake bill", 7)
    f.shot("06_fake_bill")
    for t in ("Pay Mosenergo", "Pay CityWater"):
        f.confirm(f.card(t))
        f.wait(f"Paid {t[4:]}")

    f.say("Transfer $241.40 to account 40817810500001234567")
    c = f.card("Pay account *4567")
    f.show(c)
    f.caption("C10. A new payee is scored on the bank's transfer graph. This account behaves like a money mule.",
              "Payee risk", 6)
    f.shot("07_mule")
    c.locator("button", has_text="Decline").click()
    f.pause(1)

    f.caption("C9. Now a 'bank security officer' calls. The operator reports a call from a spoofed number.", "Scam call", 4)
    p.click("#scamcall")
    f.wait("The call is in progress")
    f.say("Transfer $4,224.48 from my savings to safe account 40817810500001234567")
    c = f.card("Possible scam call")
    f.show(c.locator(".intervention"))
    f.caption("The scam ladder rises: explain, ask, pause, hold. Even a confident 'no' and the right PIN only start a pause.",
              "", 6)
    c.locator("input[placeholder='type the amount to confirm']").fill("4,224.48")
    for i in range(3):
        c.locator("input[type=radio][value=no]").nth(i).check()
    c.locator("input[name=pin]").fill("2580")
    f.confirm(c)
    f.wait("can be signed after")
    f.shot("08_scam")
    p.click("#wait15")
    f.wait("15 minutes later")
    f.confirm(c)
    f.wait("waits for a bank analyst")
    held = p.locator(".msg.notice", has_text="analyst").last
    f.show(held)
    f.caption("After the pause the money is held for a person at the bank. The analyst calls back: it was a scam.", "", 5,
              say="After the pause a bank analyst checks the transfer: it was a scam.")
    f.click("Analyst: stop it", held)
    f.wait("The money stays in your account")
    f.caption("The money stays in the account. The client hangs up.", "", 2)
    p.locator(".msg.phone button", has_text="Hang up").last.click()
    f.pause(1.5)

    f.caption("One month later.", "", 2)
    p.click("header >> text=+1 month")
    f.pause(1.5)
    p.click("header >> text=+1 day")
    f.pause(2)
    f.say("Pay for Melodify")
    f.wait("within your standing permission")
    f.show(p.locator(".msg.receipt").last)
    f.caption("The repeat payment runs inside the standing mandate: a notification, no card, no model call. "
              "One tap undoes it, and that counts against the agent.", "Next month", 5,
              say="Next month it runs alone, inside the standing permission. One tap undoes it.")

    f.slate("Earned trust", ["Agents start at level 1. Verified results raise trust; mistakes lower it at once.",
                             "Three strikes put an agent in quarantine: passports revoked, a card for everything."], 4,
              say="Earned trust. Verified results raise an agent's trust; mistakes lower it at once. Three strikes mean quarantine.")
    f.say("Find leaks in my subscriptions")
    f.wait("$5.41 a month")
    leaks = p.locator(".msg", has_text="$5.41 a month").last
    for name in ("TuneWave", "PixelPlay"):
        f.click("Block", leaks.locator("tr", has_text=name))
        f.confirm(f.card(f"Block future charges {name}"))
        f.wait(f"Future charges from {name} are blocked")
    raised = p.locator(".msg.trust", has_text="Trust raised").last
    raised.wait_for()
    f.show(raised)
    f.caption("The fifth verified result in a row. Subscriptions move to level 2: inside a standing permission "
              "Guardian may now act alone up to its limit.", "Reward", 6,
              say="Five verified results in a row raise subscriptions to level 2: Guardian may act alone up to its limit.")
    f.shot("10_trust_raised")
    p.click("#faulty")
    lowered = p.locator(".msg.trust", has_text="Trust lowered").last
    lowered.wait_for()
    f.show(lowered)
    f.caption("A faulty planner asks for ten times the permission. The kernel stops it, subscriptions fall "
              "straight to level 0, and the planner gets two strikes.", "Penalty", 7,
              say="A faulty planner asks for ten times the permission. The kernel stops it, trust drops to level 0, two strikes.")
    p.click("#faulty")
    quarantine = p.locator(".msg.trust", has_text="quarantined for 24 hours").last
    quarantine.wait_for()
    f.show(quarantine)
    f.caption("Then it cites an invoice that does not exist. Strike three: 24 hours of quarantine, its passports are "
              "revoked, then probation until five clean results.", "Quarantine", 7,
              say="Then it cites an invoice that does not exist. Strike three: 24 hours of quarantine, passports revoked.")
    f.shot("11_quarantine")
    f.tab("Trust", 0)
    f.caption("The Trust tab: what agents may do alone, per kind of task.", "Trust", 4)
    f.show(p.locator("#tab-trust h4", has_text="Agents: rewards and penalties"))
    f.caption("Each agent's record, and every change with its reason. All of it is in the signed journal.", "", 6)
    f.shot("09_trust", chat=False)

    f.slate("Investing on a US exchange", ["The broker works only with the client's investment envelope.",
                                           "A list of funds, no leverage, short selling or options, a price corridor."], 3,
              say="Investing on a US exchange. The broker works only with the client's investment envelope.")
    f.say("Invest $10 a month in SPY")
    c = f.card("Invest in SPDR")
    f.show(c)
    f.caption("C11. The agent proposes an order; the kernel checks it against the client's list of securities, the size "
              "and the envelope, with its own quote from the broker.", "Invest", 6,
              say="The agent proposes an order. The kernel checks the security, the size and the envelope, with its own quote.")
    f.confirm(c, standing=True)
    f.wait("Order placed")
    f.show(p.locator(".msg.receipt", has_text="Order placed").last)
    f.caption("The order goes to the broker and the kernel reads it back: same security, side and amount. "
              "The market is closed, so it waits for the open.", "Order placed", 6,
              say="The kernel reads the order back from the broker. The market is closed, so it waits for the open.")
    f.shot("12_invest")
    f.say("Buy $10 of GME")
    stop = p.locator(".msg.denied", has_text="GME").last
    stop.wait_for()
    f.show(stop)
    f.caption("A security outside the client's list is stopped at the broker step, before any order exists.",
              "Stopped", 5,
              say="A stock outside the client's list is stopped before any order exists.")
    f.tab("Budget", 0)
    f.show(p.locator("[data-testid=invest]"))
    f.caption("The investment envelope: what the client set aside, what is spent, orders and positions.", "", 4,
              say="The investment envelope: money set aside, orders and positions.")

    for name, text, hold, say in [
        ("Month for you", "The month: saved per month, done, asked, stopped.", 4, ""),
        ("Permissions", "Every live mandate and the vault entries: revocable at any time.", 4,
         "Every live permission, revocable at any time."),
        ("Agents at work", "Each agent gets a passport with the least it needs.", 3, "Each agent gets a minimal passport."),
        ("Cost", "Most work runs on levels 0 and 1: rules, templates and the small model.", 4,
         "Most work runs on rules, templates and the small model."),
        ("Red-team", "Red-team: 48 of 48 static attacks stopped; adaptive attacker 0 of 360 outside the mandate.", 5,
         "All 48 static attacks stopped; 0 of 360 adaptive attempts left the mandate."),
        ("Compliance", "Code requirements and Verifiable Intent / AP2 mapping, with where to check each.", 4,
         "Each requirement is mapped to where you can check it."),
        ("Journal", "Every step goes into a signed hash chain, anchored on chain.", 4,
         "Every step is in a signed hash chain, anchored on chain."),
    ]:
        f.tab(name, 0)
        f.caption(text, name, hold, say)
        if name in ("Month for you", "Red-team", "Permissions", "Cost"):
            f.shot("09_" + name.lower().replace(" ", "_").replace("-", ""), chat=False)

    f.caption("", "")
    f.slate("Guardian", ["Agents propose. The kernel decides, checks and records.",
                         "Code, tests, logs and reports: github.com/yourfavoriteself/ShenzhenFinTechathonTopicA"], 6,
            keep=True,
              say="Agents propose. The kernel decides, checks and records. Code and reports are on GitHub.")


def _ts(t: float, sep: str = ",") -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def _sub_end(m: dict) -> float:
    """Subtitles stay up while the caption is current, but not much longer than it takes to read."""
    words = len((m["title"] + " " + m["text"]).split())
    return min(m["end"], m["t"] + max(5.0, words / 2.2 + 2.0))


def srt(marks: list[dict]) -> str:
    rows = []
    for n, m in enumerate(marks, 1):
        text = f"{m['title']}: {m['text']}" if m["title"] else m["text"]
        rows.append(f"{n}\n{_ts(m['t'])} --> {_ts(_sub_end(m))}\n{text}\n")
    return "\n".join(rows)


def ass(marks: list[dict]) -> str:
    """Subtitles for the band under the picture: Inter, ink on the page background, two lines at most."""
    y = H + BAND // 2
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H + BAND}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Band,Inter,27,&H00281810,&H00281810,&H00F7F5F4,&H00F7F5F4,0,0,0,0,100,100,0,0,1,0,0,5,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for m in marks:
        title = "{\\b1\\c&H675447&}" + m["title"] + ":{\\b0\\c&H281810&}  " if m["title"] else ""
        rows.append(f"Dialogue: 0,{_ts(m['t'], '.')[1:-1]},{_ts(_sub_end(m), '.')[1:-1]},Band,,0,0,0,,"
                    f"{{\\an5\\pos({W // 2},{y})}}{title}{m['text']}")
    return head + "\n".join(rows) + "\n"


def _clean(text: str) -> str:
    return re.sub(r"\bC\d+( \+ C\d+)?\. ", "", text)


def voiceover(meta: dict, video: str) -> str:
    """The narration cheat sheet: one row per slate or caption, with the time it has on screen."""
    segs = [{"t": s["t"], "screen": f"Title card: {s['title']}",
             "say": s["say"] or " ".join(x.rstrip(".") + "." for x in [s["title"], *s["lines"]]).replace(" · ", ". ")}
            for s in meta["slates"]]
    segs += [{"t": m["t"], "screen": m["title"] or "(same scene)", "say": m["say"] or _clean(m["body"])}
             for m in meta["captions"]]
    segs.sort(key=lambda x: x["t"])
    rows, tight = [], 0
    for n, sg in enumerate(segs):
        end = segs[n + 1]["t"] if n + 1 < len(segs) else meta["duration_s"]
        need = len(sg["say"].split()) / WPS
        mark = ""
        if need > end - sg["t"] + 0.5:
            mark, tight = " (fast)", tight + 1
        rows.append(f"| {_ts(sg['t'])[3:8]} to {_ts(end)[3:8]} | {sg['screen']}{mark} | {sg['say']} |")
    mins, secs = divmod(round(meta["duration_s"]), 60)
    return f"""# Demo video: voiceover script

The demo video [`{video}`](media/{video}) ({mins}:{secs:02d}) has no voice and no captions, so that
we can record a narration track over it. Each row below is one scene: when it starts and ends, what
is on screen, and what to say. The text fits the scene at about {WPS:g} words per second; rows marked
"fast" need a quicker pace or a shorter sentence. The same lines are the subtitles of the backup copy
[`guardian_demo_subtitled.mp4`](media/guardian_demo_subtitled.mp4) and of
[`guardian_demo.srt`](media/guardian_demo.srt).

This file is generated by `scripts/record_demo.py` together with the video; times change with every
recording.

| Time | On screen | Say |
|---|---|---|
{chr(10).join(rows)}

## Recording and adding the voice

1. Play the video muted and read the "Say" column; start each row when the time in the first column
   comes up. A plain phone or headset microphone in a quiet room is enough.
2. Save the recording as `voice.m4a` (or `voice.wav`), trimmed so that it starts at 00:00 of the video.
3. Put the voice on the video without re-encoding the picture:

```bash
ffmpeg -i docs/media/guardian_demo.mp4 -i voice.m4a -c:v copy -c:a aac -b:a 160k -shortest guardian_demo_voice.mp4
```

4. If the voice runs early or late by a constant amount, shift it with `-itsoffset` (seconds) before the
   second input, for example `ffmpeg -i docs/media/guardian_demo.mp4 -itsoffset 0.5 -i voice.m4a ...`.
5. To keep the subtitles as a track that viewers can switch on, add the SRT as a third input:

```bash
ffmpeg -i guardian_demo_voice.mp4 -i docs/media/guardian_demo.srt -c copy -c:s mov_text guardian_demo_voice_cc.mp4
```
"""


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--name", default="guardian_demo")
    ap.add_argument("--script", default="", help="voiceover script path (default: docs/video_script.md next to docs/media)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / ".raw"
    shutil.rmtree(raw, ignore_errors=True)
    port = _free_port()
    server, app = _serve(port)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                                  record_video_size={"width": W, "height": H}, locale="en-GB")
        t0 = time.monotonic()
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/")
        f = Film(page, out, t0)
        page.evaluate(SLATE_JS, ["Guardian", [], False, False])     # cover the page while it loads
        lead = f.now()
        page.add_style_tag(content=RECORDING_CSS)
        page.wait_for_selector("text=device key: Ed25519")
        try:
            film(f, None)
        except Exception:
            page.screenshot(path=str(out / "_failed.png"))
            print(page.locator("#feed").inner_text()[-3000:])
            raise
        video = page.video.path()
        ctx.close()
        browser.close()
    server.should_exit = True
    if errors:
        raise SystemExit(f"page errors: {errors}")
    # everything before the first title card (a blank page loading) is cut; times shift with it
    for m in f.marks + f.slates:
        m["t"] = round(m["t"] - lead, 2)
        m["end"] = round(m["end"] - lead, 2) if m.get("end") is not None else None
    f.slates[0]["t"] = 0.0
    mp4 = out / f"{a.name}.mp4"
    _ffmpeg("-ss", f"{lead:.2f}", "-i", str(video), "-vf", f"fps={FPS},format=yuv420p", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-movflags", "+faststart", str(mp4))
    shutil.rmtree(raw, ignore_errors=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True, check=True).stdout)
    for m in f.marks:
        m["end"] = min(m["end"] if m["end"] is not None else dur, dur)
    meta = {"duration_s": round(dur, 1), "fps": FPS,
            "captions": [{"t": m["t"], "end": m["end"], "title": m["title"], "body": m["text"], "say": m["say"],
                          "text": f"{m['title']}: {m['text']}" if m["title"] else m["text"]} for m in f.marks],
            "slates": f.slates}
    (out / f"{a.name}_captions.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    (out / f"{a.name}.srt").write_text(srt(f.marks))
    subs = out / f"{a.name}.ass"
    subs.write_text(ass(f.marks))
    sub_mp4 = out / f"{a.name}_subtitled.mp4"
    _ffmpeg("-i", str(mp4), "-vf", f"pad={W}:{H + BAND}:0:0:color=0xf4f5f7,"
            f"drawbox=x=0:y={H}:w={W}:h=1:color=0xe4e7ec:t=fill,ass={subs}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(sub_mp4))
    subs.unlink()
    script = Path(a.script) if a.script else (out.parent / "video_script.md" if out.name == "media" else out / "video_script.md")
    script.write_text(voiceover(meta, mp4.name))
    print(f"{mp4}  {dur:.0f} s  {mp4.stat().st_size / 1e6:.1f} MB")
    print(f"{sub_mp4}  {sub_mp4.stat().st_size / 1e6:.1f} MB; {script}")


if __name__ == "__main__":
    main()
