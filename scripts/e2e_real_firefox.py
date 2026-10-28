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
    files = ["LICENSE", "PRIVACY.md", "background.js", "content.js", "manifest.json", "popup.css", "popup.html", "popup.js"]
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
              "dialogs", "scroll", "open_frame", "text_paging", "drop_upload", "attention"}
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
