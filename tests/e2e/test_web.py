"""Web app: device registration, chat, card signed with the device key, permissions, SSE, and the UI in Chromium."""

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.web import create_web_app
from kernel.crypto import ES256, DeviceKey


@pytest.fixture
def web(svc):
    return TestClient(create_web_app(svc))


def sign_and_confirm(web, dev, card_item, standing=False):
    c = card_item["data"]["card"]
    h = c["standing_mandate_hash"] if standing else c["mandate_hash"]
    return web.post(f"/api/cards/{card_item['data']['pending_id']}/confirm",
                    json={"alg": dev.alg, "public_key": dev.public_b64, "sig": dev.sign_mandate(h),
                          "standing": standing}).json()


@pytest.mark.parametrize("alg", ["Ed25519", ES256])
def test_chat_card_confirm_with_device_key_and_revoke(web, alg):
    dev = DeviceKey(alg=alg)
    assert web.post("/api/device", json={"alg": dev.alg, "public_key": dev.public_b64}).json() == {"ok": True}
    items = web.post("/api/chat", json={"text": "Pay for Melodify"}).json()
    card = next(i for i in items if i["type"] == "card")
    stranger = DeviceKey()
    bad = sign_and_confirm(web, stranger, card)
    assert bad[0]["type"] == "error" and "device key is not registered" in bad[0]["text"]
    done = sign_and_confirm(web, dev, card, standing=True)
    assert done[0]["type"] == "receipt"
    [m] = web.get("/api/state").json()["mandates"]
    assert "Pay Melodify" in m["can"][0]
    assert web.post(f"/api/mandates/{m['id']}/revoke").json()[0]["data"]["revoked"] == [m["id"]]
    assert web.get("/api/state").json()["mandates"] == []
    j = web.get("/api/journal").json()
    assert j["ok"] and any(e["event"] == "mandate.revoked" for e in j["entries"])


def test_sse_streams_items_and_state(web):
    web.post("/api/chat", json={"text": "How much can I spend today?"})
    with web.stream("GET", "/api/events?since=0&once=true") as r:
        body = "".join(r.iter_text())
    assert "event: item" in body and "event: state" in body and '"type":"safe"' in body.replace(" ", "")


def test_api_rejects_bad_input(web):
    assert web.post("/api/chat", json={"text": "  "}).status_code == 400
    assert web.post("/api/device", json={"alg": "RSA", "public_key": "x"}).status_code == 400
    assert web.post("/api/mandates/mdt_nope/revoke").status_code == 404
    assert web.post("/api/time/advance", json={"days": 400}).status_code == 400
    assert web.get("/kernel/health").status_code == 200


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(svc):
    uvicorn = pytest.importorskip("uvicorn")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_web_app(svc), port=port, log_level="warning", ws="none"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def test_ui_demo_path_in_chromium(server, svc):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium not available: {e}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server)
        page.wait_for_selector("text=device key: Ed25519")
        page.click("text=Transfer 20 400")
        page.click(".card >> text=Confirm")
        page.wait_for_selector("text=The cash gap is closed")
        page.click("#chips >> text=Find leaks in my subscriptions")
        page.wait_for_selector("text=847 ₽ a month")
        page.click("#chips >> text=Pay for Melodify")
        melodify = page.locator(".card", has_text="Pay Melodify")
        melodify.locator("input[type=checkbox]").check()
        melodify.locator("text=Confirm").click()
        page.wait_for_selector("text=Paid Melodify 299 ₽")
        page.click("#chips >> text=Was I charged twice?")
        page.locator(".card", has_text="Dispute charge").locator("text=Confirm").click()
        page.wait_for_selector("text=Dispute opened")
        page.click("#chips >> text=Pay my bills")
        page.wait_for_selector("text=It looks like a fake bill")
        page.click("nav >> text=Permissions")
        page.wait_for_selector("text=Pay Melodify up to 344 ₽")
        assert not errors
        browser.close()
    assert len(svc.mandates()) == 1


def test_cancellation_endpoints_validate_input(web):
    assert web.post("/api/cancellations/cxl_nope/login", json={"login": "a", "password": "b"}).status_code == 404
    assert web.post("/api/cancellations/cxl_nope/code", json={"code": "12ab"}).status_code == 422
    assert web.post("/api/cancellations/cxl_nope/code", json={"code": "1234"}).status_code == 404


def test_ui_c2_cancel_with_protected_form_in_chromium(server, svc, tmp_path):
    import os
    import re
    pw = pytest.importorskip("playwright.sync_api")
    shots = os.environ.get("GUARDIAN_SCREENSHOTS")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium not available: {e}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server)
        page.wait_for_selector("text=device key: Ed25519")
        page.click("#chips >> text=Cancel the 399 ₽ subscription")
        page.click("text=StreamBox, 399 ₽ a month, card *1234, charged since March")
        card = page.locator(".card", has_text="Cancel StreamBox, 399 ₽ a month, card *1234")
        card.wait_for()
        if shots:
            page.screenshot(path=f"{shots}/c2_1_card.png")
        bar = card.locator(".hold")
        if bar.count():
            bar.hover()
            page.mouse.down()
            page.wait_for_timeout(1700)
            page.mouse.up()
        else:
            card.locator("text=Confirm").click()
        form = page.locator(".secure", has_text="Sign-in needed on streambox.example")
        form.wait_for()
        page.wait_for_selector("text=blocked future StreamBox charges on card *1234")
        if shots:
            page.screenshot(path=f"{shots}/c2_2_protected_form.png")
        form.locator("text=fill demo login").click()
        form.locator("input[name=remember]").check()
        form.locator("text=Sign in").click()
        phone = page.locator(".msg.phone")
        phone.wait_for()
        code = re.search(r"code: (\d+)", phone.inner_text()).group(1)
        code_form = page.locator(".secure", has_text="sent a code")
        code_form.locator("input[name=code]").fill(code)
        code_form.locator("text=Send code").click()
        page.wait_for_selector("text=StreamBox is cancelled and the kernel verified it")
        page.wait_for_selector("text=no next charge when the bank ran time forward")
        if shots:
            page.screenshot(path=f"{shots}/c2_3_verified.png")
        page.click("nav >> text=Permissions")
        page.wait_for_selector("text=streambox.card_1234.password · only on streambox.example")
        if shots:
            page.screenshot(path=f"{shots}/c2_4_vault.png")
        assert not errors
        browser.close()
