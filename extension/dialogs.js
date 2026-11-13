/* Page dialog hook (alert/confirm/prompt). Packaged code only; no evaluation.
 *
 * A native page dialog pauses the page until someone answers it, and no
 * WebExtension API can answer one that is already on screen. So the bridge
 * stands in front of the page's three dialog methods:
 *  - during an agent action, dialogs get the action's answer (at most a few);
 *  - an answer the agent armed beforehand is used once for the next match;
 *  - anything else is shown natively for the user, and the extension is told
 *    first, so the agent gets a clear "blocked by a page dialog" report.
 * Runs at document_start in automatic mode (before page scripts) and whenever
 * the bridge content script is injected. Shared with content.js through the
 * content-script sandbox (globalThis.drimaDialogs).
 */
(() => {
  if (typeof exportFunction !== "function") return; // Firefox content scripts only.
  const page = window.wrappedJSObject;
  if (!page) return;
  const state = globalThis.drimaDialogs || (globalThis.drimaDialogs = {act:null, armed:[], hooked:new WeakSet()});
  if (state.hooked.has(page)) return;
  state.hooked.add(page);
  const MAX_TEXT = 300;
  const clean = text => String(text ?? "").replace(/[\x00-\x1f\x7f]/g, " ").slice(0, MAX_TEXT);
  // Never type a value into a page prompt that asks for a secret.
  const CREDENTIAL = /pass(word|code|phrase)|contrase|\bpin\b|\botp\b|token|secret|verification code|c[oó]digo de verificaci|2fa|one[- ]time/i;
  const native = {alert:page.alert, confirm:page.confirm, prompt:page.prompt};
  let sequence = 0;
  function notify(event, entry, extra = {}) {
    try { browser.runtime.sendMessage({drima_dialog:{event, ...entry, ...extra}}).catch(() => {}); } catch (_) { /* Extension reloading. */ }
  }
  function answerFor(entry, answer, value) {
    // Returns [page result, reported answer].
    if (entry.type === "alert") return [undefined, "ok"];
    if (entry.type === "confirm") return [answer === "accept", answer];
    if (answer !== "accept") return [null, "dismiss"];
    if (value != null && CREDENTIAL.test(entry.message)) return [null, "dismiss_credential_prompt"];
    return [value != null ? String(value) : entry.default, "accept"];
  }
  function takeArmed(entry) {
    // Armed in this document (state.armed) or by a reload arm registered for
    // this origin (globalThis.drimaDialogArms, top frame only).
    const now = Date.now();
    const lists = [state.armed, window === window.top ? (globalThis.drimaDialogArms || []) : []];
    for (const list of lists) {
      for (let i = 0; i < list.length; i++) {
        const arm = list[i];
        if (arm.expires < now) { list.splice(i--, 1); continue; }
        if (arm.type && arm.type !== entry.type) continue;
        if (arm.match && !entry.message.includes(arm.match)) continue;
        list.splice(i, 1);
        return arm;
      }
    }
    return null;
  }
  function handle(type, message, fallback) {
    const entry = {id:`d${Date.now().toString(36)}${(++sequence).toString(36)}`, type, message:clean(message),
      origin:location.origin, frame:window === window.top ? "top" : "frame", since:new Date().toISOString()};
    if (type === "prompt") entry.default = String(fallback ?? "").slice(0, 500);
    const act = state.act;
    if (act && act.answered.length < act.max) {
      const [result, answered] = answerFor(entry, act.answer, act.value);
      act.answered.push({type, message:entry.message, answered});
      return result;
    }
    const arm = takeArmed(entry);
    if (arm) {
      const [result, answered] = answerFor(entry, arm.answer, arm.value);
      notify("answered", entry, {answered, answered_by:"armed", arm_id:arm.id});
      return result;
    }
    // Shown natively: the user answers. Tell the extension first so in-flight
    // and later requests for this tab fail clearly instead of hanging.
    notify("open", entry, act ? {act_dialogs:act.answered.slice(), over_limit:true} : {});
    let result;
    try { result = native[type].call(page, ...(type === "prompt" ? [message, fallback] : [message])); }
    finally { notify("closed", entry, {answered_by:"user"}); }
    return result;
  }
  exportFunction(function alert(message) { return handle("alert", message); }, page, {defineAs:"alert"});
  exportFunction(function confirm(message) { return handle("confirm", message); }, page, {defineAs:"confirm"});
  exportFunction(function prompt(message, fallback) { return handle("prompt", message, fallback); }, page, {defineAs:"prompt"});
})();
