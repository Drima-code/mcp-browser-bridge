"""Verify uploads and custom dropdowns through an installed Firefox extension.

Uses a disposable web-ext Firefox profile, never the user's browser or accounts.
Requires node/npm (web-ext) and Firefox. No real application is submitted.
"""

import base64
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    expected = bytes(range(256)) * 1000
    download_bytes = b'%PDF-1.4\nSynthetic download tracking fixture\n%%EOF\n'
    done, events, reports, click_events = threading.Event(), [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == '/download':
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Disposition', 'attachment; filename="synthetic-bridge.pdf"')
                self.send_header('Content-Length', str(len(download_bytes)))
                self.end_headers()
                self.wfile.write(download_bytes)
                return
            body = b'''<!doctype html><label for="cv">CV</label>
              <input id="cv" type="file" accept=".pdf" hidden>
              <form id="application"><label for="region">Region</label>
              <div id="widget"><input id="region" role="combobox" aria-required="true"
                aria-expanded="false" aria-controls="options"></div>
              <div id="options" role="listbox" hidden>
                <div id="yes" role="option" aria-selected="false">Yes</div>
              </div><button id="send">Submit synthetic form</button></form>
              <a id="save-file" href="/download" download="synthetic-bridge.pdf">Save synthetic file</a>
              <button id="show-viewer" onmouseenter="document.querySelector('#viewer').hidden=false">Preview</button>
              <section id="viewer" role="dialog" hidden><p>Synthetic PDF text</p>
                <div aria-hidden="true"><button id="download">Download</button></div>
                <button id="close">Close</button></section>
              <script>
              let selected='', clicks=0, submissions=0;
              const region=document.querySelector('#region'), options=document.querySelector('#options');
              document.querySelector('#widget').addEventListener('mousedown',e=>{
                e.preventDefault(); region.focus(); options.hidden=false;
                region.setAttribute('aria-expanded','true');
              });
              options.addEventListener('mousedown',e=>e.preventDefault());
              options.addEventListener('click',e=>{
                selected=e.target.textContent; region.value=selected; options.hidden=true;
                e.target.setAttribute('aria-selected','true'); region.setAttribute('aria-expanded','false');
              });
              document.querySelector('#send').addEventListener('click',()=>clicks++);
              document.querySelector('#application').addEventListener('submit',async e=>{
                e.preventDefault(); submissions++;
                await fetch('/click-event',{method:'POST',body:JSON.stringify({selected,clicks,submissions})});
              });
              let changes=0, inputs=0;
              document.querySelector('#cv').addEventListener('input',()=>inputs++);
              document.querySelector('#cv').addEventListener('change',async e=>{
                changes++;
                const file=e.target.files[0];
                const hash=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
                await fetch('/event',{method:'POST',body:JSON.stringify({
                  name:file.name,size:file.size,changes,inputs,
                  hash:Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('')
                })});
              });</script>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/event':
                events.append(value)
            elif self.path == '/click-event':
                click_events.append(value)
            else:
                reports.append(value)
                done.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

    with tempfile.TemporaryDirectory(prefix='drima-upload-smoke-') as temp:
        directory = Path(temp)
        for source in (root / 'extension').iterdir():
            if source.is_file():
                shutil.copyfile(source, directory / source.name)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        manifest = json.loads((directory / 'manifest.json').read_text())
        manifest['browser_specific_settings']['gecko']['id'] = 'upload-smoke@drima.local'
        manifest['permissions'].append('http://127.0.0.1/*')
        manifest['permissions'].append('downloads')
        manifest['optional_permissions'].remove('downloads')
        manifest['background']['scripts'].append('smoke.js')
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        chunks = [base64.b64encode(expected[i:i+96*1024]).decode()
                  for i in range(0, len(expected), 96*1024)]
        harness = '''(async () => {
          const base=BASE;
          try {
            await ready;
            const tab=await browser.tabs.create({url:base+'/form'});
            for(let i=0;i<100;i++) {
              const state=await browser.tabs.executeScript(tab.id,{code:'document.readyState'});
              if(state[0]==='complete')break;
              await new Promise(resolve=>setTimeout(resolve,100));
            }
            approved.set(tab.id,origin(base));
            const context={tab_id:String(tab.id)};
            const status=await dispatch({op:'status',...context});
            if(status.addon_version!=='0.2.5' || status.content_version!=='0.2.5' || status.protocol_version!==1)
              throw new Error('Protocol/content handshake failed');
            const begin=await dispatch({op:'upload_begin',...context,selector:'#cv',files:[{
              name:'synthetic.pdf',size:SIZE,type:'application/pdf',last_modified:0}]});
            const upload={...context,upload_id:begin.upload_id};
            let offset=0;
            for(const data of CHUNKS) {
              await dispatch({op:'upload_chunk',...upload,file_index:0,offset,data});
              offset+=atob(data).length;
            }
            const result=await dispatch({op:'upload_commit',...upload});
            let replayRejected=false;
            try { await dispatch({op:'upload_commit',...upload}); }
            catch(error){replayRejected=error.message.includes('expired');}
            await dispatch({op:'act',...context,action:'click',selector:'#region'});
            const snapshot=await dispatch({op:'snapshot',...context});
            const combo=snapshot.controls.find(c=>c.id==='region');
            if(!combo?.expanded || !combo.required)throw new Error('Combobox did not open');
            const option=snapshot.controls.find(c=>c.role==='option' && c.label==='Yes');
            if(!option)throw new Error('Visible option missing from snapshot');
            await dispatch({op:'act',...context,action:'click',selector:option.selector});
            await dispatch({op:'act',...context,action:'click',selector:'#send'});
            const watch=await dispatch({op:'downloads',...context,action:'watch',expected_filename:'synthetic-bridge.pdf'});
            await dispatch({op:'act',...context,action:'click',selector:'#save-file'});
            let download;
            for(let i=0;i<100;i++) {
              await new Promise(resolve=>setTimeout(resolve,100));
              const observed=await dispatch({op:'downloads',...context,action:'list',watch_id:watch.watch_id});
              download=observed.records.find(r=>r.state==='complete');
              if(download)break;
            }
            if(!download?.saved_path)throw new Error('Synthetic download was not observed complete');
            await dispatch({op:'act',...context,action:'hover',selector:'#show-viewer'});
            const viewer=await dispatch({op:'snapshot',...context,limit:1});
            if(viewer.scope_reason!=='dialog' || viewer.controls[0]?.id!=='download' ||
               !viewer.controls[0].aria_hidden || viewer.next_offset!==1)
              throw new Error('Viewer toolbar discovery failed');
            const tail=await dispatch({op:'snapshot',...context,scope:viewer.scope_selector,offset:viewer.next_offset});
            if(tail.controls[0]?.id!=='close' || tail.next_offset!==null)
              throw new Error('Viewer control pagination failed');
            await new Promise(resolve=>setTimeout(resolve,300));
            await fetch(base+'/report',{method:'POST',body:JSON.stringify({result,replayRejected,download})});
          } catch(error) {
            await fetch(base+'/report',{method:'POST',body:JSON.stringify({error:error.message})});
          }
        })();'''.replace('BASE', json.dumps(base)).replace('SIZE', str(len(expected))).replace('CHUNKS', json.dumps(chunks))
        (directory / 'smoke.js').write_text(harness)
        command = ['npx', '--yes', 'web-ext', 'run', '--source-dir', str(directory),
                   '--firefox', '/usr/bin/firefox', '--arg=-headless',
                   '--pref=browser.download.folderList=2',
                   '--pref=browser.download.useDownloadDir=true',
                   '--pref=browser.download.dir=' + str(directory / 'downloads'),
                   '--no-reload', '--no-input']
        with (directory / 'runner.log').open('w+') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            try:
                if not done.wait(45):
                    log.seek(0)
                    raise RuntimeError('Firefox upload smoke timeout: ' + log.read()[-4000:])
                if len(reports) != 1 or reports[0].get('error'):
                    raise RuntimeError(f'Extension upload failed: {reports}')
                assert reports[0]['replayRejected']
                assert reports[0]['result']['selected'][0]['name'] == 'synthetic.pdf'
                assert events == [{'name': 'synthetic.pdf', 'size': len(expected),
                                   'changes': 1, 'inputs': 1,
                                   'hash': hashlib.sha256(expected).hexdigest()}], events
                assert click_events == [{'selected': 'Yes', 'clicks': 1, 'submissions': 1}], click_events
                downloaded = Path(reports[0]['download']['saved_path'])
                assert downloaded.parent == directory / 'downloads'
                assert downloaded.read_bytes() == download_bytes
                print('PASS: installed Firefox extension, chunked binary upload, exact SHA256, '
                      'one input/change event, duplicate commit rejected, mousedown dropdown, '
                      'selected option, exactly one synthetic submission, hover-revealed viewer, '
                      'visible aria-hidden toolbar, control pagination, protocol negotiation '
                      'and completed download verified on disk')
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
