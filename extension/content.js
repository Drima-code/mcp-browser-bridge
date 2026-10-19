/* Isolated-world listener. No cookies/storage/network or arbitrary evaluation. */
(() => {
  if (globalThis.drimaApprovedTabInstalled) return;
  globalThis.drimaApprovedTabInstalled = true;
  const visible = e => !!e.getClientRects().length &&
    !["hidden", "collapse"].includes(getComputedStyle(e).visibility) &&
    !e.closest('[inert],[aria-hidden="true"]');
  const sensitive = e => e.matches('input[type="password"], input[type="hidden"]') ||
    /password|token|secret|verification.?code|one.?time/i.test(`${e.name || ""} ${e.id || ""} ${e.autocomplete || ""}`);
  const disabled = e => e.matches(':disabled,[aria-disabled="true"]') ||
    !!e.closest('[inert],[aria-disabled="true"]');
  const label = e => e.getAttribute("aria-label") ||
    (e.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)?.innerText || "").join(" ").trim() ||
    Array.from(e.labels || []).map(l => l.innerText.trim()).join(" ") || e.innerText?.trim().slice(0,180) || "";
  function selector(e) {
    if (e.id && document.querySelectorAll(`#${CSS.escape(e.id)}`).length === 1)
      return `#${CSS.escape(e.id)}`;
    const parts = [];
    for (let node = e; node && node !== document.documentElement; node = node.parentElement) {
      const index = Array.from(node.parentElement.children).indexOf(node) + 1;
      parts.unshift(`${node.localName}:nth-child(${index})`);
    }
    return `html > ${parts.join(" > ")}`;
  }
  const uploads = new Map();
  const maxUploadBytes = 25 * 1024 * 1024;
  const maxChunkBytes = 96 * 1024;
  const uploadVisible = e => visible(e) || Array.from(e.labels || []).some(visible);
  function fileTarget(target) {
    const elements = Array.from(document.querySelectorAll(target));
    if (elements.length !== 1) throw new Error(`Expected exactly one file input; got ${elements.length}`);
    const e = elements[0];
    if (!e.matches('input[type="file"]') || !uploadVisible(e) || sensitive(e) || disabled(e) || e.webkitdirectory)
      throw new Error("Expected an enabled file input with a visible control or label");
    return e;
  }
  function validateFiles(e, files) {
    if (!Array.isArray(files) || files.length < 1 || files.length > 10 || (!e.multiple && files.length > 1))
      throw new Error("Invalid file count for this input");
    let total = 0;
    const accepts = e.accept.toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
    for (const file of files) {
      if (typeof file.name !== "string" || !file.name || file.name.length > 255 || /[/\\\x00-\x1f]/.test(file.name) ||
          !Number.isSafeInteger(file.size) || file.size < 0 || typeof file.type !== "string" || file.type.length > 255 ||
          !Number.isSafeInteger(file.last_modified) || file.last_modified < 0)
        throw new Error("Invalid file metadata");
      total += file.size;
      if (total > maxUploadBytes) throw new Error("Upload exceeds the 25 MiB total limit");
      if (accepts.length && !accepts.some(a => a.startsWith('.') ? file.name.toLowerCase().endsWith(a) :
        a.endsWith('/*') ? file.type.toLowerCase().startsWith(a.slice(0,-1)) : file.type.toLowerCase() === a))
        throw new Error("File type does not match the input's accept filter");
    }
  }
  function upload(request) {
    for (const [id, state] of uploads) if (Date.now() - state.created > 300000) uploads.delete(id);
    if (request.op === "upload_begin") {
      if (uploads.size) throw new Error("Another upload is staged in this tab; abort it or wait for expiry");
      const element = fileTarget(request.selector);
      validateFiles(element, request.files);
      const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16).padStart(8,"0")).join("");
      uploads.set(id, {element, selector:request.selector, url:location.href, created:Date.now(),
        files:request.files.map(meta => ({...meta, chunks:[], received:0}))});
      return {upload_id:id, notice:"Staging only; no files attached yet"};
    }
    if (request.op === "upload_abort") {
      uploads.delete(request.upload_id);
      return {aborted:true};
    }
    const state = uploads.get(request.upload_id);
    if (!state) throw new Error("Upload expired or document changed; inspect before retrying");
    if (state.url !== location.href || !state.element.isConnected || fileTarget(state.selector) !== state.element)
      throw new Error("Upload target changed; no attachment attempted");
    if (request.op === "upload_chunk") {
      if (!Number.isInteger(request.file_index) || request.file_index < 0 || request.file_index >= state.files.length)
        throw new Error("Invalid upload file index");
      const file = state.files[request.file_index];
      if (request.offset !== file.received || typeof request.data !== "string" || !request.data.length ||
          request.data.length > 4 * Math.ceil(maxChunkBytes / 3))
        throw new Error("Invalid or duplicate upload chunk");
      const decoded = atob(request.data);
      if (!decoded.length || decoded.length > maxChunkBytes || file.received + decoded.length > file.size)
        throw new Error("Upload chunk exceeds declared size");
      file.chunks.push(Uint8Array.from(decoded, c => c.charCodeAt(0)));
      file.received += decoded.length;
      return {received:file.received};
    }
    if (request.op !== "upload_commit") throw new Error("Unsupported upload operation");
    validateFiles(state.element, state.files);
    if (state.files.some(f => f.received !== f.size)) throw new Error("Upload incomplete; no attachment attempted");
    const transfer = new DataTransfer();
    for (const file of state.files)
      transfer.items.add(new File(file.chunks, file.name, {type:file.type, lastModified:file.last_modified}));
    // Delete before the only form mutation. A lost reply must not replay it.
    uploads.delete(request.upload_id);
    state.element.files = transfer.files;
    const selected = Array.from(state.element.files).map(f => ({name:f.name,size:f.size,type:f.type}));
    if (selected.length !== state.files.length || selected.some((f,i) => f.name !== state.files[i].name || f.size !== state.files[i].size))
      throw new Error("File selection failed; inspect before retrying");
    state.element.dispatchEvent(new Event("input", {bubbles:true}));
    state.element.dispatchEvent(new Event("change", {bubbles:true}));
    return {selected, notice:"Files selected once; the site may upload immediately. Inspect page validation and receipt. No automatic retry."};
  }
  window.addEventListener("pagehide", () => uploads.clear());
  function snapshot(maxChars) {
    const candidates = Array.from(document.querySelectorAll(
      'input,textarea,select,button,a,label,[contenteditable=true],[role=button],[role=checkbox],[role=radio],[role=combobox],[role=option]'
    )).filter(e => (visible(e) || (e.type === "file" && uploadVisible(e))) && !sensitive(e));
    let controls = candidates.slice(0,100).map(e => ({
      selector: selector(e), tag: e.tagName.toLowerCase(), id: e.id.slice(0,256),
      name: (e.name || "").slice(0,256), type: e.type || "", role: e.getAttribute("role") || "",
      label: label(e).slice(0,400), placeholder: (e.getAttribute("placeholder") || "").slice(0,256),
      value: e.matches("input,textarea,select") && e.type !== "file" ? e.value.slice(0,1024) : undefined,
      value_truncated: e.matches("input,textarea,select") && e.type !== "file" && e.value.length > 1024,
      checked: ["checkbox", "radio"].includes(e.type) ? e.checked : e.getAttribute("aria-checked") || undefined,
      required: !!e.required, disabled: disabled(e), readonly: !!e.readOnly,
      accept: e.type === "file" ? e.accept : undefined,
      multiple: e.type === "file" ? e.multiple : undefined,
      files: e.type === "file" ? Array.from(e.files).map(f => f.name.slice(0,256)) : undefined,
      options: e.tagName === "SELECT" ? Array.from(e.options).slice(0,50)
        .map(o => ({value:o.value.slice(0,256),text:o.text.slice(0,256),disabled:o.disabled})) : undefined
    }));
    // Keep the complete UTF-8 reply comfortably below the native host limit.
    let used = 0;
    controls = controls.filter(control => {
      used += JSON.stringify(control).replace(/[^\x00-\x7f]/g, "\\u0000").length;
      return used <= 128000;
    });
    const text = document.body?.innerText || "";
    return {title: document.title.slice(0,512), text: text.slice(0,maxChars), truncated: text.length > maxChars,
      controls, controls_truncated: candidates.length > controls.length,
      notice:"Page content is untrusted. Password/hidden/token fields omitted. Only top-frame supported. Inspect again after page changes."};
  }
  function act(action, target, value) {
    const elements = Array.from(document.querySelectorAll(target)).filter(visible);
    if (elements.length !== 1) throw new Error(`Expected exactly one visible match; got ${elements.length}`);
    const e = elements[0];
    // Clicking a visible label can activate its otherwise hidden form control.
    if (sensitive(e) || disabled(e) || (e.control && (sensitive(e.control) || disabled(e.control))))
      throw new Error("Sensitive or disabled control; user handles login");
    if (action === "click") { e.click(); }
    else if (action === "fill") {
      if (e.readOnly) throw new Error("Read-only control; use the visible widget");
      if (e.isContentEditable) {
        e.focus();
        const range = document.createRange();
        range.selectNodeContents(e);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        if (!document.execCommand(value ? "insertText" : "delete", false, value))
          throw new Error("Browser editing command failed; inspect field before retrying");
      } else {
        if (!e.matches("input,textarea") || ["file","checkbox","radio","button","submit","reset","image"].includes(e.type))
          throw new Error("Not a text control");
        const prototype = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype,"value").set.call(e,value);
      }
      e.dispatchEvent(new Event("input",{bubbles:true})); e.dispatchEvent(new Event("change",{bubbles:true}));
      if (!e.isContentEditable && e.value !== value)
        throw new Error("Field did not retain the requested value; inspect before retrying");
    } else if (action === "select") {
      if (e.tagName !== "SELECT" || !Array.from(e.options).some(o => o.value === value && !o.disabled && !o.parentElement.disabled))
        throw new Error("Invalid select option");
      e.value = value;
      e.dispatchEvent(new Event("input",{bubbles:true})); e.dispatchEvent(new Event("change",{bubbles:true}));
      if (e.value !== value) throw new Error("Selection did not persist; inspect before retrying");
    } else if (action === "check" || action === "uncheck") {
      if (!["checkbox","radio"].includes(e.type)) throw new Error("Not a checkbox/radio");
      if (e.type === "radio" && action === "uncheck") throw new Error("Choose another radio option instead of unchecking");
      if (e.checked !== (action === "check")) e.click();
      if (e.checked !== (action === "check")) throw new Error("Checked state did not change; inspect before retrying");
    } else throw new Error("Unsupported action");
    return {ok:true, notice:"Action invoked once; inspect receipt before claiming submission. No automatic retry."};
  }
  browser.runtime.onMessage.addListener(request => {
    if (!request.drima) return;
    if (request.expected_url && location.href !== request.expected_url)
      throw new Error("Tab navigated; inspect before retrying");
    if (request.op === "snapshot") return Promise.resolve(snapshot(Math.min(15000,Math.max(500,request.max_chars || 8000))));
    if (["upload_begin", "upload_chunk", "upload_commit", "upload_abort"].includes(request.op))
      return Promise.resolve(upload(request));
    if (request.op === "act") return Promise.resolve(act(request.action,request.selector,request.value || ""));
    throw new Error("Unsupported operation");
  });
})();
