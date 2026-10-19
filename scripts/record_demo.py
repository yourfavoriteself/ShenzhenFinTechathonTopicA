"""Record the demo video from the real UI: the "month for you" scenarios C1 to C10 in Chromium, with captions.

    python -m scripts.record_demo --out docs/media

Starts the web app on a free port (stub models), drives the page like a client would, records the
screen with Playwright, adds captions on top of the page and converts the recording to MP4 with ffmpeg.
Screenshots of the key moments go next to the video; the presentation uses them.
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

CAPTION_JS = """([title, text]) => {
  let c = document.getElementById('demo-caption');
  if (!c) {
    c = document.createElement('div');
    c.id = 'demo-caption';
    c.style.cssText = 'position:fixed;left:40px;bottom:24px;z-index:9999;max-width:1060px;'
      + 'background:rgba(16,24,40,.94);color:#ffffff;font:400 20px/1.45 Inter,system-ui,sans-serif;padding:12px 20px 13px;'
      + 'border-radius:12px;box-shadow:0 8px 24px rgba(16,24,40,.25);text-align:left;pointer-events:none';
    document.body.append(c);
  }
  c.replaceChildren();
  if (title) { const b = document.createElement('div'); b.textContent = title;
    b.style.cssText = 'font:600 13px/1.4 Inter,system-ui,sans-serif;color:#98a2b3;margin-bottom:2px'; c.append(b); }
  const t = document.createElement('div'); t.textContent = text; c.append(t);
  c.style.display = text ? 'block' : 'none';
}"""

SLATE_JS = """([title, lines]) => {
  let s = document.getElementById('demo-slate');
  if (!title) { if (s) s.remove(); return; }
  if (!s) { s = document.createElement('div'); s.id = 'demo-slate'; document.body.append(s); }
  s.style.cssText = 'position:fixed;inset:0;z-index:10000;background:#f4f5f7;color:#101828;display:flex;flex-direction:column;'
    + 'align-items:flex-start;justify-content:center;padding:0 140px;font-family:Inter,system-ui,sans-serif;text-align:left';
  s.replaceChildren();
  const logo = document.createElement('div'); logo.textContent = '\u5b88';
  logo.style.cssText = 'width:84px;height:84px;display:grid;place-items:center;background:#c2361f;color:#ffffff;border-radius:20px;'
    + 'font:600 50px/1 "Noto Sans CJK SC","WenQuanYi Zen Hei",sans-serif;margin-bottom:36px';
  s.append(logo);
  const h = document.createElement('div'); h.textContent = title;
  h.style.cssText = 'font-size:64px;font-weight:700;letter-spacing:-.03em;margin-bottom:24px';
  s.append(h);
  for (const l of lines) { const p = document.createElement('div'); p.textContent = l;
    p.style.cssText = 'font:400 25px/1.45 Inter,system-ui,sans-serif;color:#475467;margin:5px 0;max-width:1100px'; s.append(p); }
}"""

HIDE_CHIPS = "#chips { display: none !important; } #feed { padding-bottom: 120px; }"


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
    def __init__(self, page, shots: Path):
        self.page, self.shots = page, shots
        self.marks: list[tuple[float, str]] = []
        self.t0 = time.monotonic()

    def caption(self, text: str, title: str = "", hold: float = 0) -> None:
        self.page.evaluate(CAPTION_JS, [title, text])
        if text:
            self.marks.append((round(time.monotonic() - self.t0, 1), f"{title}: {text}" if title else text))
        self.pause(hold)

    def slate(self, title: str, lines: list[str], hold: float) -> None:
        self.page.evaluate(SLATE_JS, [title, lines])
        self.pause(hold)
        self.page.evaluate(SLATE_JS, ["", []])

    def pause(self, s: float) -> None:
        if s:
            self.page.wait_for_timeout(int(s * 1000))

    def shot(self, name: str, chat: bool = True) -> None:
        self.page.evaluate(CAPTION_JS, ["", ""])
        self.page.screenshot(path=str(self.shots / f"{name}.png"))
        if chat:
            self.page.screenshot(path=str(self.shots / f"{name}_chat.png"), clip={"x": 16, "y": 72, "width": 720, "height": 730})

    def say(self, text: str) -> None:
        self.page.fill("#text", "")
        self.page.locator("#text").press_sequentially(text, delay=18)
        self.pause(0.3)
        self.page.click("#ask button")

    def wait(self, text: str, timeout: float = 15) -> None:
        self.page.wait_for_selector(f"text={text}", timeout=timeout * 1000)

    def show(self, locator) -> None:
        locator.scroll_into_view_if_needed()
        locator.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
        self.pause(0.6)

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
            hold.scroll_into_view_if_needed()
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


def film(f: Film, svc) -> None:
    p = f.page
    f.slate("Guardian", ["A bank-side safety kernel for AI payment agents",
                         "Shenzhen FinTechathon 2026 · International Track · Topic A",
                         "Sandbox bank, synthetic data · recorded from the real interface"], 6)

    f.caption("The assistant is a chat. The kernel on the right owns every permission, every ruble and the journal.",
              "A month for you", 5)
    gap = p.locator(".msg.notice", has_text="will be missing").first
    f.show(gap)
    f.caption("On the first morning Guardian sees a cash gap: the rent on 20 Oct does not fit. It offers a fix.", "Cash gap", 5)
    f.click("from your savings", gap)
    c = f.card("Move money to Current account")
    f.show(c)
    f.caption("Every action is a card: exact payee, amount, source, what it can and cannot do. The device key signs it.",
              "What you see is what you sign", 6)
    f.shot("01_card")
    f.confirm(c)
    f.wait("Moved")
    f.caption("Done, and the kernel checked the bank afterwards, not the assistant's word.", "", 3)

    f.say("Make me a budget")
    f.wait("Open these envelopes")
    f.caption("C8. A budget from twelve months of history: envelopes, bills and savings.", "Budget", 4)
    f.click("Open these envelopes")
    f.pause(1.5)
    f.say("Save 60 000 for a vacation by July, don't touch the gym")
    f.wait("6 700 ₽ a month")
    f.caption("A goal is a plan month by month; the gym envelope is not touched.", "Goal", 4)
    f.click("Save 6 700")
    f.wait('Goal "vacation" is set')
    f.say("How much can I spend today?")
    f.wait("per day", 10)
    f.tab("Budget", 0)
    f.caption("Safe to spend today, after bills, envelopes and the goal.", "", 4)
    f.shot("02_budget")

    f.say("Find leaks in my subscriptions")
    f.wait("847 ₽ a month")
    f.show(p.locator(".msg", has_text="847 ₽ a month").last)
    f.caption("C3. Leaks: forgotten subscriptions, a price rise and a duplicate, 847 ₽ a month.", "Leaks", 5)
    f.shot("03_leaks")

    f.say("Cancel the 399 ₽ subscription")
    f.click("StreamBox, 399 ₽ a month, card *1234")
    c = f.card("Cancel subscription StreamBox")
    f.caption("C2. Cancelling StreamBox through the merchant's API. The bank also blocks future charges on card *1234.",
              "Cancel a subscription", 4)
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
        f.click("Add 399", boost)
        f.pause(1)

    f.say("Pay for Melodify")
    c = f.card("Pay Melodify")
    f.show(c)
    f.caption("C1. Pay Melodify, and allow it monthly: a standing mandate with a ceiling of 344 ₽.", "Pay", 4)
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

    f.say("Transfer 20 000 to account 40817810500001234567")
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
    f.say("Transfer 350 000 ₽ from my savings to safe account 40817810500001234567")
    c = f.card("Possible scam call")
    f.show(c.locator(".intervention"))
    f.caption("The scam ladder rises: explain, ask, pause, hold. Even a confident 'no' and the right PIN only start a pause.",
              "", 6)
    c.locator("input[placeholder='type the amount to confirm']").fill("350000")
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
    f.caption("After the pause the money is held for a person at the bank. The analyst calls back: it was a scam.", "", 5)
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
    f.caption("The repeat payment runs inside the standing mandate: a notification, no card, no model call.", "Next month", 5)

    for name, text, hold in [
        ("Month for you", "The month: saved per month, done, asked, stopped.", 4),
        ("Permissions", "Every live mandate and the vault entries: revocable at any time.", 4),
        ("Trust", "Trust grows only with results the kernel verified.", 3),
        ("Agents at work", "Each agent gets a passport with the least it needs.", 3),
        ("Cost", "Most work runs on levels 0 and 1: rules, templates and the small model.", 4),
        ("Red-team", "Red-team: 39 of 39 static attacks stopped; adaptive attacker 0 of 360 outside the mandate.", 5),
        ("Compliance", "Code requirements and Verifiable Intent / AP2 mapping, with where to check each.", 4),
        ("Journal", "Every step goes into a signed hash chain, anchored on chain.", 4),
    ]:
        f.tab(name, 0)
        f.caption(text, name, hold)
        if name in ("Month for you", "Red-team", "Permissions", "Cost"):
            f.shot("09_" + name.lower().replace(" ", "_").replace("-", ""), chat=False)

    f.caption("", "")
    f.slate("Guardian", ["Agents propose. The kernel decides, checks and records.",
                         "Code, tests, logs and reports: github.com/yourfavoriteself/ShenzhenFinTechathonTopicA"], 6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--name", default="guardian_demo")
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
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/")
        page.add_style_tag(content=HIDE_CHIPS)
        page.wait_for_selector("text=device key: Ed25519")
        f = Film(page, out)
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
    mp4 = out / f"{a.name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c:v", "libx264", "-preset", "slow",
                    "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
    shutil.rmtree(raw, ignore_errors=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True, check=True).stdout)
    (out / f"{a.name}_captions.json").write_text(json.dumps(
        {"duration_s": round(dur, 1), "captions": [{"t": t, "text": x} for t, x in f.marks]}, ensure_ascii=False, indent=1))
    print(f"{mp4}  {dur:.0f} s  {mp4.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
