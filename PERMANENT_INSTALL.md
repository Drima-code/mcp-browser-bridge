# Install MCP Browser Bridge permanently in normal Firefox

Version 0.2.6, Firefox 142+ on Linux. The archive built here is **unsigned**.
Normal Firefox installs a permanent copy only after Mozilla signs it. A public
store listing is optional; self-distribution is sufficient for personal use.
Do not disable Firefox signature checks or change browser security settings.

## 1. Build and validate

```bash
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts
npx --yes web-ext lint --source-dir extension --warnings-as-errors
uv run --frozen python scripts/package_extension.py
```

Upload `artifacts/mcp-browser-bridge-0.2.6.zip`. The archive contains only the six
extension assets, license and privacy notice. It contains no browser profiles,
credentials, job applications or native host executable. Source JS is plain,
readable code with no build/minification step or remote executable code.

## 2. Get Mozilla's signed XPI

1. Open <https://addons.mozilla.org/developers/addons/> in **normal Firefox**.
2. Sign in with your Mozilla account. If this extension ID is already registered,
   open that extension and upload a **new version**, rather than creating another
   add-on. The ID remains `approved-tabs@drima-browser.local` so the native host
   continues to recognize it.
3. For a new private submission, choose **Submit a New Add-on → On your own**
   (self-distribution / unlisted), then upload the ZIP above. Choose desktop Linux.
   If the existing submission is listed, follow its version/channel controls.
4. The code is readable and unbuilt; no separate generated-source package is
   needed. Supply the repository/privacy links if requested. Review notes below
   explain the native host requirement and how to test the extension.
5. Complete Mozilla's validation/signing steps and download the **signed `.xpi`**.
   Signing/review is Mozilla's process; a ZIP, successful upload or local lint
   result is not a signature or approval.

## 3. Install once

In normal Firefox, open `about:addons` → gear menu → **Install Add-on From File…**
→ select Mozilla's signed XPI → **Add**. Accept Firefox's data-transfer disclosure
if you want to use the bridge. The installed extension then survives restarts.
Pin **MCP Browser Bridge** to the toolbar using Firefox's extensions menu.

If the same ID is still loaded temporarily through `about:debugging`, remove that
temporary copy before installing the signed file, if Firefox reports a conflict.
The native host registration remains in place. Manual approvals intentionally
reset after restart; that is different from reinstalling the extension.

## 4. Connect Codex to normal Firefox

The companion native host is already registered on David's machine. On a new
machine, run `uv sync --frozen` and
`uv run --frozen python scripts/install_regular_connector.py` once.

Open a normal website tab, open the extension popup and choose **Approve this tab**.
Alternatively, use its explicit automatic-access consent and Firefox permission
prompt if you want current/future regular website tabs accessible after restart.
**Reconnect native host** restores a dropped connection without resending any
website action or expanding website permissions.

Use the `mcp-browser-bridge` MCP (`stdio`) for this browser. The original
`drima-browser` (`stdio`) starts the separate automation-controlled browser and
will not switch simply because the extension is installed.

Check the connection with:

```bash
uv run --frozen mcp-browser-bridge call tabs
```

Version 0.2.2 supports explicit file uploads through `regular_browser_upload` or
`call upload`; see README.md for limits and usage. Reconnect the Codex MCP
server if its tool list predates the new upload tool. Logins, CAPTCHA and interviews
are still completed manually. Snapshots and form actions support explicit same-origin frame paths and open
shadow roots in 0.2.6. Cross-origin frames, closed roots and protected Firefox
pages remain inaccessible.

Version 0.2.3 fixes mouse-down-driven dropdowns without new permissions or tool
arguments. After installing the signed update, reload an existing website tab
before resuming its form so it gets the updated content listener. Reloading can
clear unsaved form values; recheck them and inspect the application history before
another final-send action. Updating only the Python/native host cannot change
content code already installed in normal Firefox.

## Reviewer notes

This Linux-only bridge uses `activeTab`, `nativeMessaging` and `storage`, plus
optional HTTP/HTTPS host permissions granted only through the packaged popup.
It communicates with `drima_regular_browser`, a local open-source Python native
host in this repository. It has no analytics, remote relay, cookies, webRequest,
debugger permission or arbitrary JavaScript evaluation tool.

Install the companion host using the instructions above. Test with a synthetic
HTML form on a local HTTP server; approve its tab in the popup, then run
`call tabs`, `snapshot` and `act`. Never use live transactions as tests.
Revoking approval, changing origin, or switching to manual mode prevents further
access. Login controls are blocked. Native-host disconnects are recoverable from
the popup; mutating actions are never automatically retried.

## Updates

Every changed extension version must be signed again. With self-distribution and
no hosted update manifest, install each new signed XPI manually. This is only for
updates, not every Firefox restart. Keep the same extension ID.

Official Mozilla instructions:
- <https://extensionworkshop.com/documentation/publish/submitting-an-add-on/>
- <https://extensionworkshop.com/documentation/publish/install-self-distributed/>

## Viewer update 0.2.4

Install the same-ID signed 0.2.5 update, reload the website tab, and reconnect the
MCP client to discover snapshot scope/paging and hover arguments. A Python-only
update leaves old installed content code unchanged. Viewer fixes need no added
permissions; 0.2.5 separately offers optional download tracking.
Validation uses synthetic pages; resume the authorized Gmail work to verify its
real attachment flow after installation, without claiming that successful clicks
alone prove a completed download.

## Protocol and download tracking update 0.2.5

Install the same-ID signed 0.2.5 addon, reload existing website tabs, and reconnect
the MCP client. Version negotiation now blocks actions with stale installed
addons or content listeners, rather than silently ignoring new parameters.
Enable download tracking separately in the popup if needed and accept Firefox's
optional downloads permission. The base required permissions remain unchanged.
This permission enables future-event observation, not an automatic file download.
See README and PRIVACY for origin/filename matching and provenance limitations.


## Document discovery update 0.2.6

Sign/install the same-ID 0.2.6 archive, reload website tabs, then reconnect MCP
discovery for `regular_browser_focus`, `regular_browser_wait`, `frame_path` and
`document_id`. No new permissions. See README for same-origin frame boundaries,
open shadow selectors, bounded waits and unchanged single-shot behavior.
The release is tested on synthetic pages; the Gmail receipt collection still
requires resuming after installation and verifying actual saved attachments.
