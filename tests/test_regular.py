"""Opt-in connector tested only against synthetic pages and local IPC."""

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from mcp_browser_bridge import regular
from mcp_browser_bridge.native_host import (
    ensure_private_directory,
    read_message,
    write_message,
)


def test_native_framing_and_bounds():
    stream = io.BytesIO()
    write_message(stream, {"id": "synthetic", "op": "tabs"})
    stream.seek(0)
    assert read_message(stream) == {"id": "synthetic", "op": "tabs"}
    with pytest.raises(EOFError):
        read_message(io.BytesIO(b"\x01"))
    with pytest.raises(ValueError):
        write_message(io.BytesIO(), {"text": "x" * 300000})


def test_directory_permissions(tmp_path):
    directory = tmp_path / "private"
    ensure_private_directory(directory)
    assert directory.stat().st_mode & 0o777 == 0o700
    directory.chmod(0o755)
    with pytest.raises(ValueError):
        ensure_private_directory(directory)


async def test_bridge_single_shot_and_errors(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "bridge.sock"
    monkeypatch.setattr(regular, "socket_path", lambda: path)
    received = []

    async def fake_host(reader, writer):
        payload = json.loads(await reader.readline())
        received.append(payload)
        writer.write(
            json.dumps(
                {"error": "Synthetic unknown outcome"}
                if payload["op"] == "act"
                else {"result": {"tabs": []}}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(fake_host, path=str(path))
    os.chmod(path, 0o600)
    async with server:
        assert await regular.request({"op": "tabs"}) == {"tabs": []}
        with pytest.raises(RuntimeError, match="unknown outcome"):
            await regular.request({"op": "act"})
        assert len(received) == 2
        os.chmod(path, 0o666)
        with pytest.raises(RuntimeError, match="permissions"):
            await regular.request({"op": "tabs"})
        assert len(received) == 2


async def test_real_native_host_roundtrip(tmp_path, monkeypatch):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    path = directory / "regular-browser.sock"
    monkeypatch.setattr(regular, "socket_path", lambda: path)
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_browser_bridge.native_host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "DRIMA_REGULAR_BROWSER_DATA": str(directory)},
    )
    try:
        for _ in range(100):
            if path.exists():
                break
            await asyncio.sleep(0.02)
        assert path.exists()
        task = asyncio.create_task(regular.request({"op": "tabs"}))
        outgoing = await asyncio.wait_for(
            asyncio.to_thread(read_message, process.stdout), 5
        )
        assert outgoing["op"] == "tabs"
        write_message(
            process.stdin, {"id": outgoing["id"], "result": {"tabs": [{"tab_id": "7"}]}}
        )
        assert await task == {"tabs": [{"tab_id": "7"}]}
    finally:
        process.stdin.close()
        await asyncio.to_thread(process.wait, 5)
    assert not path.exists()


async def test_content_controls_and_no_duplicate_submission():
    script = (Path(__file__).resolve().parents[1] / "extension/content.js").read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("""<title>Synthetic connector test</title>
          <label>Name<input id="name"></label><input id="password" type="password" value="do-not-expose">
          <input id="token" type="hidden" value="private"><button id="send" onclick="window.sent=(window.sent||0)+1">Send</button>
          <button class="duplicate">Duplicate</button><button class="duplicate">Duplicate</button>
          <div id="editor" contenteditable="true">Old text</div>""")
        await page.evaluate(
            "window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}}"
        )
        await page.evaluate(script)
        await page.evaluate(script)
        snapshot = await page.evaluate(
            "handler({drima:true,op:'snapshot',max_chars:8000})"
        )
        assert not any(c["id"] in ("password", "token") for c in snapshot["controls"])
        await page.evaluate(
            "handler({drima:true,op:'act',action:'fill',selector:'#name',value:'Alex Sample'})"
        )
        assert await page.locator("#name").input_value() == "Alex Sample"
        await page.evaluate("""document.querySelector('#editor').addEventListener('input', e => {
          if (e instanceof InputEvent) window.editorModel=e.target.textContent;
        })""")
        await page.evaluate(
            "handler({drima:true,op:'act',action:'fill',selector:'#editor',value:'Texto español <literal>'})"
        )
        assert await page.locator("#editor").text_content() == "Texto español <literal>"
        assert await page.evaluate("window.editorModel") == "Texto español <literal>"
        await page.evaluate(
            "handler({drima:true,op:'act',action:'fill',selector:'#editor',value:''})"
        )
        assert await page.locator("#editor").text_content() == ""
        assert await page.evaluate("window.editorModel") == ""
        with pytest.raises(Exception, match="Sensitive"):
            await page.evaluate(
                "handler({drima:true,op:'act',action:'fill',selector:'#password',value:'x'})"
            )
        with pytest.raises(Exception, match="exactly one"):
            await page.evaluate(
                "handler({drima:true,op:'act',action:'click',selector:'.duplicate'})"
            )
        await page.evaluate(
            "handler({drima:true,op:'act',action:'click',selector:'#send'})"
        )
        assert await page.evaluate("window.sent") == 1
        await browser.close()


def test_background_permissions_and_origin_revocation():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "extension/manifest.json").read_text())
    assert set(manifest["permissions"]) == {"activeTab", "nativeMessaging", "storage"}
    assert set(manifest["optional_permissions"]) == {"downloads", "http://*/*", "https://*/*"}
    harness = r"""
const vm=require('vm'), fs=require('fs'), assert=require('assert');
const events={}, messages=[];
let listener, granted=false, active=1, settings={}, connections=0;
const tabs=new Map([[1,{id:1,title:'Job',url:'https://wellfound.com/jobs/1?private=omit'}],
 [2,{id:2,title:'Other',url:'https://example.com/'}],
 [3,{id:3,title:'Private',url:'https://private.example/',incognito:true}],
 [4,{id:4,title:'Internal',url:'about:config'}]]);
const event=name=>({addListener:fn=>events[name]=fn});
const port={onMessage:{addListener:fn=>listener=fn},onDisconnect:event('disconnect'),postMessage:m=>messages.push(m)};
const browser={tabs:{get:async id=>tabs.get(id),executeScript:async()=>{},
 sendMessage:async(id,r)=>r.op==='content_status'?{content_version:'0.2.5'}:{ok:true},
 query:async q=>q.active?[tabs.get(active)]:Array.from(tabs.values()),
 onRemoved:event('removed'),onUpdated:event('updated')},
 runtime:{id:'bridge',getManifest:()=>({version:'0.2.5'}),getURL:p=>'moz-extension://bridge/'+p,connectNative:()=>{connections++;return port},onMessage:event('ui')},
 permissions:{contains:async()=>granted,onRemoved:event('permissionRemoved')},
 storage:{local:{get:async()=>settings,set:async s=>{settings={...settings,...s}}}},
 browserAction:{setBadgeText:async()=>{}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{browser,URL,Map,Set});
const sender={id:'bridge',url:'moz-extension://bridge/popup.html'};
const ui=r=>events.ui(r,sender);
(async()=>{
 assert.equal((await ui({ui:'state'})).mode,'manual');
 await assert.rejects(()=>ui({ui:'all',confirmed:true}),/permission/);
 await assert.rejects(()=>events.ui({ui:'all',confirmed:true},{id:'bridge',url:'https://evil.example'}),/popup/);
 await ui({ui:'toggle',tabId:1});
 await listener({id:'list',op:'tabs'});
 assert.equal(messages.at(-1).result.tabs.length,1);
 assert.equal(messages.at(-1).result.tabs[0].url,'https://wellfound.com/jobs/1');
 const initialConnections=connections;
 events.disconnect();
 assert.equal((await ui({ui:'state'})).connected,false);
 await ui({ui:'reconnect'});
 assert.equal(connections,initialConnections+1);
 assert.equal((await ui({ui:'state'})).approved,true);
 await ui({ui:'reconnect'});
 assert.equal(connections,initialConnections+1);
 await listener({id:'blocked',op:'snapshot',tab_id:'2'});
 assert(messages.at(-1).error.includes('not approved'));
 tabs.get(1).url='https://accounts.google.com/login';
 events.updated(1,{url:tabs.get(1).url});
 await listener({id:'revoked',op:'snapshot',tab_id:'1'});
 assert(messages.at(-1).error.includes('not approved'));
 // Any HTTP/HTTPS website can be manually approved, not just job sites.
 active=2;
 await ui({ui:'toggle',tabId:2});
 await listener({id:'other',op:'snapshot',tab_id:'2'});
 assert(messages.at(-1).result.ok);
 await listener({id:'upload-allowed',op:'upload_begin',tab_id:'2'});
 assert(messages.at(-1).result.ok);
 await listener({id:'upload-blocked',op:'upload_commit',tab_id:'1'});
 assert(messages.at(-1).error.includes('not approved'));
 granted=true;
 await ui({ui:'all',confirmed:true});
 await listener({id:'all',op:'tabs'});
 assert.equal(messages.at(-1).result.mode,'all');
 assert.equal(messages.at(-1).result.tabs.length,2);
 tabs.set(5,{id:5,title:'Future',url:'http://future.example/'});
 await listener({id:'future',op:'snapshot',tab_id:'5'});
 assert(messages.at(-1).result.ok);
 await listener({id:'private',op:'snapshot',tab_id:'3'});
 assert(messages.at(-1).error.includes('not approved'));
 await listener({id:'internal',op:'snapshot',tab_id:'4'});
 assert(messages.at(-1).error.includes('not approved'));
 // Native clients cannot change modes.
 await listener({id:'bad-mode',op:'all',tab_id:'1'});
 assert(messages.at(-1).error.includes('Unsupported'));
 // Revocation is checked before every operation, even before event delivery.
 granted=false;
 await listener({id:'revoked-grant',op:'snapshot',tab_id:'2'});
 assert(messages.at(-1).error.includes('not approved'));
 await events.permissionRemoved();
 assert.equal((await ui({ui:'state'})).mode,'manual');
 granted=true;
 await ui({ui:'all',confirmed:true});
 await ui({ui:'manual'});
 await listener({id:'stopped',op:'tabs'});
 assert.equal(messages.at(-1).result.tabs.length,0);
 // Restart restores opt-in only if host permissions still exist.
 settings={mode:'all'};
 vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{browser,URL,Map,Set});
 assert.equal((await ui({ui:'state'})).mode,'all');
 granted=false;
 vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{browser,URL,Map,Set});
 assert.equal((await ui({ui:'state'})).mode,'manual');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(root / "extension/background.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_popup_explicit_consent_denial_and_stop():
    extension = Path(__file__).resolve().parents[1] / "extension"
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content((extension / "popup.html").read_text())
        await page.evaluate("""() => {
          window.requests=[]; window.grant=false;
          window.state={mode:'manual',eligible:true,approved:false,tabId:7};
          window.browser={runtime:{sendMessage:async r=>{
            requests.push(r.ui);
            if(r.ui==='all')state.mode='all';
            if(r.ui==='manual')state.mode='manual';
            return state;
          }}, permissions:{request:async()=>{requests.push('permission');return grant},
            remove:async()=>{requests.push('remove');return true}}};
        }""")
        await page.evaluate((extension / "popup.js").read_text())
        assert await page.locator("#all").is_disabled()
        await page.locator("#consent").check()
        await page.locator("#all").click()
        await page.wait_for_function("document.querySelector('#error').textContent.includes('denied')")
        assert "all" not in await page.evaluate("requests")
        await page.evaluate("grant=true;requests=[]")
        await page.locator("#all").click()
        await page.wait_for_function("state.mode==='all'")
        assert (await page.evaluate("requests"))[:2] == ["permission", "all"]
        await page.locator("#manual").click()
        await page.wait_for_function("state.mode==='manual' && requests.includes('remove')")
        assert not await page.locator("#consent").is_checked()
        assert await page.locator("#all").is_disabled()
        await browser.close()


async def test_form_widgets_selectors_and_failed_state_changes():
    """Exercise custom widgets, controlled forms and navigation without real accounts."""
    script = (Path(__file__).resolve().parents[1] / "extension/content.js").read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''<label for="custom">Agree</label>
          <input id="custom" type="checkbox" style="display:none">
          <input id="locked" readonly value="original">
          <fieldset disabled><button id="disabled">Disabled</button></fieldset>
          <label for="password">Password</label><input id="password" type="password">
          <input type="radio" id="radio" checked>
          <input id="controlled" oninput="this.value='original'" value="original">
          <input id="cancelled" type="checkbox" onclick="event.preventDefault()">
          <button aria-disabled="true" id="ariaDisabled">Unavailable</button>
          <button onclick="window.sends=(window.sends||0)+1">Send once</button>
          <select id="choice"><option value="a">A</option>
            <optgroup disabled><option value="b">B</option></optgroup>
            <option value="c">C</option></select>''')
        await page.evaluate("window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}}")
        await page.evaluate(script)
        snapshot = await page.evaluate("handler({drima:true,op:'snapshot'})")
        for control in snapshot["controls"]:
            assert await page.locator(control["selector"]).count() == 1
        agree = next(c for c in snapshot["controls"] if c["label"] == "Agree")
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", agree["selector"])
        assert await page.locator("#custom").is_checked()
        for action, selector, value, error in [
            ("fill", "#locked", "changed", "Read-only"),
            ("click", "#disabled", "", "disabled"),
            ("click", "label[for=password]", "", "Sensitive"),
            ("click", "#ariaDisabled", "", "disabled"),
            ("uncheck", "#radio", "", "another radio"),
            ("fill", "#controlled", "changed", "retain"),
            ("check", "#cancelled", "", "state did not change"),
            ("select", "#choice", "b", "Invalid select"),
        ]:
            with pytest.raises(Exception, match=error):
                await page.evaluate("r=>handler({drima:true,op:'act',...r})", {
                    "action": action, "selector": selector, "value": value})
        send = next(c for c in snapshot["controls"] if c["label"] == "Send once")
        with pytest.raises(Exception, match="navigated"):
            await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s,expected_url:'https://old.example/'})", send["selector"])
        assert await page.evaluate("window.sends||0") == 0
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", send["selector"])
        assert await page.evaluate("window.sends") == 1
        await browser.close()


async def test_snapshot_stays_within_native_message_limit():
    script = (Path(__file__).resolve().parents[1] / "extension/content.js").read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<body>')
        await page.evaluate('''() => {
          for (let i=0;i<110;i++) {
            const s=document.createElement('select');
            for(let j=0;j<60;j++) s.add(new Option('界'.repeat(500),'v'.repeat(500)+j));
            document.body.append(s);
          }
          window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}};
        }''')
        await page.evaluate(script)
        result = await page.evaluate("handler({drima:true,op:'snapshot',max_chars:15000})")
        assert result["controls"]
        assert result["controls_truncated"]
        assert len(json.dumps({"result": result}, ensure_ascii=True).encode()) < 256 * 1024
        await browser.close()


async def test_mousedown_combobox_selection_and_single_submit():
    """Typed combobox text must become a selected value through widget events."""
    script = (Path(__file__).resolve().parents[1] / 'extension/content.js').read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''<form id="application">
          <label for="region">Based in Latin America?</label>
          <div id="widget"><input id="region" role="combobox" aria-required="true"
            aria-expanded="false" aria-controls="options"></div>
          <div id="options" role="listbox" hidden>
            <div id="yes" role="option" aria-selected="false">Yes</div>
            <div id="no" role="option" aria-selected="false">No</div>
          </div>
          <button id="send">Submit</button></form>
          <script>
          window.selected=''; window.submissions=0; window.clicks=0;
          const region=document.querySelector('#region');
          const options=document.querySelector('#options');
          // React Select-style opening: a click event alone does not open it.
          document.querySelector('#widget').addEventListener('mousedown', e=>{
            if(e.button!==0)return;
            e.preventDefault(); region.focus();
            options.hidden=false; region.setAttribute('aria-expanded','true');
          });
          options.addEventListener('mousedown', e=>e.preventDefault());
          options.addEventListener('click', e=>{
            selected=e.target.textContent; region.value=selected;
            e.target.setAttribute('aria-selected','true');
            options.hidden=true; region.setAttribute('aria-expanded','false');
          });
          document.querySelector('#send').addEventListener('click',()=>clicks++);
          document.querySelector('#application').addEventListener('submit',e=>{
            e.preventDefault(); if(selected==='Yes')submissions++;
          });
          </script>''')
        await page.evaluate('window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}}')
        await page.evaluate(script)
        await page.evaluate("handler({drima:true,op:'act',action:'fill',selector:'#region',value:'Yes'})")
        assert await page.evaluate('selected') == ''
        await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#region'})")
        assert await page.locator('#options').is_visible()
        snap = await page.evaluate("handler({drima:true,op:'snapshot'})")
        combo = next(c for c in snap['controls'] if c['id'] == 'region')
        assert combo['expanded'] is True
        assert combo['required'] is True
        assert combo['aria_controls'] == 'options'
        yes = next(c for c in snap['controls'] if c['role'] == 'option' and c['label'] == 'Yes')
        assert yes['selected'] is False
        await page.evaluate("s=>handler({drima:true,op:'act',action:'click',selector:s})", yes['selector'])
        assert await page.evaluate('selected') == 'Yes'
        assert await page.locator('#region').get_attribute('aria-expanded') == 'false'
        assert await page.evaluate('document.activeElement.id') == 'region'
        assert await page.evaluate('submissions') == 0
        await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#send'})")
        assert await page.evaluate('({submissions,clicks})') == {'submissions': 1, 'clicks': 1}
        await browser.close()


@pytest.mark.parametrize('mutation', ['remove', 'disable', 'navigate'])
async def test_pointer_handler_changes_stop_before_activation(mutation):
    script = (Path(__file__).resolve().parents[1] / 'extension/content.js').read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<button id="send">Submit</button>')
        await page.evaluate('''mutation => {
          window.activations=0;
          const button=document.querySelector('#send');
          button.addEventListener('click',()=>activations++);
          button.addEventListener('mousedown',()=>{
            if(mutation==='remove')button.remove();
            if(mutation==='disable')button.disabled=true;
            if(mutation==='navigate')location.hash='changed';
          });
          window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}};
        }''', mutation)
        await page.evaluate(script)
        with pytest.raises(Exception, match='target changed'):
            await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#send'})")
        assert await page.evaluate('activations') == 0
        await browser.close()


async def test_cancelled_pointerdown_does_not_emit_mouse_events_or_steal_focus():
    script = (Path(__file__).resolve().parents[1] / 'extension/content.js').read_text()
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<input id="keep"><button id="target">Action</button>')
        await page.evaluate('''() => {
          document.querySelector('#keep').focus(); window.events=[];
          const button=document.querySelector('#target');
          for(const name of ['pointerdown','mousedown','pointerup','mouseup','click'])
            button.addEventListener(name,e=>{
              events.push(e.type); if(e.type==='pointerdown')e.preventDefault();
            });
          window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}};
        }''')
        await page.evaluate(script)
        await page.evaluate("handler({drima:true,op:'act',action:'click',selector:'#target'})")
        assert await page.evaluate('events') == ['pointerdown', 'pointerup', 'click']
        assert await page.evaluate('document.activeElement.id') == 'keep'
        await browser.close()


async def test_chunked_upload_to_real_firefox_file_input(tmp_path, monkeypatch):
    import base64
    import hashlib

    data = bytes(range(256)) * 1000
    path = tmp_path / 'résumé.pdf'
    path.write_bytes(data)
    script = (Path(__file__).resolve().parents[1] / 'extension/content.js').read_text()
    calls = []
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.route('http://127.0.0.1/**', lambda route: route.fulfill(
            body='<label for="cv">CV</label><input id="cv" type="file" accept=".pdf" hidden>', content_type='text/html'))
        await page.goto('http://127.0.0.1/upload')
        await page.evaluate('''() => {
          window.browser={runtime:{onMessage:{addListener:fn=>window.handler=fn}}};
          window.changes=0; window.inputs=0;
          document.querySelector('#cv').addEventListener('input',()=>window.inputs++);
          document.querySelector('#cv').addEventListener('change',()=>window.changes++);
        }''')
        await page.evaluate(script)
        async def bridge(payload):
            calls.append(payload)
            assert len(json.dumps(payload).encode()) < 256 * 1024
            if payload['op'] == 'status':
                return {'protocol_version': 1, 'addon_version': '0.2.5', 'capabilities': ['upload']}
            return await page.evaluate('r=>handler({drima:true,...r})', payload)
        monkeypatch.setattr(regular, 'request', bridge)
        result = await regular.upload('7', '#cv', [str(path)])
        assert result['selected'] == [{'name': 'résumé.pdf', 'size': len(data), 'type': 'application/pdf'}]
        assert sum(c['op'] == 'upload_commit' for c in calls) == 1
        assert sum(c['op'] == 'upload_chunk' for c in calls) == 3
        assert all('paths' not in c and str(tmp_path) not in json.dumps(c) for c in calls)
        uploaded = await page.evaluate('''async () => {
          const f=document.querySelector('#cv').files[0];
          const hash=await crypto.subtle.digest('SHA-256',await f.arrayBuffer());
          return Array.from(new Uint8Array(hash), b=>b.toString(16).padStart(2,'0')).join('');
        }''')
        assert uploaded == hashlib.sha256(data).hexdigest()
        assert await page.evaluate('({changes,inputs})') == {'changes': 1, 'inputs': 1}
        snap = await page.evaluate("handler({drima:true,op:'snapshot'})")
        file_control = next(c for c in snap['controls'] if c['type'] == 'file')
        assert file_control['selector'] == '#cv'
        assert file_control['files'] == ['résumé.pdf']
        assert file_control['accept'] == '.pdf'
        with pytest.raises(Exception, match='expired'):
            await bridge({'op': 'upload_commit', 'upload_id': next(c['upload_id'] for c in calls if c['op'] == 'upload_commit')})
        assert await page.evaluate('changes') == 1
        # A staged upload cannot be duplicated, committed early or redirected.
        meta = {'name': 'a.pdf', 'size': 2, 'type': 'application/pdf', 'last_modified': 0}
        staged = await bridge({'op': 'upload_begin', 'selector': '#cv', 'files': [meta]})
        ctx = {'upload_id': staged['upload_id']}
        with pytest.raises(Exception, match='incomplete'):
            await bridge({'op': 'upload_commit', **ctx})
        chunk = {'op': 'upload_chunk', **ctx, 'file_index': 0, 'offset': 0,
                 'data': base64.b64encode(b'hi').decode()}
        await bridge(chunk)
        with pytest.raises(Exception, match='duplicate'):
            await bridge(chunk)
        await page.evaluate("document.querySelector('#cv').replaceWith(document.querySelector('#cv').cloneNode())")
        with pytest.raises(Exception, match='target changed'):
            await bridge({'op': 'upload_commit', **ctx})
        await bridge({'op': 'upload_abort', **ctx})
        assert await page.evaluate('changes') == 1
        for selector, files, error in [
            ('#cv', [{**meta, 'name': 'a.exe', 'type': 'application/octet-stream'}], 'accept'),
            ('#cv', [meta, meta], 'file count'),
            ('#cv', [{**meta, 'size': 26*1024*1024}], 'limit'),
            ('#cv', [{**meta, 'name': '../a.pdf'}], 'metadata'),
        ]:
            if error:
                with pytest.raises(Exception, match=error):
                    await bridge({'op':'upload_begin','selector':selector,'files':files})
        await page.evaluate("document.querySelector('#cv').disabled=true")
        with pytest.raises(Exception, match='enabled file input'):
            await bridge({'op':'upload_begin','selector':'#cv','files':[meta]})
        # Multiple files, including an empty file, use one selection event.
        await page.set_content('<label for="many">Files</label><input id="many" type="file" multiple accept=".pdf,text/plain" hidden>')
        first = await bridge({'op':'upload_begin','selector':'#many','files':[
            {**meta,'size':0}, {**meta,'name':'b.txt','type':'text/plain'}]})
        ctx = {'upload_id': first['upload_id']}
        await bridge({'op':'upload_chunk',**ctx,'file_index':1,'offset':0,
                      'data':base64.b64encode(b'hi').decode()})
        result = await bridge({'op':'upload_commit',**ctx})
        assert [f['size'] for f in result['selected']] == [0,2]
        await browser.close()


async def test_upload_path_limits_and_unknown_commit(tmp_path, monkeypatch):
    file = tmp_path / 'cv.pdf'
    file.write_bytes(b'%PDF-synthetic')
    calls = []
    async def fake(payload):
        calls.append(payload)
        if payload['op'] == 'status':
            return {'protocol_version': 1, 'addon_version': '0.2.5', 'capabilities': ['upload']}
        if payload['op'] == 'upload_begin':
            return {'upload_id': 'test'}
        if payload['op'] == 'upload_commit':
            raise TimeoutError('unknown upload outcome')
        return {}
    monkeypatch.setattr(regular, 'request', fake)
    for paths in ([], ['relative.pdf'], [str(tmp_path)], [str(file)]*11):
        with pytest.raises((ValueError, OSError)):
            await regular.upload('7', '#cv', paths)
    assert not calls
    large = tmp_path / 'large.pdf'
    with large.open('wb') as stream:
        stream.truncate(26 * 1024 * 1024)
    with pytest.raises(ValueError, match='25 MiB'):
        await regular.upload('7', '#cv', [str(large)])
    assert not calls
    fifo = tmp_path / 'pipe'
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular files'):
        await regular.upload('7', '#cv', [str(fifo)])
    with pytest.raises(TimeoutError, match='unknown'):
        await regular.upload('7', '#cv', [str(file)])
    assert [c['op'] for c in calls] == ['status', 'upload_begin', 'upload_chunk', 'upload_commit', 'upload_abort']


async def test_upload_tool_is_discoverable():
    server = regular.create_regular_server()
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == 'regular_browser_upload')
    assert set(tool.input_schema['required']) == {'tab_id', 'selector', 'paths'}
