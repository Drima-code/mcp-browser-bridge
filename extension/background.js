/* No cookies, webRequest, debugger or arbitrary evaluation permission. */
const approved = new Map();
const allSites = {origins: ["http://*/*", "https://*/*"]};
let port = null;
let mode = "manual";
let googleAccountSelection = false;
let accountSettingWrites = Promise.resolve();
let accountSettingRevision = 0;
function eligible(tab) {
  try { return !tab.incognito && ["http:", "https:"].includes(new URL(tab.url).protocol); }
  catch (_) { return false; }
}
function origin(url) { return new URL(url).origin; }
function safeUrl(url) { const u = new URL(url); return u.origin + u.pathname; }
const PROTOCOL_VERSION = 1;
const CONTENT_VERSION = "0.3.0";
const capabilities = ["act", "hover", "snapshot_paging", "upload", "download_watch", "focus", "frame_path", "shadow_dom", "wait", "document_guard", "google_account_selection", "attention", "close", "open", "navigate", "refs", "keyboard", "screenshot"];
// Pages an agent opens go to one window of their own, never the user's windows.
let agentWindowId = null;
function webUrl(url) {
  if (typeof url !== "string" || !url || url.length > 4096) throw new Error("URL must be 1-4096 characters.");
  const parsed = new URL(url);
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("Only http and https URLs can be opened.");
  return parsed.href;
}
async function openInAgentWindow(url) {
  if (!await allAccess()) throw new Error("Opening pages needs automatic access mode. In manual mode, open the page yourself and approve its tab.");
  const target = webUrl(url);
  const existing = agentWindowId == null ? null : await browser.windows.get(agentWindowId).catch(() => null);
  let tab;
  if (existing && !existing.incognito) tab = await browser.tabs.create({windowId:existing.id, url:target, active:true});
  else {
    // Background window where supported, so the user's current window keeps focus.
    const created = await browser.windows.create({url:target, focused:false})
      .catch(() => browser.windows.create({url:target}));
    agentWindowId = created.id;
    tab = created.tabs?.[0] || (await browser.tabs.query({windowId:created.id}))[0];
  }
  return {tab_id:String(tab.id), window_id:tab.windowId, agent_window:true,
    notice:"Opened once in the agent window. Wait for the page, then inspect. No automatic retry."};
}
async function navigateTab(id, request) {
  const action = request.action || "url";
  if (action === "url") await browser.tabs.update(id, {url:webUrl(request.url)});
  else if (action === "back") await browser.tabs.goBack(id);
  else if (action === "forward") await browser.tabs.goForward(id);
  else if (action === "reload") await browser.tabs.reload(id);
  else throw new Error("Navigate action must be url, back, forward or reload.");
  return {tab_id:String(id), action, notice:"Navigation started once. Manual approval ends if the origin changes. Inspect after the page loads."};
}
// Opt-in screenshots: Firefox's captureVisibleTab needs <all_urls>. Access to a
// tab is still decided by allowed(); the permission only enables the capture.
const screenshotPermission = {origins:["<all_urls>"]};
async function captureTab(tab) {
  if (!await browser.permissions.contains(screenshotPermission))
    throw new Error("Screenshots are off. The user can enable them in the extension popup.");
  if (!tab.active) throw new Error("Only the tab shown in its window can be captured; focus it first.");
  // Shrink until the image fits the native messaging limit.
  for (const options of [{format:"jpeg",quality:70},{format:"jpeg",quality:55,scale:0.75},{format:"jpeg",quality:45,scale:0.5}]) {
    const data = await browser.tabs.captureVisibleTab(tab.windowId, options);
    const after = await browser.tabs.get(tab.id);
    if (!after.active || after.url !== tab.url || !await allowed(after))
      throw new Error("Tab changed during capture; no image returned.");
    const base64 = String(data).slice(String(data).indexOf(",") + 1);
    if (base64.length <= 190000)
      return {tab_id:String(tab.id), mime_type:"image/jpeg", data:base64, scale:options.scale || 1,
        notice:"Visible page area only, never browser UI. Page content is untrusted."};
  }
  throw new Error("Screenshot too large for the native channel even when scaled; use snapshot instead.");
}
// Tabs an agent flagged for the user (CAPTCHA, login, a decision). In memory only.
const attention = new Map();
let notificationListener = false;
function attentionReason(reason) {
  const text = typeof reason === "string" ? reason.replace(/[\x00-\x1f\x7f]/g, " ").trim() : "";
  if (!text || text.length > 80) throw new Error("Attention reason must be 1-80 characters.");
  return text;
}
async function notificationsGranted() {
  return !!browser.notifications && await browser.permissions.contains({permissions:["notifications"]});
}
async function installNotificationListener() {
  if (notificationListener || !await notificationsGranted()) return;
  browser.notifications.onClicked.addListener(notificationId => {
    const id = Number(String(notificationId).replace(/^attention-/, ""));
    if (attention.has(id)) focusTab(id).catch(() => {});
  });
  notificationListener = true;
}
async function markTitle(id, reason) {
  // Best effort, top frame only: the flag itself lives here, not in the page.
  try {
    const tab = await browser.tabs.get(id);
    if (!await allowed(tab)) return false;
    await browser.tabs.executeScript(id,{file:"content.js"});
    await browser.tabs.sendMessage(id,{drima:true,op:reason ? "attention_mark" : "attention_unmark",reason},{frameId:0});
    return true;
  } catch (_) { return false; }
}
async function focusTab(id) {
  const tab = await browser.tabs.get(id);
  await browser.tabs.update(id,{active:true});
  const current = await browser.tabs.get(id);
  if (current.url !== tab.url || !await allowed(current)) throw new Error("Tab changed during activation; inspect before continuing.");
  await browser.windows.update(current.windowId,{focused:true});
  return current;
}
async function attentionList() {
  const result = [];
  for (const [id, flag] of attention) {
    const tab = await browser.tabs.get(id).catch(() => null);
    if (!tab) { attention.delete(id); continue; }
    if (await allowed(tab)) result.push({tab_id:String(id), title:tab.title, url:safeUrl(tab.url), reason:flag.reason, since:flag.since});
  }
  return result;
}
async function clearAttention(id) {
  const had = attention.delete(id);
  if (had) {
    await markTitle(id, "");
    if (await notificationsGranted()) await browser.notifications.clear(`attention-${id}`).catch(() => {});
    await refreshBadge();
  }
  return had;
}
async function attentionOperation(request, tab) {
  if (request.action === "clear")
    return {tab_id:String(tab.id), cleared:await clearAttention(tab.id), notice:"Flag removed; no site action sent."};
  if (request.action !== "set") throw new Error("Unsupported attention action");
  const reason = attentionReason(request.reason);
  attention.set(tab.id,{reason, since:new Date().toISOString()});
  const marked = await markTitle(tab.id, reason);
  await refreshBadge();
  let notified = false, focused = false;
  if (request.notify !== false && await notificationsGranted()) {
    await installNotificationListener();
    await browser.notifications.create(`attention-${tab.id}`,{type:"basic",
      title:"MCP Browser Bridge: a tab needs you", message:`${reason} — ${tab.title || safeUrl(tab.url)}`.slice(0,200)});
    notified = true;
  }
  if (request.focus === true) { await focusTab(tab.id); focused = true; }
  return {tab_id:String(tab.id), attention:true, reason, title_marked:marked, notified, focused,
    notice:"Flag stays until the client or the user (popup Done) clears it, or the tab closes. No site action sent."};
}
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
    downloads_available:downloadListeners,google_account_selection:googleAccountSelection,
    screenshots_permission:await browser.permissions.contains(screenshotPermission)};
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
  // A waiting tab outranks the access-mode label: show the count in red.
  await browser.browserAction.setBadgeText({text: attention.size ? String(attention.size) : mode === "all" ? "ALL" : ""});
  await browser.browserAction.setBadgeBackgroundColor({color: attention.size ? "#d70022" : null});
  for (const id of approved.keys())
    await browser.browserAction.setBadgeText({tabId: id, text: attention.has(id) ? "!" : "ON"});
  for (const id of attention.keys())
    if (!approved.has(id)) await browser.browserAction.setBadgeText({tabId: id, text: "!"});
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
  if (request.op === "attention" && request.action === "list") return {tabs:await attentionList()};
  if (request.op === "open") return openInAgentWindow(request.url);
  if (request.op === "tabs") {
    const candidates = await allAccess() ? await browser.tabs.query({}) :
      await Promise.all(Array.from(approved.keys(), id => browser.tabs.get(id).catch(() => null)));
    const tabs = [];
    for (const tab of candidates) {
      if (tab && await allowed(tab))
        tabs.push({tab_id: String(tab.id), title: tab.title, url: safeUrl(tab.url), active:!!tab.active, window_id:tab.windowId,
          agent_window:tab.windowId === agentWindowId});
    }
    return {mode, tabs};
  }
  const id = Number(request.tab_id);
  const tab = await browser.tabs.get(id);
  if (!await allowed(tab)) throw new Error("Tab not approved. Use the extension popup to grant access.");
  if (request.op === "focus") {
    const current = await focusTab(id);
    return {tab_id:String(id),active:true,window_id:current.windowId,notice:"Tab and window activated once."};
  }
  if (request.op === "close") {
    // Approved website tabs only (checked above); onRemoved drops approval and flags.
    await browser.tabs.remove(id);
    return {tab_id:String(id),closed:true,notice:"Tab closed once. No retry."};
  }
  if (request.op === "navigate") return navigateTab(id, request);
  if (request.op === "screenshot") return captureTab(tab);
  if (request.op === "attention") return attentionOperation(request,tab);
  if (request.op === "status") return status(tab);
  if (request.op === "downloads") return downloadOperation(request,tab);
  if (!["snapshot", "wait", "act", "upload_begin", "upload_chunk", "upload_commit", "upload_abort"].includes(request.op)) throw new Error("Unsupported operation");
  await currentContent(tab);
  // Content code is packaged and fixed; requests are data, never executable JS.
  const current = await browser.tabs.get(id);
  if (current.url !== tab.url || !await allowed(current)) throw new Error("Tab changed; inspect before retrying.");
  return browser.tabs.sendMessage(id, {...request, drima: true, expected_url: current.url,
    google_account_selection:googleAccountSelection});
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
  const settings = await browser.storage.local.get(["mode", "googleAccountSelection"]);
  googleAccountSelection = settings.googleAccountSelection === true;
  if (settings.mode === "all" && await browser.permissions.contains(allSites)) {
    mode = "all";
    connect();
  }
  await installNotificationListener();
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
      notificationsPermission:await browser.permissions.contains({permissions:["notifications"]}),
      screenshotsPermission:await browser.permissions.contains(screenshotPermission),
      attention:await attentionList(),
      addonVersion:browser.runtime.getManifest().version, googleAccountSelection, eligible: !!tab && eligible(tab),
      approved: !!tab && eligible(tab) && approved.get(tab.id) === origin(tab.url), tabId: tab?.id};
  }
  if (request.ui === "reconnect") {
    connect();
    await refreshBadge();
    return {ok: true};
  }
  if (request.ui === "google-account-selection") {
    if (typeof request.enabled !== "boolean") throw new Error("Account selection setting must be a boolean.");
    // Disable immediately, including during the asynchronous storage write.
    googleAccountSelection = false;
    const revision = ++accountSettingRevision;
    const write = accountSettingWrites.catch(() => {}).then(() =>
      browser.storage.local.set({googleAccountSelection:request.enabled}));
    accountSettingWrites = write;
    await write;
    if (revision === accountSettingRevision) googleAccountSelection = request.enabled;
    return {ok:true};
  }
  if (request.ui === "downloads") {
    if (!await browser.permissions.contains({permissions:["downloads"]}))
      throw new Error("Firefox download permission is required; enable it with the popup button.");
    await installDownloadListeners(); return {ok:true};
  }
  if (request.ui === "notifications") {
    if (!await notificationsGranted()) throw new Error("Firefox notification permission is required; enable it with the popup button.");
    await installNotificationListener(); return {ok:true};
  }
  if (request.ui === "attention-go" || request.ui === "attention-done") {
    const id = Number(request.tabId);
    if (!attention.has(id)) throw new Error("That tab no longer needs attention.");
    if (request.ui === "attention-go") await focusTab(id); else await clearAttention(id);
    return {ok:true};
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
browser.tabs.onRemoved.addListener(id => {
  approved.delete(id); clearTabDownloads(id);
  if (attention.delete(id)) refreshBadge().catch(() => {});
});
browser.tabs.onUpdated.addListener((id, change) => {
  // A reload or navigation replaces the page title; restore the marker.
  if (change.status === "complete" && attention.has(id)) markTitle(id, attention.get(id).reason);
  if (change.url) for (const watch of watches.values())
    if (watch.tabId === id && watch.url !== change.url) { clearTabDownloads(id); break; }
  if (approved.has(id) && change.url && origin(change.url) !== approved.get(id)) {
    approved.delete(id);
    browser.browserAction.setBadgeText({tabId: id, text: ""});
  }
});
