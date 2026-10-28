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


async def test_google_account_rows_require_opt_in_and_preserve_sensitive_fields():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        html = '''<div role="link" data-identifier="alex@example.test" id="account"
          onclick="window.clicks=(window.clicks||0)+1"><span id="name">Alex Sample</span>
          <button id="nested">Account details</button></div>
          <input type="password" id="password"><input id="otp" autocomplete="one-time-code">
          <input type="hidden" id="token"><button id="normal">Other action</button>'''
        await page.route('https://accounts.google.com/**', lambda r: r.fulfill(
            content_type='text/html', body=html))
        await page.goto('https://accounts.google.com/synthetic-chooser')
        await install(page)
        assert [c['id'] for c in (await snapshot(page))['controls']] == ['normal']
        for target in ['#account', '#name', '#nested']:
            with pytest.raises(Exception, match='enabled in the extension popup'):
                await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", target)
        enabled = await snapshot(page, google_account_selection=True)
        assert [c['id'] for c in enabled['controls']] == ['account', 'nested', 'normal']
        account = enabled['controls'][0]
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s,google_account_selection:true})",
                            account['selector'])
        assert await page.evaluate('window.clicks') == 1
        for target in ['#password', '#otp', '#token']:
            with pytest.raises(Exception, match='Sensitive|visible match'):
                await page.evaluate("s=>handler({drima:true,op:'act',action:'fill',selector:s,value:'blocked',google_account_selection:true})", target)
        with pytest.raises(Exception, match='click-only'):
            await page.evaluate("handler({drima:true,op:'act',action:'fill',selector:'#account',value:'x',google_account_selection:true})")
        # A previously observed child selector stops working when opt-in is off.
        with pytest.raises(Exception, match='enabled in the extension popup'):
            await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#name',google_account_selection:false})")
        assert await page.evaluate('window.clicks') == 1
        await page.route('https://accounts.google.com.evil.test/**', lambda r: r.fulfill(
            content_type='text/html', body=html))
        await page.goto('https://accounts.google.com.evil.test/synthetic-chooser')
        await install(page)
        assert not any(c['id'] == 'account' for c in
                       (await snapshot(page, google_account_selection=True))['controls'])
        await browser.close()


async def test_google_account_popup_setting_and_failed_save():
    extension = Path(__file__).resolve().parents[1] / 'extension'
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content((extension / 'popup.html').read_text())
        await page.evaluate('''() => {
          window.state={mode:'manual',eligible:true,googleAccountSelection:false,attention:[]};
          window.changes=[];window.rejectSave=false;
          window.browser={runtime:{sendMessage:async r=>{
            if(r.ui==='google-account-selection') {
              changes.push(r.enabled);
              if(rejectSave)throw new Error('Synthetic storage failure');
              state.googleAccountSelection=r.enabled;
            }
            return state;
          }}};
        }''')
        await page.evaluate((extension / 'popup.js').read_text())
        option = page.locator('#google-account-selection')
        assert not await option.is_checked()
        await option.check()
        await page.wait_for_function('state.googleAccountSelection===true')
        await option.uncheck()
        await page.wait_for_function('changes.length===2 && state.googleAccountSelection===false')
        assert await page.evaluate('changes') == [True, False]
        await page.evaluate('rejectSave=true')
        await option.click()
        await page.wait_for_function("document.querySelector('#error').textContent.includes('storage failure')")
        assert not await option.is_checked()
        await browser.close()


async def test_dropzone_hidden_file_input_and_unlabeled_question_fields():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<form><div class="q"><p>Explain your experience? *</p><div><textarea id="answer"></textarea></div></div>'
            '<div class="q"><p>Resume *</p><div id="zone">Drag and drop file or browse'
            '<input id="cv" type="file" style="display:none"></div></div>'
            '<div id="twin">Two inputs<input id="a" type="file" style="display:none">'
            '<input id="b" type="file" style="display:none"></div>'
            '<div style="display:none"><input id="ghost" type="file"></div></form>')
        await install(page)
        result = await snapshot(page)
        answer = next(c for c in result['controls'] if c['selector'] == '#answer')
        assert answer['label'] == 'Explain your experience? *'
        meta = {'name': 'cv.txt', 'size': 3, 'type': 'text/plain', 'last_modified': 0}
        begin = await page.evaluate('r=>handler(r)', {'drima': True, 'op': 'upload_begin', 'selector': '#cv', 'files': [meta]})
        ctx = {'drima': True, 'upload_id': begin['upload_id']}
        await page.evaluate('r=>handler(r)', {**ctx, 'op': 'upload_chunk', 'file_index': 0, 'offset': 0, 'data': 'YWJj'})
        await page.evaluate('r=>handler(r)', {**ctx, 'op': 'upload_commit'})
        assert await page.evaluate("document.querySelector('#cv').files[0].text()") == 'abc'
        # Ambiguous zones (two inputs) and invisible containers stay rejected.
        for target in ('#a', '#ghost'):
            with pytest.raises(Exception, match='visible control or label'):
                await page.evaluate('r=>handler(r)', {'drima': True, 'op': 'upload_begin', 'selector': target, 'files': [meta]})
        await browser.close()


async def test_contenteditable_fill_waits_for_async_editor_selection():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        # Emulates Lexical: model text changes only through beforeinput, using the
        # selection learned from the asynchronous selectionchange event.
        await page.set_content('''<form><div id="ed" contenteditable="true">old text</div>
          <input type="hidden" name="answer" value="old text"></form><script>
          const ed=document.querySelector('#ed'), field=document.querySelector('[name=answer]');
          let known=null;
          document.addEventListener('selectionchange',()=>{const s=getSelection();
            known=s.rangeCount&&ed.contains(s.anchorNode)?s.toString():null;});
          const apply=data=>{const text=known===ed.textContent?data:ed.textContent+data;
            ed.textContent=text; field.value=text;};
          ed.addEventListener('beforeinput',ev=>{ev.preventDefault();
            if(known!==null&&ev.inputType==='insertText')apply(ev.data);});
          // Native DOM edits that bypass the editor are reverted from the model.
          new MutationObserver(()=>{if(ed.textContent!==field.value)ed.textContent=field.value;})
            .observe(ed,{childList:true,characterData:true,subtree:true});
        </script>''')
        await install(page)
        await page.evaluate("r=>handler(r)", {'drima': True, 'op': 'act', 'action': 'fill',
                                              'selector': '#ed', 'value': 'nuevo texto'})
        assert await page.evaluate("document.querySelector('[name=answer]').value") == 'nuevo texto'
        await browser.close()


async def test_link_href_is_exposed_without_query_or_fragment():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.route('http://bridge.test/**', lambda route: route.fulfill(content_type='text/html', body=
            '<a id="apply" href="/jobs/x/applications/new?token=secret#top">Apply now</a>'
            '<a id="js" href="javascript:void(0)">Script</a>'))
        await page.goto('http://bridge.test/')
        await install(page)
        controls = {c['selector']: c for c in (await snapshot(page))['controls']}
        assert controls['#apply']['href'] == 'http://bridge.test/jobs/x/applications/new'
        assert 'href' not in controls['#js'] or controls['#js']['href'] is None
        await browser.close()


async def test_custom_dropdown_leaf_options_are_discoverable_and_clickable():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div class="chosen-container"><input class="chosen-search-input">'
            '<ul class="chosen-results"><li class="active-result" onclick="window.picked=this.textContent">CV English</li>'
            '<li class="active-result" onclick="window.picked=this.textContent">CV Espanol</li></ul></div>'
            '<div class="fab-Dropdown"><div class="fab-MenuOption" onclick="window.country=this.textContent">Chile</div></div>'
            '<ul class="nav"><li>Plain navigation text</li></ul>'
            '<div class="dropdown" style="display:none"><li>Hidden option</li></div>')
        await install(page)
        controls = (await snapshot(page))['controls']
        options = [c for c in controls if c['role'] == 'option']
        assert [c['label'] for c in options] == ['CV English', 'CV Espanol', 'Chile']
        for option in options[1:]:
            await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", option['selector'])
        assert await page.evaluate('[window.picked, window.country]') == ['CV Espanol', 'Chile']
        await browser.close()


async def test_attention_title_marker_survives_site_title_changes():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<title>Apply</title><p>form</p>')
        await install(page)
        await page.evaluate("handler({drima:true,op:'attention_mark',reason:'CAPTCHA'})")
        assert await page.title() == '\U0001F534 CAPTCHA – Apply'
        await page.evaluate("document.title='Step 2'")
        await page.wait_for_function("document.title.startsWith('\\u{1F534}')")
        assert await page.title() == '\U0001F534 CAPTCHA – Step 2'
        await page.evaluate("handler({drima:true,op:'attention_unmark'})")
        assert await page.title() == 'Step 2'
        await browser.close()


async def test_refs_compact_snapshot_and_stale_refs_after_document_change():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<div><div><form><label>Name <input name="n"></label>'
                               '<button type="button" onclick="window.hit=(window.hit||0)+1">Next</button>'
                               '<input id="short" value="kept"></form></div></div>')
        await install(page)
        full = (await snapshot(page))['controls']
        assert all(c['ref'].startswith('ref=e') and c['selector'] for c in full)
        compact = (await snapshot(page, compact=True))['controls']
        button = next(c for c in compact if c['label'] == 'Next')
        # Positional selectors are replaced by refs; short id selectors and refs stay stable.
        assert 'selector' not in button and 'disabled' not in button and 'value' not in button
        assert next(c for c in compact if c.get('id') == 'short')['selector'] == '#short'
        assert [c['ref'] for c in compact] == [c['ref'] for c in full]
        assert len(str(compact)) < len(str(full)) * 0.7
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", button['ref'])
        await page.evaluate("s=>handler({drima:true,op:'act',action:'fill',selector:s,value:'Ana'})",
                            next(c['ref'] for c in compact if c.get('name') == 'n'))
        assert await page.evaluate('[window.hit, document.querySelector("[name=n]").value]') == [1, 'Ana']
        await page.evaluate('document.querySelector("button").remove()')
        with pytest.raises(Exception, match='stale ref'):
            await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", button['ref'])
        await browser.close()


async def test_type_filters_and_press_picks_option_without_native_submit():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<form onsubmit="window.submitted=true;return false"><input id="q" autocomplete="off">'
            '<ul class="results"></ul><input type="password" id="pw"></form>'
            '<script>const all=["CV English","CV Español IA","CV Español IA — TCIT"];let active=0;'
            'q.addEventListener("input",()=>{const m=all.filter(x=>x.toLowerCase().includes(q.value.toLowerCase()));'
            'document.querySelector(".results").innerHTML=m.map(x=>"<li>"+x+"</li>").join("");active=0;});'
            'q.addEventListener("keydown",e=>{if(e.keyCode===40)active++;'
            'if(e.key==="Enter"){e.preventDefault();window.picked=document.querySelectorAll(".results li")[active].textContent;}'
            'if(e.key==="Escape")window.escaped=true;});</script>')
        await install(page)
        act = "r=>handler({drima:true,op:'act',...r})"
        result = await page.evaluate(act, {'action': 'type', 'selector': '#q', 'value': 'Español'})
        assert result['ok'] and await page.input_value('#q') == 'Español'
        assert await page.evaluate('document.querySelectorAll(".results li").length') == 2
        await page.evaluate(act, {'action': 'press', 'selector': '#q', 'value': 'ArrowDown'})
        await page.evaluate(act, {'action': 'press', 'selector': '#q', 'value': 'Enter'})
        await page.evaluate(act, {'action': 'press', 'selector': '#q', 'value': 'Escape'})
        assert await page.evaluate('[window.picked, window.escaped, !!window.submitted]') == \
            ['CV Español IA — TCIT', True, False]
        for bad in [{'action': 'press', 'selector': '#q', 'value': 'Tab'},
                    {'action': 'press', 'selector': '#pw', 'value': 'Enter'},
                    {'action': 'type', 'selector': '#pw', 'value': 'x'},
                    {'action': 'type', 'selector': '#q', 'value': 'x' * 501}]:
            with pytest.raises(Exception):
                await page.evaluate(act, bad)
        await browser.close()


XRAY_SHIM = """() => {
  window.wrappedJSObject = window;
  window.exportFunction = (fn, target, options) => { target[options.defineAs] = fn; return fn; };
  window.routed = [];
  browser.runtime.sendMessage = async m => { routed.push(m); return {tab_id: String(40 + routed.length)}; };
}"""


async def stage_upload(page, selector, picker=True):
    meta = {"name": "cv.pdf", "size": 3, "type": "application/pdf", "last_modified": 1}
    begin = await page.evaluate("r=>handler({drima:true,op:'upload_begin',...r})",
                                {"selector": selector, "files": [meta], "picker": picker})
    await page.evaluate("r=>handler({drima:true,op:'upload_chunk',...r})",
                        {"upload_id": begin["upload_id"], "file_index": 0, "offset": 0, "data": "QUJD"})
    return page.evaluate("r=>handler({drima:true,op:'upload_commit',...r})", {"upload_id": begin["upload_id"]})


async def test_picker_upload_fills_the_input_the_site_asks_to_open():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<button id="up">Upload resume</button><button id="pick">Choose</button>'
            '<label><input type="file" id="hidden" accept=".pdf" style="display:none"></label>'
            '<button id="hid">Attach</button><button id="noop">Nothing</button>'
            '<script>window.got=[];'
            'up.onclick=()=>{const i=document.createElement("input");i.type="file";i.accept=".pdf,.docx";'
            ' i.onchange=()=>got.push("detached:"+i.files[0].name);i.click();};'
            'pick.onclick=()=>{const i=document.createElement("input");i.type="file";'
            ' i.onchange=()=>got.push("picker:"+i.files[0].name);setTimeout(()=>i.showPicker(),100);};'
            'hidden.onchange=()=>got.push("hidden:"+hidden.files[0].name);hid.onclick=()=>hidden.click();</script>')
        await install(page)
        await page.evaluate(XRAY_SHIM)
        for button in ["#up", "#pick", "#hid"]:
            result = await (await stage_upload(page, button))
            assert result["via_picker"] and result["selected"][0]["name"] == "cv.pdf"
        assert await page.evaluate("got") == ["detached:cv.pdf", "picker:cv.pdf", "hidden:cv.pdf"]
        # The page's own methods are back once the upload settles.
        assert await page.evaluate("!Object.hasOwn(HTMLInputElement.prototype,'click')")
        with pytest.raises(Exception, match="did not ask for a file chooser"):
            await (await stage_upload(page, "#noop"))
        assert await page.evaluate("got.length") == 3
        await browser.close()


async def test_new_windows_from_agent_clicks_route_to_the_extension():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<a id="ext" target="_blank" href="https://ats.example/apply?token=secret">Apply on company website</a>'
            '<button id="pop" onclick="window.opened=window.open(\'https://jobs.example/form\')">Apply</button>'
            '<a id="tracked" target="_blank" href="https://x.example/" onclick="event.preventDefault();window.own=1">Own</a>'
            '<button id="plain" onclick="window.plain=1">Next</button>')
        await install(page)
        await page.evaluate(XRAY_SHIM)
        native_open = await page.evaluate("String(window.open)")
        click = "s=>handler({drima:true,op:'act',action:'click',selector:s})"
        result = await page.evaluate(click, "#ext")
        assert result["opened"] == [{"tab_id": "41", "origin": "https://ats.example"}]
        assert "secret" not in str(result)
        result = await page.evaluate(click, "#pop")
        assert result["opened"] == [{"tab_id": "42", "origin": "https://jobs.example"}]
        assert await page.evaluate("window.opened") is None
        # Links the page handles itself, and ordinary buttons, route nothing.
        for selector in ["#tracked", "#plain"]:
            assert "opened" not in await page.evaluate(click, selector)
        assert await page.evaluate("[window.own, window.plain]") == [1, 1]
        assert await page.evaluate("routed.map(m=>m.drima_route_open)") == [
            "https://ats.example/apply?token=secret", "https://jobs.example/form"]
        assert await page.evaluate("String(window.open)") == native_open
        await browser.close()
