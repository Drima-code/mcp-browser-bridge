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
  $("toggle").disabled = state.mode !== "manual" || !state.eligible;
  $("toggle").textContent = state.approved ? "Revoke this tab" : "Approve this tab";
  $("all").disabled = state.mode === "all" || !$("consent").checked;
}
function fail(error) { $("error").textContent = error.message; }
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
