const $ = id => document.getElementById(id);
const allSites = {origins: ["http://*/*", "https://*/*"]};
let state;
async function render() {
  $("error").textContent = "";
  state = await browser.runtime.sendMessage({ui: "state"});
  $("status").textContent = `Version: ${state.addonVersion || "unknown"}. Mode: ${state.mode === "all" ? "automatic (all website tabs)" : "manual approval"}. Native host: ${state.connected ? "connected" : "not connected"}.`;
  $("downloads").disabled = !!state.downloadsPermission;
  $("downloads-off").disabled = !state.downloadsPermission;
  $("reconnect").disabled = state.connected;
  $("screenshots").disabled = !!state.screenshotsPermission;
  $("screenshots-off").disabled = !state.screenshotsPermission;
  $("notifications").disabled = !!state.notificationsPermission;
  $("notifications-off").disabled = !state.notificationsPermission;
  const list = $("attention");
  list.replaceChildren();
  $("attention-empty").hidden = state.attention.length > 0;
  for (const flag of state.attention) {
    // Text nodes only: titles and reasons come from websites and clients.
    const item = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = `${flag.reason} — ${flag.title || flag.url}`;
    const go = document.createElement("button");
    go.textContent = "Go to tab";
    go.addEventListener("click", () => browser.runtime.sendMessage({ui:"attention-go",tabId:Number(flag.tab_id)}).then(render).catch(fail));
    const done = document.createElement("button");
    done.textContent = "Done";
    done.addEventListener("click", () => browser.runtime.sendMessage({ui:"attention-done",tabId:Number(flag.tab_id)}).then(render).catch(fail));
    item.append(text, go, done);
    list.append(item);
  }
  $("google-account-selection").checked = state.googleAccountSelection === true;
  $("toggle").disabled = state.mode !== "manual" || !state.eligible;
  $("toggle").textContent = state.approved ? "Revoke this tab" : "Approve this tab";
  $("all").disabled = state.mode === "all" || !$("consent").checked;
}
function fail(error) { $("error").textContent = error.message; }
$("google-account-selection").addEventListener("change", async () => {
  try {
    await browser.runtime.sendMessage({ui:"google-account-selection",enabled:$("google-account-selection").checked});
    await render();
  } catch (error) { await render().catch(() => {}); fail(error); }
});
$("downloads").addEventListener("click", async () => {
  try {
    const granted = await browser.permissions.request({permissions:["downloads"]});
    if (!granted) throw new Error("Download tracking permission denied; tracking remains disabled.");
    await browser.runtime.sendMessage({ui:"downloads"}); await render();
  } catch (error) { fail(error); }
});
$("downloads-off").addEventListener("click", async () => {
  try { await browser.permissions.remove({permissions:["downloads"]}); await render(); }
  catch (error) { fail(error); }
});
$("screenshots").addEventListener("click", async () => {
  try {
    // Directly in the click handler so Firefox shows its permission prompt.
    const granted = await browser.permissions.request({origins:["<all_urls>"]});
    if (!granted) throw new Error("Screenshot permission denied; screenshots remain off.");
    await render();
  } catch (error) { fail(error); }
});
$("screenshots-off").addEventListener("click", async () => {
  try { await browser.permissions.remove({origins:["<all_urls>"]}); await render(); }
  catch (error) { fail(error); }
});
$("notifications").addEventListener("click", async () => {
  try {
    const granted = await browser.permissions.request({permissions:["notifications"]});
    if (!granted) throw new Error("Notification permission denied; flags still show in tab titles and here.");
    await browser.runtime.sendMessage({ui:"notifications"}); await render();
  } catch (error) { fail(error); }
});
$("notifications-off").addEventListener("click", async () => {
  try { await browser.permissions.remove({permissions:["notifications"]}); await render(); }
  catch (error) { fail(error); }
});
$("reconnect").addEventListener("click", async () => {
  try { await browser.runtime.sendMessage({ui: "reconnect"}); await render(); }
  catch (error) { fail(error); }
});
$("consent").addEventListener("change", () => {
  $("all").disabled = !$("consent").checked || state?.mode === "all";
});
$("toggle").addEventListener("click", async () => {
  try { await browser.runtime.sendMessage({ui: "toggle", tabId: state.tabId}); await render(); }
  catch (error) { fail(error); }
});
$("manual").addEventListener("click", async () => {
  try {
    await browser.runtime.sendMessage({ui: "manual"});
    await browser.permissions.remove(allSites);
    $("consent").checked = false;
    await render();
  } catch (error) { fail(error); }
});
$("all").addEventListener("click", async () => {
  if (!$("consent").checked) return;
  try {
    // Directly in the user click handler, before any other awaited work.
    const granted = await browser.permissions.request(allSites);
    if (!granted) throw new Error("Website access denied; automatic mode was not enabled.");
    await browser.runtime.sendMessage({ui: "all", confirmed: true});
    await render();
  } catch (error) { fail(error); }
});
render().catch(fail);
