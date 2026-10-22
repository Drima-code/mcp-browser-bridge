/* No cookies, webRequest, debugger or arbitrary evaluation permission. */
const approved = new Map();
const allSites = {origins: ["http://*/*", "https://*/*"]};
let port = null;
let mode = "manual";
function eligible(tab) {
  try { return !tab.incognito && ["http:", "https:"].includes(new URL(tab.url).protocol); }
  catch (_) { return false; }
}
function origin(url) { return new URL(url).origin; }
function safeUrl(url) { const u = new URL(url); return u.origin + u.pathname; }
const PROTOCOL_VERSION = 1;
const CONTENT_VERSION = "0.2.5";
const capabilities = ["act", "hover", "snapshot_paging", "upload", "download_watch"];
const watches = new Map(), pendingDownloads = new Map();
const watchLifetime = 5 * 60 * 1000;
let downloadListeners = false;
function trackingError() { for (const watch of watches.values()) watch.trackingError = true; }
function downloadCreated(item) { observeDownload(item).catch(trackingError); }
function downloadUpdated(delta) { downloadChanged(delta).catch(trackingError); }
function clearDownloads() { watches.clear(); pendingDownloads.clear(); }
function clearTabDownloads(tabId) {
  for (const [id,watch] of watches) if (watch.tabId === tabId) watches.delete(id);
  for (const [id,pending] of pendingDownloads) if (!watches.has(pending.watchId)) pendingDownloads.delete(id);
}
function recordCount(set,id,watch) {
  if (set.size < 32) set.add(id); else watch.overflow = true;
}
function expireDownloads() {
  const now = Date.now();
  for (const [id, watch] of watches) if (now - watch.started > watchLifetime * 2) watches.delete(id);
  for (const [id, pending] of pendingDownloads)
    if (now - pending.received > watchLifetime) pendingDownloads.delete(id);
}
async function watchAllowed(watch) {
  const tab = await browser.tabs.get(watch.tabId).catch(() => null);
  return !!tab && tab.url === watch.url && await allowed(tab);
}
function basename(path) { return (path || "").split(/[\\/]/).pop(); }
function filenameMatches(actual, expected) {
  actual = basename(actual);
  if (actual === expected) return true;
  const dot = expected.lastIndexOf('.');
  const stem = dot > 0 ? expected.slice(0,dot) : expected, ext = dot > 0 ? expected.slice(dot) : "";
  if (!actual.startsWith(stem + " (") || !actual.endsWith(")" + ext)) return false;
  return /^\d+$/.test(actual.slice(stem.length+2,actual.length-ext.length-1));
}
async function observeDownload(item, received = Date.now()) {
  expireDownloads();
  if (!item || item.incognito || !Number.isInteger(item.id)) return;
  if (!await browser.permissions.contains({permissions:["downloads"]})) { clearDownloads(); return; }
  const started = Date.parse(item.startTime);
  if (!Number.isFinite(started)) return;
  let source;
  try { source = origin(item.referrer); } catch (_) { source = null; }
  const candidates = [];
  for (const [id, watch] of watches) {
    if (watch.stopped || received - watch.started > watchLifetime || started < watch.started) continue;
    if (!await watchAllowed(watch)) { watches.delete(id); continue; }
    if (item.filename && !filenameMatches(item.filename,watch.expected)) continue;
    if (!source) {
      // No provenance: retain only a count, never a path or unrelated metadata.
      if (item.filename && filenameMatches(item.filename,watch.expected)) recordCount(watch.unattributed,item.id,watch);
      continue;
    }
    if (source === watch.origin) candidates.push([id,watch]);
  }
  if (candidates.length !== 1) {
    for (const [,watch] of candidates) recordCount(watch.ambiguous,item.id,watch);
    pendingDownloads.delete(item.id); return;
  }
  const [watchId, watch] = candidates[0];
  if (!await browser.permissions.contains({permissions:["downloads"]}) || !await watchAllowed(watch)) return;
  if (!item.filename) {
    if (pendingDownloads.size < 32) pendingDownloads.set(item.id,{watchId,received});
    return;
  }
  if (watch.records.size >= 10 && !watch.records.has(item.id)) { watch.overflow = true; return; }
  watch.records.set(item.id,downloadRecord(item));
  if (item.state === "in_progress") pendingDownloads.set(item.id,{watchId,received});
  else pendingDownloads.delete(item.id);
}
function downloadRecord(item) {
  const complete = item.state === "complete";
  const pathOmitted = complete && item.filename.length > 2048;
  return {id:item.id, filename:basename(item.filename).slice(0,255), state:item.state,
    bytes_received:item.bytesReceived, total_bytes:item.totalBytes, exists:item.exists,
    saved_path:complete && item.exists !== false && !pathOmitted ? item.filename : null,
    path_omitted:pathOmitted,
    error:item.error ? String(item.error).slice(0,256) : null,
    correlation:"origin_filename_time_candidate"};
}
async function downloadChanged(delta) {
  expireDownloads();
  const pending = pendingDownloads.get(delta.id);
  if (!pending) return;
  const watch = watches.get(pending.watchId);
  if (!watch || !await watchAllowed(watch) || !await browser.permissions.contains({permissions:["downloads"]})) {
    pendingDownloads.delete(delta.id); watches.delete(pending.watchId); return;
  }
  const [item] = await browser.downloads.search({id:delta.id});
  if (item) await observeDownload(item,pending.received);
}
async function installDownloadListeners() {
  if (downloadListeners || !browser.downloads || !await browser.permissions.contains({permissions:["downloads"]})) return;
  // Event observation only: never enumerate global history or start a transfer.
  browser.downloads.onCreated.addListener(downloadCreated);
  browser.downloads.onChanged.addListener(downloadUpdated);
  downloadListeners = true;
}
async function downloadOperation(request, tab) {
  expireDownloads();
  if (!await browser.permissions.contains({permissions:["downloads"]})) {
    clearDownloads(); throw new Error("Enable download tracking in the addon popup and accept Firefox's permission prompt.");
  }
  await installDownloadListeners();
  if (!downloadListeners) throw new Error("Firefox download events are unavailable; inspect the addon permission and reconnect.");
  if (request.action === "watch") {
    const expected = request.expected_filename;
    if (typeof expected !== "string" || !expected || expected.length > 255 || /[/\\\x00-\x1f]/.test(expected))
      throw new Error("Provide the expected attachment basename, without directories.");
    if (watches.size >= 8) throw new Error("Download watch limit reached; stop an old watch or wait for expiry.");
    const id = crypto.randomUUID();
    watches.set(id,{tabId:tab.id,url:tab.url,origin:origin(tab.url),expected,started:Date.now(),
      records:new Map(),ambiguous:new Set(),unattributed:new Set(),stopped:false,overflow:false,trackingError:false});
    return {watch_id:id,state:"waiting",expires_in_seconds:300,
      notice:"Armed for future candidates only; no download started. Firefox provides no source tab ID."};
  }
  const watch = watches.get(request.watch_id);
  if (!watch || watch.tabId !== tab.id) throw new Error("Unknown or expired download watch for this tab.");
  if (!await watchAllowed(watch)) { watches.delete(request.watch_id); throw new Error("Tab changed or access revoked; watch cleared."); }
  if (request.action === "stop") {
    watches.delete(request.watch_id);
    for (const [id,pending] of pendingDownloads) if (pending.watchId === request.watch_id) pendingDownloads.delete(id);
    return {stopped:true,notice:"Observation stopped; no download was cancelled."};
  }
  if (request.action !== "list") throw new Error("Unsupported download watch action");
  // Refresh only IDs captured after arming this watch; never query past history.
  for (const [id] of watch.records) {
    const [item] = await browser.downloads.search({id});
    if (item && !item.incognito && filenameMatches(item.filename,watch.expected)) watch.records.set(id,downloadRecord(item));
  }
  if (!await browser.permissions.contains({permissions:["downloads"]}) || !await watchAllowed(watch)) {
    clearTabDownloads(tab.id); throw new Error("Download permission or website access revoked; watch cleared.");
  }
  return {watch_id:request.watch_id,expected_filename:watch.expected,
    state:Date.now()-watch.started > watchLifetime ? "expired" : watch.records.size ? "observed" : "waiting",
    records:Array.from(watch.records.values()),ambiguous_events:watch.ambiguous.size,
    unattributed_events:watch.unattributed.size,overflow:watch.overflow,tracking_error:watch.trackingError,
    notice:"Candidates match origin, filename and time; source-tab identity is not proven. No event does not prove a failed click."};
}
async function status(tab) {
  await installDownloadListeners();
  const result = {addon_version:browser.runtime.getManifest().version,protocol_version:PROTOCOL_VERSION,
    capabilities,downloads_permission:await browser.permissions.contains({permissions:["downloads"]}),
    downloads_available:downloadListeners};
  if (tab) result.content_version = await currentContent(tab);
  return result;
}
async function currentContent(tab) {
  await browser.tabs.executeScript(tab.id,{file:"content.js"});
  const current = await browser.tabs.get(tab.id);
  if (current.url !== tab.url || !await allowed(current)) throw new Error("Tab changed; inspect before retrying.");
  let reply;
  try { reply = await browser.tabs.sendMessage(tab.id,{drima:true,op:"content_status",expected_url:tab.url}); }
  catch (_) { throw new Error("Website content listener is outdated or unavailable; reload this website tab. No site action sent."); }
  if (reply?.content_version !== CONTENT_VERSION)
    throw new Error("Website content listener version mismatch; reload this website tab. No site action sent.");
  return reply.content_version;
}
async function refreshBadge() {
  await browser.browserAction.setBadgeText({text: mode === "all" ? "ALL" : ""});
  for (const id of approved.keys())
    await browser.browserAction.setBadgeText({tabId: id, text: "ON"});
}
async function allAccess() {
  return mode === "all" && await browser.permissions.contains(allSites);
}
async function allowed(tab) {
  return eligible(tab) && (await allAccess() || approved.get(tab.id) === origin(tab.url));
}
async function dispatch(request) {
  await ready;
  if (request.op === "status" && request.tab_id == null) return status();
  if (request.op === "tabs") {
    const candidates = await allAccess() ? await browser.tabs.query({}) :
      await Promise.all(Array.from(approved.keys(), id => browser.tabs.get(id).catch(() => null)));
    const tabs = [];
    for (const tab of candidates) {
      if (tab && await allowed(tab))
        tabs.push({tab_id: String(tab.id), title: tab.title, url: safeUrl(tab.url)});
    }
    return {mode, tabs};
  }
  const id = Number(request.tab_id);
  const tab = await browser.tabs.get(id);
  if (!await allowed(tab)) throw new Error("Tab not approved. Use the extension popup to grant access.");
  if (request.op === "status") return status(tab);
  if (request.op === "downloads") return downloadOperation(request,tab);
  if (!["snapshot", "act", "upload_begin", "upload_chunk", "upload_commit", "upload_abort"].includes(request.op)) throw new Error("Unsupported operation");
  await currentContent(tab);
  // Content code is packaged and fixed; requests are data, never executable JS.
  await browser.tabs.executeScript(id, {file: "content.js"});
  const current = await browser.tabs.get(id);
  if (current.url !== tab.url || !await allowed(current)) throw new Error("Tab changed; inspect before retrying.");
  return browser.tabs.sendMessage(id, {...request, drima: true, expected_url: current.url});
}
function connect() {
  if (port) return;
  const connection = browser.runtime.connectNative("drima_regular_browser");
  port = connection;
  connection.onMessage.addListener(async request => {
    try { connection.postMessage({id: request.id, result: await dispatch(request)}); }
    catch (error) { try { connection.postMessage({id: request.id, error: error.message}); } catch (_) {} }
  });
  connection.onDisconnect.addListener(() => {
    if (port === connection) port = null;
    browser.browserAction.setBadgeText({text: "ERR"});
    for (const id of approved.keys()) browser.browserAction.setBadgeText({tabId: id, text: "ERR"});
  });
}
async function manualMode() {
  clearDownloads();
  mode = "manual"; // Stop broad access before asynchronous cleanup.
  for (const id of approved.keys()) await browser.browserAction.setBadgeText({tabId: id, text: ""});
  approved.clear();
  await browser.storage.local.set({mode});
  await refreshBadge();
}
const ready = (async () => {
  const settings = await browser.storage.local.get("mode");
  if (settings.mode === "all" && await browser.permissions.contains(allSites)) {
    mode = "all";
    connect();
  }
  await refreshBadge();
})();
browser.runtime.onMessage.addListener(async (request, sender) => {
  // Only packaged UI changes access settings; never native MCP/page requests.
  if (sender.id !== browser.runtime.id || sender.url !== browser.runtime.getURL("popup.html"))
    throw new Error("Access settings may only be changed in the extension popup.");
  await ready;
  if (request.ui === "state") {
    const [tab] = await browser.tabs.query({active: true, currentWindow: true});
    return {mode, connected: !!port, downloadsPermission:await browser.permissions.contains({permissions:["downloads"]}),
      addonVersion:browser.runtime.getManifest().version, eligible: !!tab && eligible(tab),
      approved: !!tab && eligible(tab) && approved.get(tab.id) === origin(tab.url), tabId: tab?.id};
  }
  if (request.ui === "reconnect") {
    connect();
    await refreshBadge();
    return {ok: true};
  }
  if (request.ui === "downloads") {
    if (!await browser.permissions.contains({permissions:["downloads"]}))
      throw new Error("Firefox download permission is required; enable it with the popup button.");
    await installDownloadListeners(); return {ok:true};
  }
  if (request.ui === "manual") { await manualMode(); return {ok: true}; }
  if (request.ui === "all") {
    if (request.confirmed !== true || !await browser.permissions.contains(allSites))
      throw new Error("Explicit consent and Firefox website permission are required.");
    await manualMode();
    mode = "all";
    await browser.storage.local.set({mode});
    connect();
    await refreshBadge();
    return {ok: true};
  }
  if (request.ui === "toggle") {
    if (mode !== "manual") throw new Error("Switch to manual mode to approve individual tabs.");
    const [tab] = await browser.tabs.query({active: true, currentWindow: true});
    if (!tab || tab.id !== request.tabId || !eligible(tab)) throw new Error("Active tab changed or is unsupported.");
    if (approved.has(tab.id)) {
      clearTabDownloads(tab.id);
      approved.delete(tab.id);
      await browser.browserAction.setBadgeText({tabId: tab.id, text: ""});
    } else {
      approved.set(tab.id, origin(tab.url));
      connect();
      await browser.browserAction.setBadgeText({tabId: tab.id, text: "ON"});
    }
    return {ok: true};
  }
  throw new Error("Unsupported UI request");
});
browser.permissions.onRemoved.addListener(async () => {
  if (!await browser.permissions.contains({permissions:["downloads"]})) {
    clearDownloads(); downloadListeners = false;
    try {
      browser.downloads?.onCreated.removeListener(downloadCreated);
      browser.downloads?.onChanged.removeListener(downloadUpdated);
    } catch (_) { /* Permission revocation can remove the API itself. */ }
  }
  if (mode === "all" && !await browser.permissions.contains(allSites)) await manualMode();
});
browser.tabs.onRemoved.addListener(id => { approved.delete(id); clearTabDownloads(id); });
browser.tabs.onUpdated.addListener((id, change) => {
  if (change.url) for (const watch of watches.values())
    if (watch.tabId === id && watch.url !== change.url) { clearTabDownloads(id); break; }
  if (approved.has(id) && change.url && origin(change.url) !== approved.get(id)) {
    approved.delete(id);
    browser.browserAction.setBadgeText({tabId: id, text: ""});
  }
});
