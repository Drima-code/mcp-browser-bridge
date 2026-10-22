"""Viewer regressions use synthetic pages only, never private Gmail contents."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

SCRIPT = (Path(__file__).resolve().parents[1] / "extension/content.js").read_text()


async def install(page):
    await page.evaluate("window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}}")
    await page.evaluate(SCRIPT)


async def snapshot(page, **kwargs):
    return await page.evaluate("r=>handler({drima:true,op:'snapshot',...r})", kwargs)


async def test_dialog_controls_survive_a_long_background_page():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            "<p>Background inbox text</p>" + "<button>Inbox action</button>" * 140
            + '<section role="dialog" aria-modal="true" id="viewer">'
            '<p>PDF document text</p><button id="download" onclick="window.downloads=(window.downloads||0)+1">'
            'Download</button><button>Close</button><input type="password" value="private"></section>'
        )
        await install(page)
        result = await snapshot(page)
        assert result["scope_reason"] == "dialog"
        assert result["scope_selector"] == "#viewer"
        assert result["text"] == "PDF document text\n\nDownloadClose"
        assert [c["label"] for c in result["controls"]] == ["Download", "Close"]
        control = result["controls"][0]
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", control["selector"])
        assert await page.evaluate("window.downloads") == 1
        # An explicit scope can still inspect a known region outside a dialog.
        background = await snapshot(page, scope="body", offset=100, limit=10)
        assert background["scope_reason"] == "explicit"
        assert len(background["controls"]) == 10
        await browser.close()


async def test_pagination_reaches_visually_rendered_aria_hidden_toolbar():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            "<button>Inbox action</button>" * 125
            + '<div aria-hidden="true"><button id="download" onclick="window.count=(window.count||0)+1">Download</button></div>'
            '<div style="display:none"><button id="hidden">Hidden</button></div>'
            '<div inert><button id="inert">Inert</button></div>'
            '<input type="password" id="password" value="private">'
        )
        await install(page)
        first = await snapshot(page)
        assert first["controls_total"] == 126
        assert first["controls_truncated"] and first["next_offset"] == 100
        second = await snapshot(page, offset=first["next_offset"])
        assert second["next_offset"] is None
        control = next(c for c in second["controls"] if c["id"] == "download")
        assert control["aria_hidden"]
        await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#download'})")
        assert await page.evaluate("window.count") == 1
        for selector in ["#hidden", "#inert"]:
            with pytest.raises(Exception, match="visible match"):
                await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", selector)
        for bad in [{"offset": -1}, {"offset": 0.5}, {"limit": 101}, {"limit": 0}]:
            with pytest.raises(Exception, match="offset or limit"):
                await snapshot(page, **bad)
        await browser.close()


async def test_hover_reveals_toolbar_without_activation_and_stops_on_target_change():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''<div id="viewer" style="width:400px;height:200px"
          onmousemove="document.querySelector('#toolbar').style.visibility='visible'"
          onclick="window.activations=(window.activations||0)+1">PDF surface</div>
          <div id="toolbar" style="visibility:hidden"><button>Download</button></div>
          <button id="removed" onpointerover="this.remove()">Removed on hover</button>''')
        await install(page)
        assert not any(c["label"] == "Download" for c in (await snapshot(page))["controls"])
        await page.evaluate("handler({drima:true,op:'act',action:'hover',selector:'#viewer'})")
        assert any(c["label"] == "Download" for c in (await snapshot(page))["controls"])
        assert await page.evaluate("window.activations||0") == 0
        with pytest.raises(Exception, match="target changed"):
            await page.evaluate("handler({drima:true,op:'act',action:'hover',selector:'#removed'})")
        await browser.close()


async def test_explicit_scope_and_large_controls_keep_pagination_progressing():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<section id="viewer"><select id="large"></select><button>Next</button></section>')
        await page.evaluate("""() => {
          for(let i=0;i<50;i++) document.querySelector('select').add(new Option('界'.repeat(256),'界'.repeat(256)+i));
        }""")
        await install(page)
        first = await snapshot(page, scope="#viewer", limit=1)
        assert first["controls"][0]["options_truncated"]
        assert first["next_offset"] == 1
        second = await snapshot(page, scope="#viewer", offset=1)
        assert second["controls"][0]["label"] == "Next"
        assert second["next_offset"] is None
        assert (await snapshot(page, scope="html"))["scope_selector"] == "html"
        for scope in ["#missing", "button,select"]:
            with pytest.raises(Exception, match="visible scope"):
                await snapshot(page, scope=scope)
        await browser.close()
