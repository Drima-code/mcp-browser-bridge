"""Verify uploads through an installed Firefox extension on a synthetic local form.

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
    done, events, reports = threading.Event(), [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = b'''<!doctype html><label for="cv">CV</label>
              <input id="cv" type="file" accept=".pdf" hidden>
              <script>
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
            await new Promise(resolve=>setTimeout(resolve,300));
            await fetch(base+'/report',{method:'POST',body:JSON.stringify({result,replayRejected})});
          } catch(error) {
            await fetch(base+'/report',{method:'POST',body:JSON.stringify({error:error.message})});
          }
        })();'''.replace('BASE', json.dumps(base)).replace('SIZE', str(len(expected))).replace('CHUNKS', json.dumps(chunks))
        (directory / 'smoke.js').write_text(harness)
        command = ['npx', '--yes', 'web-ext', 'run', '--source-dir', str(directory),
                   '--firefox', '/usr/bin/firefox', '--arg=-headless',
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
                print('PASS: installed Firefox extension, chunked binary upload, exact SHA256, '
                      'one input/change event, duplicate commit rejected')
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
