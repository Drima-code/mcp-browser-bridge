"""Page dialogs (0.3.4): synthetic pages, a stubbed extension API and local IPC only."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from playwright.async_api import async_playwright

from mcp_browser_bridge import regular
from mcp_browser_bridge.native_host import read_message, write_message

ROOT = Path(__file__).resolve().parents[1]
CONTENT = (ROOT / "extension/content.js").read_text()
DIALOGS = (ROOT / "extension/dialogs.js").read_text()
SHIM = """() => {
  window.browser = {runtime:{onMessage:{addListener:fn => window.handler = fn},
    sendMessage:async m => { (window.reports = window.reports || []).push(m); return {}; }}};
  window.wrappedJSObject = window;
  window.exportFunction = (fn, target, options) => { target[options.defineAs] = fn; return fn; };
}"""


async def hooked_page(p, html):
    browser = await p.firefox.launch(headless=True)
    page = await browser.new_page()
    # Playwright dismisses native dialogs: it stands in for the user here.
    page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
    await page.set_content(html)
    await page.evaluate(SHIM)
    await page.evaluate(DIALOGS)
    await page.evaluate(CONTENT)
    return browser, page


ACT = "r=>handler({drima:true,op:'act',action:'click',...r})"


async def test_act_answers_prompts_with_a_value_but_never_credentials():
    async with async_playwright() as p:
        browser, page = await hooked_page(
            p, '<button id="name" onclick="window.v=prompt(\'Project name?\',\'x\')">Name</button>'
               '<button id="verify" onclick="window.pwv=prompt(\'Enter your password\')">Verify</button>')
        result = await page.evaluate(ACT, {"selector": "#name", "dialog": "accept", "dialog_value": "Corpus A"})
        assert await page.evaluate("window.v") == "Corpus A"
        assert result["dialogs"] == [{"type": "prompt", "message": "Project name?", "answered": "accept"}]
        await page.evaluate(ACT, {"selector": "#name", "dialog": "accept"})
        assert await page.evaluate("window.v") == "x"  # No value: the page's default.
        result = await page.evaluate(ACT, {"selector": "#verify", "dialog": "accept", "dialog_value": "hunter2"})
        assert await page.evaluate("window.pwv") is None
        assert result["dialogs"][0]["answered"] == "dismiss_credential_prompt"
        assert "hunter2" not in json.dumps(result)
        for bad in [{"dialog_value": "x" * 501}, {"dialog_window_ms": 3001}, {"settle_ms": 2001}, {"settle_ms": -1}]:
            with pytest.raises(Exception, match="dialog_value|dialog_window_ms|settle_ms"):
                await page.evaluate(ACT, {"selector": "#name", **bad})
        await browser.close()


async def test_prompt_loop_is_answered_at_most_five_times_then_shown_natively():
    async with async_playwright() as p:
        browser, page = await hooked_page(
            p, '<button id="loop" onclick="window.n=0;let v;do{window.n++;v=prompt(\'Count?\')}'
               'while(!/^[1-9]$/.test(v??\'\') && window.n<7)">Set</button>')
        result = await page.evaluate(ACT, {"selector": "#loop", "dialog": "accept", "dialog_value": ""})
        assert len(result["dialogs"]) == 5 and result["dialogs_limit_reached"] is True
        assert await page.evaluate("window.n") == 7  # Two more reached the native (user) path.
        reports = [r["drima_dialog"] for r in await page.evaluate("window.reports")]
        assert [r["event"] for r in reports] == ["open", "closed", "open", "closed"]
        assert reports[0]["over_limit"] is True and len(reports[0]["act_dialogs"]) == 5
        assert reports[0]["type"] == "prompt" and reports[0]["message"] == "Count?"
        await browser.close()


async def test_late_dialog_window_settle_and_armed_answers():
    async with async_playwright() as p:
        browser, page = await hooked_page(
            p, '<button id="late" onclick="setTimeout(()=>window.lateV=confirm(\'Late?\'),300)">Late</button>'
               '<button id="t" onclick="window.tv=prompt(\'Label?\')">T</button>')
        result = await page.evaluate(ACT, {"selector": "#late", "dialog": "accept", "dialog_window_ms": 0})
        assert "dialogs" not in result
        await page.wait_for_function("window.lateV !== undefined")
        assert await page.evaluate("window.lateV") is False  # Shown natively; the user dismissed it.
        assert (await page.evaluate("window.reports"))[0]["drima_dialog"]["event"] == "open"
        result = await page.evaluate(ACT, {"selector": "#late", "dialog": "accept", "dialog_window_ms": 600})
        assert result["dialogs"][0]["message"] == "Late?" and await page.evaluate("window.lateV") is True
        # An armed answer is used once, only for a matching dialog, until it expires.
        arm = "a=>handler({drima:true,op:'dialog_arm',arm:a})"
        await page.evaluate(arm, {"id": "a1", "answer": "accept", "value": "T1", "type": "prompt",
                                  "match": "Label", "expires": 9e15})
        await page.evaluate(arm, {"id": "a2", "answer": "accept", "value": "old", "type": None,
                                  "match": None, "expires": 1})
        await page.evaluate("document.getElementById('t').onclick()")
        assert await page.evaluate("window.tv") == "T1"
        await page.evaluate("window.reports = []; document.getElementById('t').onclick()")
        assert await page.evaluate("window.tv") is None  # Used up, the expired one ignored: native.
        assert [r["drima_dialog"]["event"] for r in await page.evaluate("window.reports")] == ["open", "closed"]
        started = await page.evaluate("Date.now()")
        paused = await page.evaluate("r=>handler({drima:true,op:'wait',...r})", {"settle_ms": 200})
        assert paused["paused_ms"] == 200 and await page.evaluate("Date.now()") - started >= 190
        with pytest.raises(Exception, match="selector, or settle_ms"):
            await page.evaluate("r=>handler({drima:true,op:'wait',...r})", {})
        assert (await page.evaluate("handler({drima:true,op:'liveness'})"))["alive"] is True
        await browser.close()


BACKGROUND = r"""
const vm=require('vm'), fs=require('fs'), assert=require('assert'), crypto=require('crypto').webcrypto;
const events={}, messages=[], sent=[], registered=[], reloads=[];
let listener, mode='all', paused=false, actReply=null, removed=[];
const tabs=new Map([[1,{id:1,title:'Tool',url:'http://127.0.0.1:8765/tool.html',windowId:7,active:true}],
 [2,{id:2,title:'Other',url:'https://other.example/',windowId:7}]]);
const event=name=>({addListener:fn=>events[name]=fn});
const port={onMessage:{addListener:fn=>listener=fn},onDisconnect:event('disconnect'),postMessage:m=>messages.push(m)};
const browser={windows:{update:async()=>{}},
 contentScripts:{register:async o=>{const r={o,unregistered:false,unregister:async()=>{r.unregistered=true}};registered.push(r);return r}},
 tabs:{get:async id=>{if(!tabs.has(id))throw new Error('no tab');return tabs.get(id)},executeScript:async(id,o)=>{assert.equal(o.runAt,'document_start')},
  remove:async id=>{removed.push(id);tabs.delete(id);events.removed(id)},reload:async id=>reloads.push(id),
  sendMessage:async(id,r,o)=>{sent.push(r.op);
   if(r.op==='content_status')return {content_version:'0.3.4'};
   if(r.op==='liveness')return paused?new Promise(()=>{}):{alive:true};
   if(r.op==='act')return actReply||new Promise(()=>{});
   return {ok:true}},
  query:async()=>Array.from(tabs.values()),onRemoved:event('removed'),onUpdated:event('updated')},
 runtime:{id:'bridge',getManifest:()=>({version:'0.3.4'}),getURL:p=>'moz-extension://bridge/'+p,connectNative:()=>port,onMessage:event('ui')},
 permissions:{contains:async p=>!p.permissions,onRemoved:event('permissionRemoved')},
 storage:{local:{get:async()=>({mode}),set:async()=>{}}},
 browserAction:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{setTimeout,clearTimeout,browser,URL,Map,Set,String,Number,Date,Math,Promise,JSON,crypto});
const call=async r=>{await listener({id:'x',...r});const m=messages.at(-1);if(m.error)throw new Error(m.error);return m.result};
const page=(tab,report)=>events.ui({drima_dialog:report},{id:'bridge',tab:{id:tab}});
const confirmReport={id:'d1',type:'confirm',message:'Found unsaved work. Restore it?',origin:'http://127.0.0.1:8765',frame:'top',since:'t'};
(async()=>{
 await events.ui({ui:'state'},{id:'bridge',url:'moz-extension://bridge/popup.html'});
 // Automatic mode registers the hook for every website frame at document_start.
 assert.equal(registered.length,1); assert.deepEqual(registered[0].o.js,[{file:'dialogs.js'}]);
 assert.equal(registered[0].o.runAt,'document_start'); assert.equal(registered[0].o.allFrames,true);
 const st=await call({op:'status'});
 for (const c of ['page_dialogs','dialog_values','dialog_window','settle']) assert(st.capabilities.includes(c));
 // An act in flight resolves at once when the page shows a dialog it did not answer.
 const pendingAct=call({op:'act',tab_id:'1',action:'click',selector:'#x'});
 await new Promise(r=>setTimeout(r,20));
 await page(1,{event:'open',...confirmReport,act_dialogs:[{type:'prompt',message:'n',answered:'accept'}],over_limit:true});
 const acted=await pendingAct;
 assert.equal(acted.outcome,'uncertain'); assert.equal(acted.pending_dialog.message,'Found unsaved work. Restore it?');
 assert.equal(acted.dialogs.length,1);
 // Page operations fail fast with the dialog's text; nothing reaches the page.
 const before=sent.length;
 for (const op of ['snapshot','wait','act','upload_begin'])
   await assert.rejects(()=>call({op,tab_id:'1',selector:'#x'}),/blocked by a pending page dialog: confirm "Found unsaved work/);
 assert.equal(sent.length,before);
 const status=await call({op:'status',tab_id:'1'});
 assert.equal(status.pending_dialog.type,'confirm'); assert.equal(status.content_version,null);
 let list=await call({op:'dialog',tab_id:'1',action:'list'});
 assert.equal(list.pending[0].origin,'http://127.0.0.1:8765'); assert.equal(list.pending[0].shown,'native');
 // On screen: not answerable in place; reload=true arms the same dialog and reloads once.
 await assert.rejects(()=>call({op:'dialog',tab_id:'1',action:'accept'}),/reload=true/);
 await assert.rejects(()=>call({op:'dialog',tab_id:'1',action:'accept',value:'x'.repeat(501)}),/500/);
 const r=await call({op:'dialog',tab_id:'1',action:'accept',reload:true,value:'a"b</script>'});
 assert.equal(r.reloaded,true); assert.deepEqual(reloads,[1]);
 const arm=registered[1];
 assert.deepEqual(arm.o.matches,['http://127.0.0.1/*']); assert.equal(arm.o.runAt,'document_start');
 const code=arm.o.js[0].code, armed=JSON.parse(code.slice(code.indexOf('push(')+5,-2));
 assert.equal(armed.type,'confirm'); assert.equal(armed.match,'Found unsaved work. Restore it?');
 assert.equal(armed.value,'a"b</script>'); assert.equal(armed.answer,'accept');
 // The page used it: the registration goes away and the answer is listed.
 await page(1,{event:'answered',...confirmReport,answered:'accept',answered_by:'armed',arm_id:armed.id});
 assert.equal(arm.unregistered,true);
 list=await call({op:'dialog',tab_id:'1',action:'list'});
 assert.equal(list.pending.length,0); assert.equal(list.recent.at(-1).answered_by,'armed');
 // No dialog on screen: arm in place for the next one.
 const inplace=await call({op:'dialog',tab_id:'1',action:'dismiss',type:'prompt',match:'Label'});
 assert.equal(inplace.armed.answer,'dismiss'); assert(sent.includes('dialog_arm'));
 // A user-answered dialog clears the block.
 await page(1,{event:'open',...confirmReport,id:'d2'});
 await page(1,{event:'closed',...confirmReport,id:'d2',answered_by:'user'});
 actReply={ok:true};
 assert.equal((await call({op:'act',tab_id:'1',action:'click',selector:'#x'})).ok,true);
 // A page paused by a dialog the hook never saw: liveness fails, clear error.
 paused=true;
 await assert.rejects(()=>call({op:'snapshot',tab_id:'1'}),/Tab is paused/);
 assert.equal((await call({op:'dialog',tab_id:'1',action:'list'})).paused_without_report,true);
 await assert.rejects(()=>call({op:'snapshot',tab_id:'1'}),/pending page dialog/);
 // Reports from tabs the bridge may not access are ignored.
 await page(99,{event:'open',...confirmReport});
 // Close works on the paused tab and drops its state.
 assert.equal((await call({op:'close',tab_id:'1'})).closed,true); assert.deepEqual(removed,[1]);
 // Manual mode: hook unregistered; reload answering refused.
 await events.ui({ui:'manual'},{id:'bridge',url:'moz-extension://bridge/popup.html'});
 assert.equal(registered[0].unregistered,true);
 console.log('ok');
})().catch(e=>{console.error(e);process.exit(1)});
"""


def test_background_tracks_page_dialogs_and_never_hangs():
    result = subprocess.run(["node", "-e", BACKGROUND, str(ROOT / "extension/background.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout


async def test_dialog_tool_payloads_and_page_independent_ops(monkeypatch):
    payloads, statuses = [], []

    async def fake(payload):
        if payload["op"] == "status":
            statuses.append(payload.get("tab_id"))
            return {"protocol_version": 1, "addon_version": "0.3.4",
                    "capabilities": ["act", "wait", "page_dialogs", "dialog_values", "dialog_window",
                                     "settle", "dialogs", "close", "screenshot"]}
        payloads.append(payload)
        return {"ok": True}

    monkeypatch.setattr(regular, "request", fake)
    server = regular.create_regular_server()
    await server.call_tool("regular_browser_dialog", {"tab_id": "3", "action": "accept", "value": "A",
                                                      "reload": True})
    assert payloads[-1] == {"op": "dialog", "tab_id": "3", "action": "accept", "ttl_ms": 30000,
                            "value": "A", "reload": True}
    await server.call_tool("regular_browser_close", {"tab_id": "3"})
    # Neither needs (or waits for) the paused page's content listener.
    assert statuses == [None, None]
    await server.call_tool("regular_browser_act", {"tab_id": "3", "action": "click", "selector": "#a",
                                                   "dialog": "accept", "dialog_value": "", "dialog_window_ms": 1500,
                                                   "settle_ms": 300})
    assert payloads[-1]["dialog_value"] == "" and payloads[-1]["dialog_window_ms"] == 1500
    assert payloads[-1]["settle_ms"] == 300 and statuses[-1] == "3"
    await server.call_tool("regular_browser_wait", {"tab_id": "3", "settle_ms": 500})
    assert payloads[-1]["settle_ms"] == 500 and payloads[-1]["selector"] == ""
    schemas = {t.name: t.input_schema for t in await server.list_tools()}
    assert schemas["regular_browser_act"]["properties"]["settle_ms"]["maximum"] == 2000
    assert schemas["regular_browser_wait"]["properties"]["settle_ms"]["maximum"] == 2000
    dialog = schemas["regular_browser_dialog"]["properties"]
    assert dialog["ttl_ms"]["maximum"] == 120000 and "reload" in dialog
    count = len(payloads)
    with pytest.raises(ToolError, match="selector, or settle_ms"):
        await server.call_tool("regular_browser_wait", {"tab_id": "3"})
    assert len(payloads) == count


async def test_bridge_failures_reach_the_client_with_their_text(monkeypatch):
    async def blocked(payload, required=()):
        raise RuntimeError('Tab blocked by a pending page dialog: confirm "Restore it?"')

    async def lost(payload, required=()):
        raise TimeoutError()

    server = regular.create_regular_server()
    monkeypatch.setattr(regular, "checked_request", blocked)
    with pytest.raises(ToolError, match='pending page dialog: confirm "Restore it\\?"') as caught:
        await server.call_tool("regular_browser_snapshot", {"tab_id": "1"})
    assert not isinstance(caught.value, UnexpectedToolError)
    monkeypatch.setattr(regular, "checked_request", lost)
    with pytest.raises(ToolError, match="TimeoutError; outcome unknown"):
        await server.call_tool("regular_browser_close", {"tab_id": "1"})


async def test_a_tab_specific_refusal_is_reported_as_is(monkeypatch):
    async def fake(payload):
        raise regular.AddonError("Tab is paused: the page is not running")

    monkeypatch.setattr(regular, "_request", fake)
    with pytest.raises(RuntimeError, match="^Tab is paused.*No requested action was sent"):
        await regular.checked_request({"op": "snapshot", "tab_id": "1"})


async def test_native_host_serves_requests_concurrently(tmp_path, monkeypatch):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    path = directory / "regular-browser.sock"
    monkeypatch.setattr(regular, "socket_path", lambda: path)
    process = subprocess.Popen([sys.executable, "-m", "mcp_browser_bridge.native_host"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={**os.environ, "DRIMA_REGULAR_BROWSER_DATA": str(directory)})
    try:
        for _ in range(100):
            if path.exists():
                break
            await asyncio.sleep(0.02)
        # A request the extension never answers (a paused page) ...
        stuck = asyncio.create_task(regular.request({"op": "snapshot", "tab_id": "1"}))
        first = await asyncio.wait_for(asyncio.to_thread(read_message, process.stdout), 5)
        assert first["op"] == "snapshot"
        # ... does not hold back the next one.
        task = asyncio.create_task(regular.request({"op": "tabs"}))
        second = await asyncio.wait_for(asyncio.to_thread(read_message, process.stdout), 5)
        assert second["op"] == "tabs"
        write_message(process.stdin, {"id": second["id"], "result": {"tabs": []}})
        assert await asyncio.wait_for(task, 5) == {"tabs": []}
        write_message(process.stdin, {"id": first["id"], "result": {"late": True}})
        assert await asyncio.wait_for(stuck, 5) == {"late": True}
    finally:
        process.stdin.close()
        await asyncio.to_thread(process.wait, 5)
