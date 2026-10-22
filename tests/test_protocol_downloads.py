"""Protocol negotiation and future-only download observation on synthetic data."""

import subprocess
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from mcp_browser_bridge import regular


async def test_popup_download_permission_denial_grant_and_revoke():
    extension = Path(__file__).resolve().parents[1] / "extension"
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        await page.set_content((extension / "popup.html").read_text())
        await page.evaluate("""() => {
          window.calls=[];window.grant=false;window.permitted=false;
          window.browser={runtime:{sendMessage:async r=>{
            calls.push({ui:r.ui});
            if(r.ui==='state')return {mode:'manual',eligible:true,connected:true,
              addonVersion:'0.2.5',downloadsPermission:permitted};
            return {ok:true};
          }},permissions:{request:async q=>{calls.push({request:q});permitted=grant;return grant;},
            remove:async q=>{calls.push({remove:q});permitted=false;return true;}}};
        }""")
        await page.evaluate((extension / "popup.js").read_text())
        await page.wait_for_function("document.querySelector('#status').textContent.includes('0.2.5')")
        await page.locator("#downloads").click()
        await page.wait_for_function("document.querySelector('#error').textContent.includes('denied')")
        assert not await page.evaluate("calls.some(c=>c.ui==='downloads')")
        await page.evaluate("grant=true")
        await page.locator("#downloads").click()
        await page.wait_for_function("document.querySelector('#downloads').disabled")
        assert await page.evaluate("calls.filter(c=>c.ui==='downloads').length") == 1
        await page.locator("#downloads-off").click()
        await page.wait_for_function("document.querySelector('#downloads-off').disabled")
        assert await page.evaluate("calls.some(c=>c.remove?.permissions?.[0]==='downloads')")
        assert not await page.evaluate("calls.some(c=>['all','toggle'].includes(c.ui))")
        await browser.close()


@pytest.mark.parametrize("reply", [None, {}, [], {"protocol_version": 2},
    {"protocol_version": 1, "capabilities": None},
    {"protocol_version": True}, {"protocol_version": 1, "addon_version": "0.2.4", "capabilities": []}])
async def test_incompatible_addon_never_receives_the_requested_action(monkeypatch, reply):
    calls = []

    async def fake(payload):
        calls.append(payload)
        if reply is None:
            raise regular.AddonError("Unsupported operation")
        return reply

    monkeypatch.setattr(regular, "request", fake)
    with pytest.raises(RuntimeError, match="[Aa]ddon|protocol"):
        await regular.checked_request({"op": "act", "tab_id": "7", "action": "hover"}, ("hover",))
    assert calls == [{"op": "status", "tab_id": "7"}]


async def test_compatible_action_is_never_replayed_on_unknown_outcome(monkeypatch):
    calls = []

    async def fake(payload):
        calls.append(payload)
        if payload["op"] == "status":
            return {"protocol_version": 1, "addon_version": "0.2.5", "capabilities": ["act"]}
        raise TimeoutError("unknown action outcome")

    monkeypatch.setattr(regular, "request", fake)
    with pytest.raises(TimeoutError, match="unknown"):
        await regular.checked_request({"op": "act", "tab_id": "7"}, ("act",))
    assert [c["op"] for c in calls] == ["status", "act"]


def test_download_lifecycle_privacy_and_stale_content():
    script = Path(__file__).resolve().parents[1] / "extension/background.js"
    harness = r"""
const vm=require('vm'),fs=require('fs'),assert=require('assert'),crypto=require('crypto').webcrypto;
let now=1700000000000,downloadsPermission=false,contentVersion='0.2.5',active=1,listener,actions=0,revokeOnSearch=false;
const events={},messages=[],items=new Map(),searches=[],tabs=new Map([
 [1,{id:1,title:'Synthetic',url:'https://medical.example/inbox'}],
 [2,{id:2,title:'Second',url:'https://medical.example/inbox'}]]);
class ClockDate extends Date { static now(){return now;} }
const event=name=>({addListener:fn=>events[name]=fn,removeListener:()=>{}});
const port={onMessage:{addListener:fn=>listener=fn},onDisconnect:event('disconnect'),postMessage:m=>messages.push(m)};
const browser={tabs:{get:async id=>tabs.get(id),query:async q=>[tabs.get(active)],
 executeScript:async()=>{},sendMessage:async(id,r)=>{
  if(r.op==='content_status')return {content_version:contentVersion}; actions++;return {ok:true};
 },onRemoved:event('removed'),onUpdated:event('updated')},
 runtime:{id:'bridge',getManifest:()=>({version:'0.2.5'}),getURL:p=>'moz-extension://bridge/'+p,
 connectNative:()=>port,onMessage:event('ui')},
 permissions:{contains:async q=>q.permissions?downloadsPermission:false,onRemoved:event('permissionRemoved')},
 storage:{local:{get:async()=>({}),set:async()=>{}}},browserAction:{setBadgeText:async()=>{}},
 downloads:{onCreated:event('created'),onChanged:event('changed'),search:async q=>{
  assert.deepEqual(Object.keys(q),['id']);searches.push(q.id);
  if(revokeOnSearch)downloadsPermission=false;
  return items.has(q.id)?[items.get(q.id)]:[];
 }}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{browser,URL,Map,Set,Date:ClockDate,crypto});
const ui=r=>events.ui(r,{id:'bridge',url:'moz-extension://bridge/popup.html'});
async function call(r){await listener({id:'test',...r});const m=messages.at(-1);if(m.error)throw Error(m.error);return m.result;}
async function created(id,extra={}){
 now+=10;const item={id,startTime:new Date(now).toISOString(),incognito:false,
  referrer:'https://medical.example/inbox?private=never-return',url:'https://files.example/file?token=never-return',
  filename:'/tmp/invoice.pdf',state:'in_progress',bytesReceived:2,totalBytes:20,exists:true,...extra};
 items.set(id,item);events.created(item);await new Promise(r=>setTimeout(r,0));return item;
}
(async()=>{
 await ui({ui:'toggle',tabId:1});
 const status=await call({op:'status'});assert.equal(status.addon_version,'0.2.5');
 assert.equal(status.protocol_version,1);assert.equal(status.downloads_permission,false);
 await assert.rejects(()=>call({op:'downloads',tab_id:'1',action:'watch',expected_filename:'invoice.pdf'}),/permission/);
 contentVersion='0.2.4';
 await assert.rejects(()=>call({op:'act',tab_id:'1',action:'click',selector:'#send'}),/reload/);
 assert.equal(actions,0);contentVersion='0.2.5';
 assert.equal((await call({op:'status',tab_id:'1'})).content_version,'0.2.5');
 downloadsPermission=true;await ui({ui:'downloads'});
 const w=await call({op:'downloads',tab_id:'1',action:'watch',expected_filename:'invoice.pdf'});
 assert.equal(w.state,'waiting');assert.equal(searches.length,0);
 for(const name of ['../invoice.pdf','/tmp/invoice.pdf',''])
  await assert.rejects(()=>call({op:'downloads',tab_id:'1',action:'watch',expected_filename:name}),/basename/);
 await created(1,{incognito:true});await created(2,{referrer:'https://other.example/'});
 await created(3,{filename:'/tmp/unrelated.pdf'});
 await created(4,{startTime:new Date(now-10000).toISOString()});
 let view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.records.length,0);assert.equal(searches.length,0);
 const item=await created(5,{filename:''});
 item.filename='/tmp/invoice (1).pdf';item.state='complete';item.bytesReceived=20;
 events.changed({id:5,state:{current:'complete'}});await new Promise(r=>setTimeout(r,0));
 view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.records[0].saved_path,'/tmp/invoice (1).pdf');
 assert.equal(view.records[0].state,'complete');assert(!JSON.stringify(view).includes('never-return'));
 const failed=await created(6);failed.state='interrupted';failed.error='NETWORK_FAILED';
 events.changed({id:6,state:{current:'interrupted'}});await new Promise(r=>setTimeout(r,0));
 view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.records.find(r=>r.id===6).error,'NETWORK_FAILED');
 assert.equal(view.records.find(r=>r.id===6).saved_path,null);
 await created(7,{referrer:''});view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.unattributed_events,1);assert(!view.records.some(r=>r.id===7));
 await created(11,{state:'complete',filename:'/tmp/'+'界'.repeat(3000)+'/invoice.pdf'});
 view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.records.find(r=>r.id===11).saved_path,null);
 assert(view.records.find(r=>r.id===11).path_omitted);
 // Two approved tabs with the same origin/name cannot claim an exact association.
 active=2;await ui({ui:'toggle',tabId:2});
 const second=await call({op:'downloads',tab_id:'2',action:'watch',expected_filename:'invoice.pdf'});
 await created(8);
 view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.ambiguous_events,1);assert(!view.records.some(r=>r.id===8));
 await call({op:'downloads',tab_id:'2',action:'stop',watch_id:second.watch_id});
 now+=300001;await created(9);
 view=await call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id});
 assert.equal(view.state,'expired');assert(!view.records.some(r=>r.id===9));
 // Revocation clears observations; regrant does not restore the old watch.
 downloadsPermission=false;await events.permissionRemoved();downloadsPermission=true;await ui({ui:'downloads'});
 await assert.rejects(()=>call({op:'downloads',tab_id:'1',action:'list',watch_id:w.watch_id}),/Unknown/);
 const next=await call({op:'downloads',tab_id:'1',action:'watch',expected_filename:'invoice.pdf'});
 tabs.get(1).url='https://medical.example/another';events.updated(1,{url:tabs.get(1).url});
 await assert.rejects(()=>call({op:'downloads',tab_id:'1',action:'list',watch_id:next.watch_id}),/Unknown/);
 assert(searches.every(id=>[5,6,11].includes(id)));assert.equal(actions,0);
 // Permission revoked during an asynchronous metadata refresh must not leak paths.
 const race=await call({op:'downloads',tab_id:'1',action:'watch',expected_filename:'invoice.pdf'});
 await created(10,{state:'complete'});revokeOnSearch=true;
 await assert.rejects(()=>call({op:'downloads',tab_id:'1',action:'list',watch_id:race.watch_id}),/revoked/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run(["node", "-e", harness, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
