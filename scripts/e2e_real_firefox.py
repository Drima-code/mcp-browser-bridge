"""End-to-end check of the packaged addon in a real, throwaway Firefox.

Installs the extension temporarily into a fresh profile driven by geckodriver,
grants its optional permissions the way the popup would, then exercises the MCP
bridge against the synthetic job site in tests/e2e_site. The native host gets a
private socket directory, so a running everyday Firefox is never touched.

    uv run --frozen --with selenium python scripts/e2e_real_firefox.py [--headful]
"""

import asyncio
import base64
import functools
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service

ROOT = Path(__file__).resolve().parents[1]
ADDON_ID = "approved-tabs@drima-browser.local"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def serve(port, host):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT / "tests/e2e_site"))
    server = http.server.ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def package(path):
    files = ["LICENSE", "PRIVACY.md", "background.js", "content.js", "dialogs.js", "manifest.json", "popup.css", "popup.html", "popup.js"]
    with zipfile.ZipFile(path, "w") as z:
        for name in files:
            source = ROOT / "extension" / name if (ROOT / "extension" / name).exists() else ROOT / name
            z.write(source, name)


async def bridge(op, **arguments):
    from mcp_browser_bridge.regular import checked_request
    return await checked_request({"op": op, **arguments})


async def attempt(op, **arguments):
    try:
        return await bridge(op, **arguments), None
    except Exception as exc:  # Expected failures are part of the checks.
        return None, str(exc)


async def text_of(tab, selector):
    snap = await bridge("snapshot", tab_id=tab, max_chars=15000, compact=True)
    return snap["text"]


async def wait_text(tab, needle, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if needle in await text_of(tab, None):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


async def tab_with(path, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        tabs = (await bridge("tabs"))["tabs"]
        for tab in tabs:
            if tab["url"].endswith(path):
                return tab
        await asyncio.sleep(0.2)
    return None


async def scenario(base, cross, cv):
    status = await bridge("status")
    wanted = {"open", "navigate", "close", "refs", "keyboard", "screenshot", "picker_upload", "popup_routing",
              "dialogs", "scroll", "open_frame", "text_paging", "drop_upload", "attention", "save_document",
              "page_dialogs", "dialog_values", "dialog_window", "settle"}
    check("status lists every capability", wanted <= set(status["capabilities"]), str(status["capabilities"]))
    board = await tab_with("/index.html")
    check("user tab visible in automatic mode", board)
    tab = board["tab_id"]

    # New windows from synthetic clicks go to the agent window.
    result = await bridge("act", tab_id=tab, action="click", selector="#external")
    opened = result.get("opened", [])
    check("target=_blank link routed to agent window", opened and opened[0]["origin"] == base, json.dumps(result))
    check("routed result never contains the full address", "secret-value" not in json.dumps(result))
    result = await bridge("act", tab_id=tab, action="click", selector="#popup")
    check("window.open routed to agent window", result.get("opened"), json.dumps(result))
    tabs = (await bridge("tabs"))["tabs"]
    check("routed tabs are marked agent_window", sum(t.get("agent_window", False) for t in tabs) >= 2, json.dumps(tabs))
    result = await bridge("act", tab_id=tab, action="click", selector="#signin")
    check("sign-in pop-up is not routed", "opened" not in result, json.dumps(result))
    result = await bridge("act", tab_id=tab, action="click", selector="#confirm")
    check("confirm dismissed and reported", result.get("dialogs", [{}])[0].get("message") == "Discard draft?", json.dumps(result))
    check("page saw Cancel", await wait_text(tab, "confirm:false"))

    # Agent window: open, form controls, keyboard, editors, frames, shadow, files.
    opened = await bridge("open", url=f"{base}/ats.html")
    ats = opened["tab_id"]
    check("open uses the agent window", opened["agent_window"])
    await wait_text(ats, "Job 10")
    snap = await bridge("snapshot", tab_id=ats, max_chars=2000, compact=True)
    name = next(c for c in snap["controls"] if c.get("id") == "name")
    check("compact snapshot gives refs", name["ref"].startswith("ref=e"))
    ok, _ = await attempt("act", tab_id=ats, action="fill", selector=name["ref"], value="David Meza")
    check("fill by ref", ok)
    ok, _ = await attempt("act", tab_id=ats, action="select", selector="#country", value="cl")
    check("select option", ok)
    ok, _ = await attempt("act", tab_id=ats, action="check", selector="input[value=yes]")
    check("radio", ok)
    ok, _ = await attempt("act", tab_id=ats, action="check", selector="#consent")
    check("checkbox", ok)
    await bridge("act", tab_id=ats, action="type", selector="#combo", value="Español")
    await bridge("act", tab_id=ats, action="press", selector="#combo", value="ArrowDown")
    await bridge("act", tab_id=ats, action="press", selector="#combo", value="Enter")
    check("type + keys pick a filtered option", await wait_text(ats, "CV Español IA — WiTi\n") or
          "CV Español IA — WiTi" in (await text_of(ats, None)).split("Search resume")[1])
    ok, err = await attempt("act", tab_id=ats, action="fill", selector="#editor", value="Nuevo texto")
    check("contenteditable fill", ok, err or "")
    _, err = await attempt("act", tab_id=ats, action="fill", selector="#pw", value="x")
    check("password field refused", err and "Sensitive" in err, err or "")
    ok, err = await attempt("act", tab_id=ats, action="fill", selector="custom-q >>> #sq", value="shadow ok")
    check("open shadow root fill", ok, err or "")
    ok, err = await attempt("act", tab_id=ats, action="fill", selector="#ff", value="frame ok", frame_path=["#same"])
    check("same-origin frame fill", ok, err or "")
    from mcp_browser_bridge.regular import upload
    try:
        result = await upload(ats, "#hiddenfile", [str(cv)])
        check("hidden drop-zone input upload", result["selected"][0]["name"] == cv.name)
    except Exception as exc:
        check("hidden drop-zone input upload", False, str(exc))
    snap = await bridge("snapshot", tab_id=ats, max_chars=500, compact=True)
    cross_frame = next((f for f in snap["frames"] if f.get("origin") == cross), None)
    check("cross-origin frame reports origin only", cross_frame and "token" not in json.dumps(snap["frames"]), json.dumps(snap["frames"]))
    if cross_frame:
        result = await bridge("act", tab_id=ats, action="open_frame", selector=cross_frame["ref"])
        check("open_frame opens the embedded page", result.get("opened", [{}])[0].get("origin") == cross, json.dumps(result))
    # Like an agent: scroll to the bottom again until the list stops growing.
    for _ in range(6):
        await bridge("act", tab_id=ats, action="scroll", selector="#feed > div:first-child", value="bottom")
        await asyncio.sleep(0.5)
        if "40 jobs" in await text_of(ats, None):
            break
    loaded = await wait_text(ats, "40 jobs", 2)
    count = (await text_of(ats, None)).split(" jobs")[0].rsplit("\n", 1)[-1]
    check("scroll loads more items", loaded, f"feed shows {count} jobs")
    first = await bridge("snapshot", tab_id=ats, max_chars=15000)
    text, offset = first["text"], first.get("next_text_offset")
    while offset:
        page = await bridge("snapshot", tab_id=ats, max_chars=15000, text_offset=offset)
        text, offset = text + page["text"], page.get("next_text_offset")
    check("text paging reaches the end", "END-OF-DESCRIPTION" in text and len(text) == first["text_length"])
    await bridge("focus", tab_id=ats)
    try:
        from mcp_browser_bridge.regular import request
        shot = await request({"op": "screenshot", "tab_id": ats})
        image = base64.b64decode(shot["data"])
        check("screenshot returns a JPEG", image[:2] == b"\xff\xd8" and len(image) > 1000)
    except Exception as exc:
        check("screenshot returns a JPEG", False, str(exc))
    result = await bridge("act", tab_id=ats, action="click", selector="#submit")
    check("form submit receipt", await wait_text(ats, "Application received"))

    # LinkedIn-style Easy Apply: picker-only upload, drop zone, confirm accept.
    await bridge("navigate", tab_id=ats, action="url", url=f"{base}/easyapply.html")
    await wait_text(ats, "Upload resume")
    try:
        result = await upload(ats, "#upload", [str(cv)], via_picker=True)
        check("picker upload result", result.get("via_picker"), json.dumps(result))
        check("site received the file from its own chooser", await wait_text(ats, f"{cv.name}:{cv.stat().st_size}"))
    except Exception as exc:
        check("picker upload result", False, str(exc))
    try:
        result = await upload(ats, "#dropzone", [str(cv)], via_drop=True)
        check("drop upload delivered", await wait_text(ats, f"{cv.name}:{cv.stat().st_size}\n") or
              "dropped" not in (await text_of(ats, None)).split("Drag your CV here")[1][:5], json.dumps(result))
    except Exception as exc:
        check("drop upload delivered", False, str(exc))
    result = await bridge("act", tab_id=ats, action="click", selector="#next", dialog="accept")
    check("confirm accepted on request", await wait_text(ats, "sent"), json.dumps(result))

    # Attention, navigation and cleanup.
    flagged = await bridge("attention", action="set", tab_id=ats, reason="E2E check")
    check("attention flag set", flagged.get("attention"))
    await bridge("attention", action="clear", tab_id=ats)
    await bridge("navigate", tab_id=ats, action="back")
    check("navigate back", await wait_text(ats, "Application received", 6) or await wait_text(ats, "Fake ATS", 2))
    closed = await bridge("close", tab_id=ats)
    check("close tab", closed.get("closed"))

    # A PDF shown in Firefox's viewer (no content script) is saved through downloads.
    viewer = (await bridge("open", url=f"{base}/paper.pdf"))["tab_id"]
    await asyncio.sleep(1.5)
    saved, err = await attempt("save", tab_id=viewer, action="save", filename="E2E paper.pdf", wait_ms=8000)
    expected = (ROOT / "tests/e2e_site/paper.pdf").read_bytes()
    path = Path(saved["saved_path"]) if saved and saved.get("saved_path") else None
    check("save_document stores the viewer PDF", path and path.read_bytes() == expected, err or json.dumps(saved))
    _, err = await attempt("save", tab_id=viewer, action="save", filename="../escape.pdf")
    check("save_document rejects path names", err and "basename" in err, err or "")
    await dialog_scenario(base)


async def timed(op, **arguments):
    started = time.time()
    result, err = await attempt(op, **arguments)
    return result, err, time.time() - started


async def dialog_scenario(base):
    # 1. A dialog the page opens while loading, not caused by the agent.
    tab = (await bridge("open", url=f"{base}/restore.html"))["tab_id"]
    await asyncio.sleep(1.5)
    _, err, took = await timed("snapshot", tab_id=tab, max_chars=500)
    check("page dialog: snapshot fails fast with its text",
          err and 'pending page dialog: confirm "Found unsaved work from' in err and took < 3, f"{took:.1f}s {err}")
    _, err, took = await timed("wait", tab_id=tab, selector="#out", timeout_ms=1000)
    check("page dialog: wait fails fast", err and "pending page dialog" in err and took < 3, f"{took:.1f}s {err}")
    status, err = await attempt("status", tab_id=tab)
    check("page dialog: status reports it", status and status.get("pending_dialog", {}).get("type") == "confirm", err or json.dumps(status))
    _, _, took = await timed("tabs")
    check("other requests are not stalled by the paused tab", took < 1, f"{took:.1f}s")
    listing = await bridge("dialog", tab_id=tab, action="list")
    pending = listing["pending"]
    check("dialog list: type, text and origin", pending and pending[0]["type"] == "confirm"
          and "sintetico.csv" in pending[0]["message"] and pending[0]["origin"] == base, json.dumps(listing))
    _, err = await attempt("dialog", tab_id=tab, action="accept")
    check("on-screen dialog cannot be answered in place", err and "reload=true" in err, err or "")
    result = await bridge("dialog", tab_id=tab, action="accept", reload=True)
    check("reload with armed answer", result.get("reloaded"), json.dumps(result))
    check("page restored after armed accept", await wait_text(tab, "restore:true", 6))
    recent = (await bridge("dialog", tab_id=tab, action="list"))["recent"]
    check("answered dialog listed as recent", recent and recent[-1]["answered_by"] == "armed"
          and recent[-1]["answered"] == "accept", json.dumps(recent))

    # 5. Close (and screenshot) work on a paused tab.
    blocked = (await bridge("open", url=f"{base}/restore.html"))["tab_id"]
    await asyncio.sleep(1.5)
    await bridge("focus", tab_id=blocked)
    shot, err, took = await timed("screenshot", tab_id=blocked)
    check("screenshot of a paused tab", shot and took < 3, f"{took:.1f}s {err}")
    closed, err, took = await timed("close", tab_id=blocked)
    check("close a tab paused by a dialog", closed and closed.get("closed") and took < 3, f"{took:.1f}s {err}")

    # 2-4. Deferred dialogs, prompt values, settle.
    tab = (await bridge("open", url=f"{base}/dialogs.html"))["tab_id"]
    await wait_text(tab, "Dialog cases")
    result = await bridge("act", tab_id=tab, action="click", selector="#late", dialog="accept", dialog_window_ms=1200)
    check("dialog_window_ms catches a late confirm", result.get("dialogs", [{}])[0].get("message") == "Late confirm?"
          and await wait_text(tab, "late:true"), json.dumps(result))
    result = await bridge("act", tab_id=tab, action="click", selector="#name", dialog="accept", dialog_value="Corpus A")
    check("dialog_value answers a prompt", await wait_text(tab, "name:Corpus A"), json.dumps(result))
    result = await bridge("act", tab_id=tab, action="click", selector="#identity", dialog="accept", dialog_value="hunter2")
    check("credential prompt never gets the value", result.get("dialogs", [{}])[0].get("answered") == "dismiss_credential_prompt"
          and await wait_text(tab, "secret:null"), json.dumps(result))
    await bridge("act", tab_id=tab, action="click", selector="#openpop", dialog_window_ms=0)
    result = await bridge("act", tab_id=tab, action="click", selector="#popbtn", dialog="accept", dialog_value="early")
    check("click before the popover listener is lost (reproduces case 4)", "dialogs" not in result
          and "pop:none" in await text_of(tab, None), json.dumps(result))
    result = await bridge("act", tab_id=tab, action="click", selector="#popbtn", settle_ms=600,
                          dialog="accept", dialog_value="Price")
    check("settle_ms lets the late listener attach", await wait_text(tab, "pop:Price"), json.dumps(result))
    paused, err, took = await timed("wait", tab_id=tab, settle_ms=300)
    check("wait settle_ms is a plain pause", paused and paused.get("paused_ms") == 300, err or "")
    armed = await bridge("dialog", tab_id=tab, action="accept", type="prompt", value="T1")
    check("answer armed in place", armed.get("armed", {}).get("value_set"), json.dumps(armed))
    await bridge("act", tab_id=tab, action="click", selector="#timer")
    check("armed answer used by a later page dialog", await wait_text(tab, "timer:T1", 4))
    # Default window misses a 700 ms dialog: now it is reported instead of hanging.
    await bridge("act", tab_id=tab, action="click", selector="#late")
    await asyncio.sleep(1)
    _, err, took = await timed("snapshot", tab_id=tab, max_chars=500)
    check("late unanswered dialog reported as blocking", err and 'confirm "Late confirm?"' in err and took < 3, f"{took:.1f}s {err}")
    await bridge("close", tab_id=tab)

    # 3. A page that repeats its prompt: at most 5 answers, the rest pending.
    tab = (await bridge("open", url=f"{base}/loop.html"))["tab_id"]
    await wait_text(tab, "n:none")
    result, err, took = await timed("act", tab_id=tab, action="click", selector="#loop", dialog="accept", dialog_value="")
    check("prompt loop: 5 answered, then pending", result and result.get("outcome") == "uncertain"
          and len(result.get("dialogs", [])) == 5 and result.get("pending_dialog", {}).get("type") == "prompt"
          and took < 4, f"{took:.1f}s {err or json.dumps(result)}")
    closed, err = await attempt("close", tab_id=tab)
    check("close the looping tab", closed and closed.get("closed"), err or "")

    # A dialog the hook cannot see still gives a clear error, not a hang.
    tab = (await bridge("open", url=f"{base}/unhooked.html"))["tab_id"]
    await wait_text(tab, "start")
    await bridge("act", tab_id=tab, action="click", selector="#go", dialog_window_ms=0)
    await asyncio.sleep(1)
    _, err, took = await timed("snapshot", tab_id=tab, max_chars=500)
    check("unseen dialog: page reported paused", err and "paused" in err and took < 6, f"{took:.1f}s {err}")
    listing, err = await attempt("dialog", tab_id=tab, action="list")
    check("unseen dialog: list says paused", listing and listing.get("paused_without_report"), err or json.dumps(listing))
    closed, err = await attempt("close", tab_id=tab)
    check("close the paused tab", closed and closed.get("closed"), err or "")


def main():
    headful = "--headful" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="bridge-e2e-"))
    data = work / "socket"
    data.mkdir(mode=0o700)
    os.environ["DRIMA_REGULAR_BROWSER_DATA"] = str(data)
    xpi = work / "bridge.xpi"
    package(xpi)
    cv = work / "e2e-cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n% synthetic test CV\n%%EOF\n")
    port = 18600 + os.getpid() % 300
    servers = [serve(port, "127.0.0.1"), serve(port + 1, "localhost")]
    base, cross = f"http://127.0.0.1:{port}", f"http://localhost:{port + 1}"
    addon_uuid = str(uuid.uuid4())
    options = webdriver.FirefoxOptions()
    if not headful:
        options.add_argument("-headless")
    options.set_preference("extensions.webextOptionalPermissionPrompts", False)
    # Leave page dialogs on screen, as for a real user; the bridge must cope.
    options.set_capability("unhandledPromptBehavior", "ignore")
    (work / "downloads").mkdir()
    options.set_preference("browser.download.folderList", 2)
    options.set_preference("browser.download.dir", str(work / "downloads"))
    options.set_preference("extensions.webextensions.uuids", json.dumps({ADDON_ID: addon_uuid}))
    # --allow-system-access: the chrome context opens the extension popup page.
    driver = webdriver.Firefox(options=options, service=Service(
        env={**os.environ}, service_args=["--allow-system-access"]))
    try:
        driver.install_addon(str(xpi), temporary=True)
        before = set(driver.window_handles)
        with driver.context(driver.CONTEXT_CHROME):
            driver.execute_script(
                "gBrowser.selectedTab = gBrowser.addTab(arguments[0], "
                "{triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()});",
                f"moz-extension://{addon_uuid}/popup.html")
        time.sleep(1)
        driver.switch_to.window((set(driver.window_handles) - before).pop())
        driver.find_element(By.ID, "consent").click()
        driver.find_element(By.ID, "all").click()
        time.sleep(1)
        driver.find_element(By.ID, "screenshots").click()
        time.sleep(1)
        driver.find_element(By.ID, "downloads").click()
        time.sleep(1)
        status = driver.find_element(By.ID, "status").text
        check("popup: automatic access enabled, host connected", "automatic" in status and "connected" in status, status)
        driver.switch_to.window(sorted(before)[0])
        driver.get(f"{base}/index.html")
        time.sleep(1)
        asyncio.run(scenario(base, cross, cv))
    finally:
        driver.quit()
        for server in servers:
            server.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
