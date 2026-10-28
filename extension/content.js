/* Isolated-world listener. No cookies/storage/network or arbitrary evaluation. */
(() => {
  if (globalThis.drimaApprovedTabInstalled) return;
  globalThis.drimaApprovedTabInstalled = true;
  const contexts = new WeakMap();
  const uploads = new Map(); // One bounded staging area across all frame contexts.
  // Firefox shows page objects to content scripts through Xray wrappers.
  // exportFunction lets packaged code stand in for one page method for a
  // bounded moment around an agent action; restore() puts the page back.
  function pageHook(target, name, replacement) {
    if (!target || typeof exportFunction !== "function") return () => {}; // Not a Firefox content script.
    const own = Object.prototype.hasOwnProperty.call(target, name);
    const original = own ? target[name] : undefined;
    exportFunction(replacement, target, {defineAs:name});
    return () => { if (own) target[name] = original; else delete target[name]; };
  }
  const settle = ms => new Promise(resolve => window.setTimeout(resolve, ms));
  function createContext(document, window) {
    const location = window.location;
    const documentId = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16).padStart(8,"0")).join("");
    const ancestors = function*(e) {
      for (let node = e; node; node = node.parentElement || node.getRootNode().host) yield node;
    };
    const ancestorMatches = (e, css) => Array.from(ancestors(e)).some(node => node.matches(css));
    // Short per-document element refs handed out by snapshot ("ref=e12"). They die
    // with this document, so a ref can never reach an element of a later page.
    const refs = new Map(), refOf = new WeakMap();
    const refFor = e => {
      let ref = refOf.get(e);
      if (!ref && refs.size < 20000) { ref = `e${refs.size + 1}`; refOf.set(e,ref); refs.set(ref,e); }
      return ref;
    };
    const query = (css, root = document) => {
      if (typeof css !== "string" || !css || css.length > 8192) throw new Error("Invalid selector");
      const ref = /^ref=(e\d{1,6})$/.exec(css);
      if (ref) {
        const e = refs.get(ref[1]);
        if (!e || !e.isConnected) throw new Error("Unknown or stale ref; take a new snapshot");
        return [e];
      }
      const parts = css.split(" >>> ");
      if (parts.length > 16) throw new Error("Shadow selector depth exceeded");
      for (const part of parts.slice(0,-1)) {
        const hosts = Array.from(root.querySelectorAll(part));
        if (hosts.length !== 1 || !visible(hosts[0]) || !hosts[0].shadowRoot)
          throw new Error("Expected exactly one visible open shadow host");
        root = hosts[0].shadowRoot;
      }
      return Array.from(root.querySelectorAll(parts.at(-1)));
    };
    function deepQuery(root, css) {
      const found = Array.from(root?.querySelectorAll(css) || []);
      for (const host of root?.querySelectorAll("*") || [])
        if (host.shadowRoot) found.push(...deepQuery(host.shadowRoot, css));
      if (root?.shadowRoot) found.push(...deepQuery(root.shadowRoot, css));
      return found;
    }
    const visible = e => !!e.getClientRects().length &&
      !["hidden", "collapse"].includes(window.getComputedStyle(e).visibility) &&
      !ancestorMatches(e, '[inert]');
    // ARIA hides content from assistive technology, not from the screen. Google
    // viewers and other widgets can render an aria-hidden toolbar for mouse use.
    const ariaHidden = e => ancestorMatches(e, '[aria-hidden="true"]');
    const sensitive = e => e.matches('input[type="password"], input[type="hidden"]') ||
      /password|token|secret|verification.?code|one.?time/i.test(
        `${e.name || ""} ${e.id || ""} ${e.getAttribute("autocomplete") || ""}`);
    const disabled = e => e.matches(':disabled,[aria-disabled="true"]') ||
      ancestorMatches(e, '[inert],[aria-disabled="true"]');
    const googleAccountSelector = '[role="link"][data-identifier]';
    const googleAccountRow = e => location.origin === "https://accounts.google.com" &&
      ancestorMatches(e, googleAccountSelector);
    const formField = 'input:not([type="hidden"]),textarea,select,[contenteditable="true"]';
    // Unlabeled form fields: use the text of the nearest container that holds
    // only this field (a typical question wrapper). Display metadata only.
    const containerLabel = e => {
      if (!e.matches(formField)) return "";
      let node = e.parentElement;
      for (let depth = 0; node && depth < 4 && node !== document.body; depth++, node = node.parentElement) {
        if (node.querySelectorAll(formField).length !== 1) return "";
        const text = node.innerText?.trim();
        if (text) return text.slice(0,180);
      }
      return "";
    };
    const label = e => e.getAttribute("aria-label") ||
      (e.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
        .map(id => e.getRootNode().getElementById(id)?.innerText || "").join(" ").trim() ||
      Array.from(e.labels || []).map(l => l.innerText.trim()).join(" ") || e.innerText?.trim().slice(0,180) ||
      containerLabel(e) || "";
    function selector(e) {
      const root = e.getRootNode();
      let local;
      if (e === document.documentElement) local = "html";
      else if (e.id && root.querySelectorAll(`#${CSS.escape(e.id)}`).length === 1)
        local = `#${CSS.escape(e.id)}`;
      else {
        const parts = [];
        for (let node = e; node; node = node.parentElement) {
          const siblings = node.parentElement?.children || root.children;
          const index = Array.from(siblings).indexOf(node) + 1;
          parts.unshift(`${node.localName}:nth-child(${index})`);
        }
        local = parts.join(" > ");
      }
      return root.host ? `${selector(root.host)} >>> ${local}` : local;
    }
    const maxUploadBytes = 25 * 1024 * 1024;
    const maxChunkBytes = 96 * 1024;
    // Drop zones often hide the input inside a visible container. Accept the
    // nearest visible ancestor (3 levels) only when it owns no other file input.
    const dropZoneVisible = e => {
      let node = e.parentElement;
      for (let depth = 0; node && depth < 3 && node !== document.body; depth++, node = node.parentElement) {
        if (node.querySelectorAll('input[type="file"]').length !== 1) return false;
        if (visible(node) && node.innerText?.trim()) return true;
      }
      return false;
    };
    const uploadVisible = e => visible(e) || Array.from(e.labels || []).some(visible) || dropZoneVisible(e);
    function fileTarget(target) {
      const elements = query(target);
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
        // Picker mode: the target is the visible control that opens the site's
        // file chooser (often a file input created only when clicked).
        const element = request.picker === true ? pickerTrigger(request.selector) : fileTarget(request.selector);
        if (request.picker !== true) validateFiles(element, request.files);
        const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16).padStart(8,"0")).join("");
        uploads.set(id, {document, element, selector:request.selector, url:location.href, created:Date.now(),
          picker:request.picker === true,
          files:request.files.map(meta => ({...meta, chunks:[], received:0}))});
        return {upload_id:id, notice:"Staging only; no files attached yet"};
      }
      if (request.op === "upload_abort") {
        const staged = uploads.get(request.upload_id);
        if (staged && staged.document !== document) throw new Error("Upload belongs to another frame document");
        uploads.delete(request.upload_id);
        return {aborted:true};
      }
      const state = uploads.get(request.upload_id);
      if (!state) throw new Error("Upload expired or document changed; inspect before retrying");
      if (state.document !== document || state.url !== location.href || !state.element.isConnected ||
          (state.picker ? pickerTrigger(state.selector) : fileTarget(state.selector)) !== state.element)
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
      if (!state.picker) validateFiles(state.element, state.files);
      if (state.files.some(f => f.received !== f.size)) throw new Error("Upload incomplete; no attachment attempted");
      const transfer = new DataTransfer();
      for (const file of state.files)
        transfer.items.add(new File(file.chunks, file.name, {type:file.type, lastModified:file.last_modified}));
      // Delete before the only form mutation. A lost reply must not replay it.
      uploads.delete(request.upload_id);
      if (state.picker) return attachThroughPicker(state, transfer);
      return attachFiles(state.element, state.files, transfer);
    }
    function attachFiles(input, files, transfer) {
      input.files = transfer.files;
      const selected = Array.from(input.files).map(f => ({name:f.name,size:f.size,type:f.type}));
      if (selected.length !== files.length || selected.some((f,i) => f.name !== files[i].name || f.size !== files[i].size))
        throw new Error("File selection failed; inspect before retrying");
      input.dispatchEvent(new Event("input", {bubbles:true,composed:true}));
      input.dispatchEvent(new Event("change", {bubbles:true,composed:true}));
      return {selected, notice:"Files selected once; the site may upload immediately. Inspect page validation and receipt. No automatic retry."};
    }
    function pickerTrigger(target) {
      const elements = query(target).filter(visible);
      if (elements.length !== 1) throw new Error(`Expected exactly one visible picker control; got ${elements.length}`);
      const e = elements[0];
      if (sensitive(e) || disabled(e)) throw new Error("Sensitive or disabled control");
      return e;
    }
    // Synthetic clicks cannot open a native file chooser. For one requested
    // upload, intercept the site's own request to open one (input.click() or
    // showPicker() on a file input) and attach the staged files to that input
    // instead. Only the input the site asked to open is touched.
    async function attachThroughPicker(state, transfer) {
      let captured = null;
      const take = input => { if (!captured && input && String(input.type).toLowerCase() === "file") { captured = input; return true; } return false; };
      const proto = window.wrappedJSObject?.HTMLInputElement.prototype;
      const restore = [
        pageHook(proto, "click", function () { if (!take(this)) HTMLElement.prototype.click.call(this); }),
        pageHook(proto, "showPicker", function () { if (!take(this)) HTMLInputElement.prototype.showPicker.call(this); })];
      try {
        click(state.element);
        for (let waited = 0; !captured && waited < 2000; waited += 50) await settle(50);
      } finally { restore.forEach(undo => undo()); }
      if (!captured) throw new Error("The control did not ask for a file chooser within 2 seconds; nothing attached.");
      validateFiles(captured, state.files);
      if (captured.disabled || captured.webkitdirectory) throw new Error("The requested chooser is disabled or a folder picker; nothing attached.");
      return {...attachFiles(captured, state.files, transfer), via_picker:true};
    }
    window.addEventListener("pagehide", () => uploads.clear());
    function click(e) {
      const url = location.href;
      const ensureTarget = () => {
        if (window.document !== document || location.href !== url || !e.isConnected || !visible(e) || disabled(e) ||
            sensitive(e) || (e.control && (sensitive(e.control) || disabled(e.control))))
          throw new Error("Click target changed during pointer events; inspect before retrying");
      };
      e.scrollIntoView({block:"nearest", inline:"nearest"});
      const rect = e.getBoundingClientRect();
      const mouse = {bubbles:true, cancelable:true, composed:true, view:window,
        button:0, clientX:rect.left + rect.width / 2, clientY:rect.top + rect.height / 2};
      const pointer = {...mouse, pointerId:1, pointerType:"mouse", isPrimary:true};
      // .click() alone misses mousedown-driven widgets (e.g. React Select).
      // Respect cancellation of pointerdown (suppresses compatibility mouse events)
      // and mousedown (suppresses our focus default), without replaying activation.
      const compatibilityMouse = e.dispatchEvent(new PointerEvent("pointerdown", {...pointer, buttons:1}));
      ensureTarget();
      if (compatibilityMouse) {
        const focusDefault = e.dispatchEvent(new MouseEvent("mousedown", {...mouse, buttons:1}));
        ensureTarget();
        if (focusDefault) {
          const focusable = e.closest('button,input,select,textarea,a[href],[tabindex],[contenteditable="true"]');
          focusable?.focus({preventScroll:true});
          ensureTarget();
        }
      }
      e.dispatchEvent(new PointerEvent("pointerup", {...pointer, buttons:0}));
      ensureTarget();
      if (compatibilityMouse) {
        e.dispatchEvent(new MouseEvent("mouseup", {...mouse, buttons:0}));
        ensureTarget();
      }
      e.click(); // Exactly one activation, including labels/checkboxes/submit buttons.
    }
    // Pages opened by a synthetic click (window.open, target=_blank links) are
    // blocked by Firefox. While one agent click settles, hand such http(s)
    // addresses to the extension, which opens them in the agent window. The
    // full address goes only to the extension; the client sees the origin.
    async function clickRoutingNewWindows(e) {
      const routed = [];
      const route = url => {
        let parsed;
        try { parsed = new URL(String(url ?? ""), location.href); } catch (_) { return; }
        if (!/^https?:$/.test(parsed.protocol) || routed.length >= 3) return;
        routed.push(browser.runtime.sendMessage({drima_route_open:parsed.href})
          .then(r => ({tab_id:r.tab_id, origin:parsed.origin}), error => ({origin:parsed.origin, error:error.message})));
      };
      const onClick = event => { // Registered last: runs after the page's own handlers.
        if (event.defaultPrevented) return;
        const link = event.target?.closest?.("a[href]");
        const target = (link?.target || "").toLowerCase();
        if (link && target && !["_self","_top","_parent"].includes(target) && /^https?:$/.test(link.protocol)) {
          event.preventDefault(); route(link.href);
        }
      };
      window.addEventListener("click", onClick);
      const restore = pageHook(window.wrappedJSObject, "open", function (url) { route(url); return null; });
      try { click(e); }
      finally { window.removeEventListener("click", onClick); }
      await settle(400); // Sites often open the new page right after their handler.
      restore();
      const opened = await Promise.all(routed);
      return {ok:true, ...(opened.length ? {opened} : {}),
        notice:"Action invoked once; inspect receipt before claiming submission. No automatic retry." +
          (opened.length ? " New pages went to the agent window." : "")};
    }
    function snapshot(maxChars, request = {}) {
      const offset = request.offset ?? 0, limit = request.limit ?? 100;
      if (!Number.isInteger(offset) || offset < 0 || offset > 100000 ||
          !Number.isInteger(limit) || limit < 1 || limit > 100)
        throw new Error("Invalid snapshot offset or limit");
      let root = document.body, scopeReason = "document";
      if (request.scope) {
        const matches = query(request.scope).filter(visible);
        if (matches.length !== 1) throw new Error(`Expected exactly one visible scope; got ${matches.length}`);
        root = matches[0]; scopeReason = "explicit";
      } else {
        const dialogs = deepQuery(document, '[role="dialog"],[aria-modal="true"],dialog[open]')
          .filter(visible);
        // Prefer the innermost dialog containing focus; otherwise the last visible
        // dialog, as used by viewers appended over a long inbox or document.
        const focused = dialogs.filter(e => e.contains(document.activeElement));
        if (dialogs.length) { root = focused.at(-1) || dialogs.at(-1); scopeReason = "dialog"; }
      }
      const controlSelector =
        'input,textarea,select,button,a,label,[contenteditable=true],[role=button],[role=checkbox],[role=radio],[role=combobox],[role=option]' +
        (request.google_account_selection === true && location.origin === "https://accounts.google.com" ?
          ',' + googleAccountSelector : '');
      // Custom dropdowns (Chosen, BambooHR, many React selects) render options as
      // plain leaf items inside a list/popup container. Expose them as role "option".
      const optionContainer = '[role=listbox],[role=menu],ul[class*="result" i],ul[class*="option" i],' +
        '[class*="dropdown" i],[class*="menu" i],[class*="suggest" i],[class*="autocomplete" i],[class*="options" i]';
      const optionLike = e => !e.matches(controlSelector) && e.matches('li,[class*="option" i],[class*="result" i]') &&
        !e.querySelector(controlSelector + ',li') && (e.innerText || "").trim().length > 0 &&
        (e.innerText || "").trim().length <= 200 && !!e.parentElement?.closest(optionContainer);
      const optionCandidates = deepQuery(root, 'li,[class*="option" i],[class*="result" i]').filter(optionLike);
      const candidates = [ ...(root?.matches(controlSelector) ? [root] : []),
        ...deepQuery(root, controlSelector), ...optionCandidates
      ].filter(e => (visible(e) || (e.type === "file" && uploadVisible(e))) && !sensitive(e) &&
        (!googleAccountRow(e) || request.google_account_selection === true));
      const compact = request.compact === true;
      const page = candidates.slice(offset,offset+limit).map(e => ({
        ref: refFor(e) ? `ref=${refFor(e)}` : undefined,
        selector: compact && refFor(e) && !/^#[^ >]{1,80}$/.test(selector(e)) ? undefined : selector(e),
        tag: e.tagName.toLowerCase(), id: e.id.slice(0,256),
        name: (e.name || "").slice(0,256), type: e.type || "",
        role: e.getAttribute("role") || (optionCandidates.includes(e) ? "option" : ""),
        label: label(e).slice(0,400), placeholder: (e.getAttribute("placeholder") || "").slice(0,256),
        value: e.matches("input,textarea,select") && e.type !== "file" ? e.value.slice(0,1024) : undefined,
        value_truncated: e.matches("input,textarea,select") && e.type !== "file" && e.value.length > 1024,
        checked: ["checkbox", "radio"].includes(e.type) ? e.checked : e.getAttribute("aria-checked") || undefined,
        required: !!e.required || e.getAttribute("aria-required") === "true", disabled: disabled(e), readonly: !!e.readOnly,
        expanded: e.hasAttribute("aria-expanded") ? e.getAttribute("aria-expanded") === "true" : undefined,
        selected: e.hasAttribute("aria-selected") ? e.getAttribute("aria-selected") === "true" : undefined,
        aria_hidden: ariaHidden(e),
        aria_controls: e.getAttribute("aria-controls")?.slice(0,256),
        // Link target without query/fragment, so signed or tokenized URLs never leak.
        href: e.tagName === "A" && /^https?:$/.test(e.protocol || "") ? (e.origin + e.pathname).slice(0,512) : undefined,
        accept: e.type === "file" ? e.accept : undefined,
        multiple: e.type === "file" ? e.multiple : undefined,
        files: e.type === "file" ? Array.from(e.files).map(f => f.name.slice(0,256)) : undefined,
        options: e.tagName === "SELECT" ? Array.from(e.options).slice(0,50)
          .map(o => ({value:o.value.slice(0,256),text:o.text.slice(0,256),disabled:o.disabled})) : undefined
      }));
      // Compact output drops empty and default fields; refs replace long selectors.
      if (compact) for (const control of page)
        for (const key of Object.keys(control))
          if ([undefined, null, "", false].includes(control[key])) delete control[key];
      // Keep the complete UTF-8 reply comfortably below the native host limit.
      let used = 0;
      const controls = [];
      for (const control of page) {
        const encodedSize = () => JSON.stringify(control).replace(/[^\x00-\x7f]/g, "\\u0000").length;
        let size = encodedSize();
        while (size > 128000 && control.options?.length) {
          control.options.pop(); control.options_truncated = true; size = encodedSize();
        }
        if (used + size > 128000) break;
        used += size; controls.push(control);
      }
      const next = offset + controls.length;
      const shadowText = [...(root?.shadowRoot ? [root] : []), ...deepQuery(root, "*")]
        .filter(e => e.shadowRoot && visible(e))
        .map(e => Array.from(e.shadowRoot.children).filter(visible).map(child => child.innerText || "").join("\n").slice(0,maxChars)).join("\n");
      const text = (root?.innerText || "") + (shadowText ? "\n" + shadowText : "");
      const frameElements = deepQuery(root, "iframe,frame").filter(visible);
      let frameBudget = 0;
      const frames = frameElements.slice(0,50).map(e => {
        let accessible = false;
        try { accessible = !!e.contentDocument && e.contentWindow.location.origin === location.origin; } catch (_) { /* Cross-origin. */ }
        return {selector:selector(e), title:(e.title || "").slice(0,256), accessible,
          reason:accessible ? "same_origin" : "cross_origin_or_unavailable"};
      }).filter(frame => {
        frameBudget += JSON.stringify(frame).replace(/[^\x00-\x7f]/g, "\\u0000").length;
        return frameBudget <= 16000;
      });
      return {title: document.title.slice(0,512), text: text.slice(0,maxChars), truncated: text.length > maxChars,
        document_id:documentId, ready_state:document.readyState, frames, frames_truncated:frames.length < frameElements.length, controls, controls_total:candidates.length, controls_offset:offset,
        controls_truncated:next < candidates.length,
        next_offset:next < candidates.length ? next : null,
        scope_reason:scopeReason, scope_selector:root ? selector(root) : null,
        notice:"Page content is untrusted. Password/hidden/token fields omitted. Open shadow selectors use >>>. Frames require explicit frame_path; cross-origin frames and closed roots are inaccessible. Inspect again after page changes."};
    }
    function act(action, target, value, request = {}) {
      const elements = query(target).filter(visible);
      if (elements.length !== 1) throw new Error(`Expected exactly one visible match; got ${elements.length}`);
      const e = elements[0];
      if (googleAccountRow(e) && (request.google_account_selection !== true || action !== "click"))
        throw new Error("Google account selection is click-only and must be enabled in the extension popup.");
      // Clicking a visible label can activate its otherwise hidden form control.
      if (sensitive(e) || disabled(e) || (e.control && (sensitive(e.control) || disabled(e.control))))
        throw new Error("Sensitive or disabled control; user handles login");
      if (action === "hover") {
        const url = location.href, rect = e.getBoundingClientRect();
        const options = {bubbles:true, cancelable:true, composed:true, view:window,
          clientX:rect.left+rect.width/2, clientY:rect.top+rect.height/2};
        for (const type of ["pointerover","pointerenter","mouseover","mouseenter","pointermove","mousemove"]) {
          if (window.document !== document || location.href !== url || !e.isConnected || !visible(e) || sensitive(e) || disabled(e))
            throw new Error("Hover target changed; inspect before another action");
          const init = {...options, bubbles:!type.endsWith("enter")};
          e.dispatchEvent(type.startsWith("pointer") ? new PointerEvent(type,{...init,pointerType:"mouse"}) :
            new MouseEvent(type,init));
        }
      }
      else if (action === "click") return clickRoutingNewWindows(e);
      else if (action === "fill") {
        if (e.readOnly) throw new Error("Read-only control; use the visible widget");
        if (e.isContentEditable) {
          e.focus();
          const range = document.createRange();
          range.selectNodeContents(e);
          const selection = window.getSelection();
          selection.removeAllRanges(); selection.addRange(range);
          // Lexical/ProseMirror-style editors read the DOM selection from the
          // asynchronous selectionchange event, and Firefox fires no beforeinput
          // for execCommand. Once the selection is known, offer the editor one
          // cancelable beforeinput; only if unhandled, edit natively (which fires
          // the input event such editors also observe).
          const url = location.href;
          const settle = () => new Promise(resolve => window.setTimeout(resolve, 50));
          const ensureEditor = () => {
            if (window.document !== document || location.href !== url || !e.isConnected || !visible(e) || disabled(e))
              throw new Error("Editor changed during text insertion; inspect before retrying");
          };
          return settle().then(() => {
            ensureEditor();
            const inputType = value ? "insertText" : "deleteContentBackward";
            const before = new InputEvent("beforeinput", {inputType, data:value || null,
              bubbles:true, cancelable:true, composed:true});
            if (e.dispatchEvent(before) && !document.execCommand(value ? "insertText" : "delete", false, value))
              throw new Error("Browser editing command failed; inspect field before retrying");
            return settle();
          }).then(() => {
            ensureEditor();
            const normalize = text => text.replace(/\s+/g, " ").trim();
            if (normalize(e.innerText) !== normalize(value))
              throw new Error("Editor did not retain the requested text; inspect before retrying");
            return {ok:true, notice:"Action invoked once; inspect receipt before claiming submission. No automatic retry."};
          });
        } else {
          if (!e.matches("input,textarea") || ["file","checkbox","radio","button","submit","reset","image"].includes(e.type))
            throw new Error("Not a text control");
          const prototype = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(prototype,"value").set.call(e,value);
        }
        e.dispatchEvent(new Event("input",{bubbles:true,composed:true})); e.dispatchEvent(new Event("change",{bubbles:true,composed:true}));
        if (!e.isContentEditable && e.value !== value)
          throw new Error("Field did not retain the requested value; inspect before retrying");
      } else if (action === "select") {
        if (e.tagName !== "SELECT" || !Array.from(e.options).some(o => o.value === value && !o.disabled && !o.parentElement.disabled))
          throw new Error("Invalid select option");
        e.value = value;
        e.dispatchEvent(new Event("input",{bubbles:true,composed:true})); e.dispatchEvent(new Event("change",{bubbles:true,composed:true}));
        if (e.value !== value) throw new Error("Selection did not persist; inspect before retrying");
      } else if (action === "check" || action === "uncheck") {
        if (!["checkbox","radio"].includes(e.type)) throw new Error("Not a checkbox/radio");
        if (e.type === "radio" && action === "uncheck") throw new Error("Choose another radio option instead of unchecking");
        if (e.checked !== (action === "check")) e.click();
        if (e.checked !== (action === "check")) throw new Error("Checked state did not change; inspect before retrying");
      } else if (action === "press") {
        // Synthetic keys reach page handlers (filters, comboboxes, dialogs) but
        // trigger no native default such as form submission or focus movement.
        const codes = {Enter:13, Escape:27, ArrowDown:40, ArrowUp:38, ArrowLeft:37, ArrowRight:39,
          Backspace:8, Delete:46, Home:36, End:35, PageUp:33, PageDown:34, " ":32};
        if (!Object.hasOwn(codes, value)) throw new Error("Unsupported key; use Enter, Escape, arrows, Backspace, Delete, Home, End, PageUp, PageDown or space");
        const url = location.href;
        const init = {key:value, code:value === " " ? "Space" : value, keyCode:codes[value], which:codes[value],
          bubbles:true, cancelable:true, composed:true, view:window};
        e.focus({preventScroll:true});
        const proceed = e.dispatchEvent(new KeyboardEvent("keydown", init));
        if (window.document !== document || location.href !== url) return {ok:true, notice:"Key pressed once; the page changed. Inspect before another action."};
        if (proceed && (value === "Enter" || value === " "))
          e.dispatchEvent(new KeyboardEvent("keypress", {...init, charCode:codes[value]}));
        if (e.isConnected) e.dispatchEvent(new KeyboardEvent("keyup", init));
      } else if (action === "type") {
        // Append text key by key for widgets that filter as you type; fill replaces.
        if (!e.matches("input,textarea") || ["file","checkbox","radio","button","submit","reset","image","range","color"].includes(e.type))
          throw new Error("Not a text control");
        if (e.readOnly) throw new Error("Read-only control; use the visible widget");
        if (!value || value.length > 500) throw new Error("Type 1-500 characters");
        const url = location.href, start = e.value;
        const prototype = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype,"value").set;
        e.focus({preventScroll:true});
        for (const ch of value) {
          if (window.document !== document || location.href !== url || !e.isConnected || disabled(e))
            throw new Error("Field changed while typing; inspect before retrying");
          const init = {key:ch, bubbles:true, cancelable:true, composed:true, view:window};
          if (e.dispatchEvent(new KeyboardEvent("keydown", init))) {
            e.dispatchEvent(new KeyboardEvent("keypress", {...init, charCode:ch.codePointAt(0)}));
            setter.call(e, e.value + ch);
            e.dispatchEvent(new InputEvent("input", {inputType:"insertText", data:ch, bubbles:true, composed:true}));
          }
          e.dispatchEvent(new KeyboardEvent("keyup", init));
        }
        if (e.value !== start + value) return {ok:true, value:e.value.slice(0,1024),
          notice:"Typed once; the page altered the text. Inspect before another action."};
      } else throw new Error("Unsupported action");
      return {ok:true, notice:"Action invoked once; inspect receipt before claiming submission. No automatic retry."};
    }
    async function wait(request) {
      const timeout = request.timeout_ms ?? 5000;
      if (!Number.isInteger(timeout) || timeout < 0 || timeout > 10000 ||
          !["visible","hidden","attached","detached"].includes(request.state || "visible"))
        throw new Error("Invalid wait state or timeout (0..10000 ms)");
      const state = request.state || "visible", started = Date.now(), url = location.href;
      while (true) {
        if (location.href !== url || !document.documentElement?.isConnected || window.document !== document)
          throw new Error("Document changed during wait; inspect again");
        const matches = query(request.selector);
        if (matches.length > 1) throw new Error(`Expected at most one wait target; got ${matches.length}`);
        const shown = matches.length === 1 && visible(matches[0]);
        const matched = state === "attached" ? matches.length === 1 : state === "detached" ? !matches.length :
          state === "visible" ? shown : !shown;
        if (matched || Date.now() - started >= timeout)
          return {matched, state, elapsed_ms:Date.now()-started, document_id:documentId,
            notice:"Read-only observation; no action invoked or retried."};
        await new Promise(resolve => window.setTimeout(resolve,100));
      }
    }
    return request => {
      if (request.document_id && request.document_id !== documentId)
        throw new Error("Document changed since inspection; no action sent. Inspect again.");
      if (request.op === "snapshot") return Promise.resolve(snapshot(Math.min(15000,Math.max(500,request.max_chars || 8000)),request));
      if (request.op === "wait") return wait(request);
      if (["upload_begin", "upload_chunk", "upload_commit", "upload_abort"].includes(request.op))
        return Promise.resolve(upload(request));
      if (request.op === "act") return Promise.resolve(act(request.action,request.selector,request.value || "",request));
      throw new Error("Unsupported operation");
    };
  }
  // Visible tab-strip marker for a flagged tab. Keeps the prefix when the site
  // rewrites its own title; removing it restores the site's current title.
  const titleMarker = {prefix:"", observer:null};
  function applyTitleMarker() {
    if (titleMarker.prefix && !document.title.startsWith(titleMarker.prefix))
      document.title = titleMarker.prefix + document.title;
  }
  function markAttention(reason) {
    unmarkAttention();
    const text = String(reason || "").replace(/[\x00-\x1f\x7f]/g, " ").trim().slice(0,80);
    titleMarker.prefix = `\u{1F534} ${text} \u2013 `;
    applyTitleMarker();
    titleMarker.observer = new MutationObserver(applyTitleMarker);
    titleMarker.observer.observe(document.head || document.documentElement, {subtree:true, childList:true, characterData:true});
    return {marked:true};
  }
  function unmarkAttention() {
    titleMarker.observer?.disconnect();
    if (titleMarker.prefix && document.title.startsWith(titleMarker.prefix))
      document.title = document.title.slice(titleMarker.prefix.length);
    titleMarker.prefix = ""; titleMarker.observer = null;
    return {marked:false};
  }
  // Explicit same-origin descent only. The top tab's grant never authorizes a
  // cross-origin embedded site. Cache by Document so reload invalidates staged state.
  browser.runtime.onMessage.addListener(request => {
    if (!request.drima) return;
    if (request.expected_url && location.href !== request.expected_url)
      throw new Error("Tab navigated; inspect before retrying");
    if (request.op === "content_status") return Promise.resolve({content_version:"0.3.2"});
    if (request.op === "attention_mark" || request.op === "attention_unmark") {
      if (window !== window.top) return;
      return Promise.resolve(request.op === "attention_mark" ? markAttention(request.reason) : unmarkAttention());
    }
    const path = request.frame_path || [];
    if (!Array.isArray(path) || path.length > 8 || path.some(s => typeof s !== "string"))
      throw new Error("Invalid frame_path (maximum 8 observed selectors)");
    let doc = document;
    for (const css of path) {
      // Use the same packaged selector resolver without exposing executable code.
      let root = doc;
      const parts = css.split(" >>> ");
      if (!css || css.length > 8192 || parts.length > 16) throw new Error("Invalid frame selector");
      for (const part of parts.slice(0,-1)) {
        const hosts = root.querySelectorAll(part);
        if (hosts.length !== 1 || !hosts[0].shadowRoot) throw new Error("Ambiguous or unavailable shadow host");
        root = hosts[0].shadowRoot;
      }
      const matches = root.querySelectorAll(parts.at(-1));
      if (matches.length !== 1 || !matches[0].matches("iframe,frame")) throw new Error("Expected exactly one frame");
      const frame = matches[0];
      for (let e = frame; e; e = e.parentElement || e.getRootNode().host)
        if (!e.getClientRects().length || ["hidden","collapse"].includes(doc.defaultView.getComputedStyle(e).visibility) || e.matches("[inert]"))
          throw new Error("Frame is hidden or inert");
      try {
        if (!frame.contentDocument || frame.contentWindow.location.origin !== location.origin) throw new Error();
        doc = frame.contentDocument;
      } catch (_) { throw new Error("Cross-origin or unavailable frame; no embedded-site access granted"); }
    }
    if (!contexts.has(doc)) contexts.set(doc,createContext(doc,doc.defaultView));
    return contexts.get(doc)(request);
  });
})();
