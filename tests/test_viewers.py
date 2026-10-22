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


async def test_shadow_controls_strict_selectors_inert_hosts_and_read_only_waits():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<div id="host"></div><div id="inert-host" inert></div>')
        await page.evaluate('''() => {
          host.attachShadow({mode:'open'}).innerHTML = `<section><button id="save">Shadow save</button>
            <input type="password" value="private"><div id="nested"></div></section>`;
          host.shadowRoot.querySelector('#save').onclick=()=>window.clicks=(window.clicks||0)+1;
          host.shadowRoot.querySelector('#nested').attachShadow({mode:'open'}).innerHTML='<button>Nested</button>';
          document.querySelector('#inert-host').attachShadow({mode:'open'}).innerHTML='<button id="blocked">Blocked</button>';
        }''')
        await install(page)
        result = await snapshot(page)
        assert [c['label'] for c in result['controls']] == ['Shadow save', 'Nested']
        assert result['controls'][0]['selector'] == '#host >>> #save'
        assert 'Shadow save' in result['text']
        assert 'Shadow save' in (await snapshot(page, scope='#host'))['text']
        target = {"drima": True, "op": "act", "action": "click", "selector": result['controls'][0]['selector'],
                  "document_id": result['document_id']}
        await page.evaluate('r=>handler(r)', target)
        assert await page.evaluate('window.clicks') == 1
        with pytest.raises(Exception, match='Document changed'):
            await page.evaluate('r=>handler({...r,document_id:"old"})', target)
        with pytest.raises(Exception, match='shadow host'):
            await page.evaluate('r=>handler({...r,selector:"#inert-host >>> #blocked"})', target)
        nested = await snapshot(page, scope='#host >>> #nested >>> button')
        assert nested['controls'][0]['label'] == 'Nested'
        await page.evaluate("setTimeout(()=>{const b=document.createElement('button');b.id='later';document.body.append(b)},150)")
        found = await page.evaluate("handler({drima:true,op:'wait',selector:'#later',timeout_ms:1500})")
        assert found['matched']
        absent = await page.evaluate("handler({drima:true,op:'wait',selector:'#missing',timeout_ms:0})")
        assert not absent['matched']
        assert await page.evaluate('window.clicks') == 1
        with pytest.raises(Exception, match='at most one'):
            await page.evaluate("handler({drima:true,op:'wait',selector:'div'})")
        await browser.close()


async def test_same_origin_frame_inventory_actions_and_document_replacement():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.route('http://bridge.test/**', lambda route: route.fulfill(
            content_type='text/html', body=(
                '<button id="save" onclick="window.clicks=(window.clicks||0)+1">Frame save</button>'
                if route.request.url.endswith('/inner') else
                '<iframe id="frame" src="/inner"></iframe>'
                '<iframe id="foreign" sandbox srcdoc="<button>Foreign</button>"></iframe>'
                '<iframe id="hidden" hidden src="/inner"></iframe>'
            )))
        await page.goto('http://bridge.test/')
        await install(page)
        inventory = (await snapshot(page))['frames']
        assert len(inventory) == 2
        assert inventory[0]['accessible'] and not inventory[1]['accessible']
        result = await snapshot(page, frame_path=['#frame'])
        assert result['controls'][0]['label'] == 'Frame save'
        assert result['document_id'] != (await snapshot(page))['document_id']
        request = {'drima': True, 'op': 'act', 'action': 'click', 'selector': '#save',
                   'frame_path': ['#frame'], 'document_id': result['document_id']}
        await page.evaluate('r=>handler(r)', request)
        assert await page.frames[1].evaluate('window.clicks') == 1
        for frame, message in [('#foreign', 'Cross-origin'), ('#hidden', 'hidden or inert')]:
            with pytest.raises(Exception, match=message):
                await snapshot(page, frame_path=[frame])
        await page.frames[1].goto('http://bridge.test/inner')
        with pytest.raises(Exception, match='Document changed'):
            await page.evaluate('r=>handler(r)', request)
        assert await page.frames[1].evaluate('window.clicks||0') == 0
        await browser.close()


async def test_upload_staging_is_bounded_across_frames_and_shadow_roots():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.route('http://bridge.test/**', lambda route: route.fulfill(
            content_type='text/html', body='<input id="file" type="file">' if route.request.url.endswith('/inner')
            else '<iframe id="frame" src="/inner"></iframe><div id="host"></div>'))
        await page.goto('http://bridge.test/')
        await page.evaluate('''host.attachShadow({mode:'open'}).innerHTML='<input id="file" type="file">';
          window.changes=0;host.addEventListener('change',()=>window.changes++);''')
        await install(page)
        meta = {'name': 'synthetic.txt', 'size': 3, 'type': 'text/plain', 'last_modified': 0}
        target = {'drima': True, 'frame_path': ['#frame']}
        begin = await page.evaluate('r=>handler(r)', {**target, 'op': 'upload_begin', 'selector': '#file', 'files': [meta]})
        with pytest.raises(Exception, match='Another upload'):
            await page.evaluate('r=>handler(r)', {'drima': True, 'op': 'upload_begin',
                                                'selector': '#host >>> #file', 'files': [meta]})
        context = {**target, 'upload_id': begin['upload_id']}
        with pytest.raises(Exception, match='another frame document'):
            await page.evaluate('r=>handler(r)', {**context, 'op': 'upload_abort', 'frame_path': []})
        await page.evaluate('r=>handler(r)', {**context, 'op': 'upload_chunk', 'file_index': 0, 'offset': 0, 'data': 'YWJj'})
        result = await page.evaluate('r=>handler(r)', {**context, 'op': 'upload_commit'})
        assert result['selected'][0]['size'] == 3
        assert await page.frames[1].evaluate("document.querySelector('#file').files[0].text()") == 'abc'
        shadow = await page.evaluate('r=>handler(r)', {'drima': True, 'op': 'upload_begin',
                                                      'selector': '#host >>> #file', 'files': [meta]})
        context = {'drima': True, 'upload_id': shadow['upload_id']}
        await page.evaluate('r=>handler(r)', {**context, 'op': 'upload_chunk', 'file_index': 0, 'offset': 0, 'data': 'YWJj'})
        await page.evaluate('r=>handler(r)', {**context, 'op': 'upload_commit'})
        assert await page.evaluate('window.changes') == 1
        assert await page.evaluate("host.shadowRoot.querySelector('#file').files[0].text()") == 'abc'
        await browser.close()
