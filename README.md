# MCP Browser Bridge

Control existing Firefox website tabs through the **MCP Browser Bridge** addon and a local native host. This project does not launch a separate browser.

## Install

Requires Python 3.12+, uv and Firefox 142+ on Linux.

```bash
uv sync --frozen
uv run --frozen python scripts/install_regular_connector.py
codex mcp add mcp-browser-bridge -- /absolute/path/mcp-browser-bridge/.venv/bin/mcp-browser-bridge stdio
```

## Firefox extension


The Firefox extension is named **MCP Browser Bridge**. Its source is in
`extension/` and is distributed under this repository's MIT license. It requires
Firefox 142+ for built-in data-transfer consent. See [privacy notice](PRIVACY.md):
approved page content goes to your local MCP client, which may send it to its AI
provider depending on that client's configuration.
It also requires
the local native host below; installing the extension alone is not sufficient.
For permanent installation, follow [the signing and install guide](PERMANENT_INSTALL.md).
Build an upload ZIP with `uv run --frozen python scripts/package_extension.py`,
get it signed by Mozilla (unlisted/self-distribution is sufficient), then install
the signed XPI through `about:addons`. A public store listing is being prepared;
no approved Mozilla listing is claimed yet.

The separate Drima Browser MCP project launches its own browser profile. This connector instead uses Firefox's supported extension/native
messaging APIs, keeping existing sessions in that browser. **Manual per-tab approval
is the default**, on any regular HTTP/HTTPS website. The toolbar popup also offers
explicit opt-in automatic access to all current and future regular website tabs.
It does not copy cookies, spoof automation or change account security. It cannot
bypass Firefox's protected-page restrictions. Private tabs, file URLs and internal
browser pages are excluded.

```bash
uv sync --frozen
uv run --frozen python scripts/install_regular_connector.py
```

In your normal Firefox, open `about:debugging#/runtime/this-firefox`, click
**Load Temporary Add-on**, and select this checkout's `extension/manifest.json`.
Open the toolbar popup and click **Approve this tab**: **ON** indicates approval;
use **Revoke this tab** to stop it. Leaving the approved origin revokes approval.
For automatic mode, acknowledge its privacy warning and click **Enable automatic
access**, then accept Firefox's optional website permission prompt. **ALL** marks
that mode, which persists across restarts. **Switch to manual** stops automatic
access and removes those optional permissions. The native MCP client cannot change
the access mode. No built-in tab-count cap; resource/message-size limits still apply.
The popup also has an optional **Google account selection** checkbox, off by default.
When enabled, account chooser rows on approved `https://accounts.google.com` tabs
become click targets. Selecting an existing account can sign you into a website.
The choice persists and can be disabled in the popup; the MCP client cannot change it.
Passwords, verification codes and CAPTCHA still require the user.

An **ERR** badge indicates a native-host connection problem. Temporary extensions
unload when Firefox restarts; permanent installation requires Mozilla signing.

Register MCP `mcp-browser-bridge` with command `/absolute/path/mcp-browser-bridge/.venv/bin/mcp-browser-bridge`
and args `["stdio"]`. This server only connects to the Firefox extension.
For immediate diagnostics without client rediscovery:

```bash
uv run --frozen mcp-browser-bridge call tabs
uv run --frozen mcp-browser-bridge call snapshot '{"tab_id":"APPROVED_ID"}'
```

Available tools: approved-tab listing/activation, bounded snapshots, read-only waits and single
strict-selector click/fill/select/check/uncheck actions. Password/token controls
blocked. Snapshots include strict CSS selectors, current checkbox state and attached filenames.
Actions reject read-only/disabled controls and report failed value changes.
Version 0.2.3 fixes custom dropdowns that open on mouse-down rather than click.
The `click` action sends pointer/mouse down and up events, respects cancelled
focus defaults, and activates the target once. If a handler removes, disables,
or navigates away from the target, it stops and reports an uncertain outcome.
Snapshots expose `expanded`, `selected`, `aria_controls`, and ARIA-required state
so an agent can open a combobox, inspect its visible options, click one, and verify
selection. Typing “Yes” into a combobox does not select that option. Native
`select` remains for visible HTML select elements only. These are synthetic
events; sites requiring trusted native input still need manual interaction.
The popup includes **Reconnect native host** for dropped connections without
replaying website actions. No arbitrary JS evaluation, network interception or
automatic retries. Version 0.2.2 adds **regular_browser_upload** for explicit
absolute local paths: up to 10 files and 25 MiB combined. Files are sent in bounded
chunks; only the final commit attaches them and triggers the site's upload events.
Only filenames and file contents reach the approved tab; local paths stay local.
The input's type filter, disabled state and multi-file setting are checked. Visible
inputs and hidden inputs with visible associated labels are supported. Snapshots
include their selectors, accepted types and selected filenames.

```bash
uv run --frozen mcp-browser-bridge call upload \
  '{"tab_id":"APPROVED_ID","selector":"#resume","paths":["/absolute/path/resume.pdf"]}'
```

Attach only files the user has authorized for that site. Selection can start an
upload immediately; it does not prove the employer accepted the application.
Inspect filenames and page validation before submitting. A timeout or navigation
change must be inspected before any retry. No directory uploads or file-picker
automation. Iframe uploads and sites requiring trusted picker events remain manual.
The user still handles passwords, verification codes, CAPTCHA and assessments.

Install the Mozilla-signed **0.2.3** update with the same extension ID. The local
Python code is available immediately through the diagnostic CLI above; reconnect
Codex's regular MCP server if its tool list still lacks `regular_browser_upload`.
The installed-extension smoke test uses a disposable Firefox profile and a local
synthetic form; it checks file uploads, a mouse-down-driven custom dropdown,
and exactly one synthetic form submission. It never touches user accounts or
real applications.

Linux only: native host uses a mode-0600 Unix socket inside a mode-0700 user-owned
directory. It is a trusted-user local bridge, not isolation from same-user malware.
No HTTP listener or external relay. Install only in a browser you control.

Reference: [Mozilla native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)
and [activeTab permissions](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Working_with_the_Tabs_API).


## Development

Claude and Codex entry points are [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md).
On David's workstation they route to shared coordination in a sibling private
checkout when available. Public contributors can follow the ordinary development
instructions below without that checkout.

```bash
uv sync --frozen --group dev
uv run --frozen playwright install firefox --no-shell
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts
```

Playwright is used only for synthetic tests, not by the bridge at runtime. Tool names remain `regular_browser_*` for client compatibility. The native host and extension IDs remain unchanged for installed addons.

## License

[MIT](LICENSE). Runtime profiles, credentials and downloads stay outside this repository.

## Viewer controls in version 0.2.4

Snapshots prioritize the visible dialog, including its text, rather than the
background inbox. `aria-hidden` is reported as accessibility metadata and no
longer makes a visibly rendered control disappear. CSS-hidden and inert targets,
password/token controls, strict matching and website access grants retain their
checks.

When `controls_truncated` is true, repeat the **read-only snapshot** with the
returned `next_offset`. Each page reports `controls_total` and `controls_offset`;
`limit` is 1–100. Reinspect after any page change because offsets are not stable
across changes. An observed CSS `scope` restricts text and controls to one visible
viewer or toolbar. Native replies remain bounded, including large option lists.

`regular_browser_act` also accepts `hover`: it dispatches synthetic pointer/mouse
events at the target without clicking or moving the desktop cursor. Inspect the
next snapshot for a revealed toolbar. It cannot reproduce trusted native input,
expose controls genuinely hidden by CSS without the site's handlers, or inspect
Firefox's protected PDF viewer. No new extension permissions are required.

Update the addon to 0.2.5 **and reload the website tab** before using these
features; updating the Python server alone cannot update installed content code.
The release addresses reproducible viewer discovery defects; private Gmail
attachment downloads still need verification after installing the signed update.

## Version negotiation and download observation (0.2.5)

`regular_browser_status` reports the Python server version, installed addon
version, protocol, capabilities and optional download permission. Supply `tab_id`
to check the content listener loaded in that website too. Before an action, the
server negotiates support; outdated addons, unsupported capabilities and stale
content listeners produce an explicit update/reload error before the requested
action is sent. There is no fallback that silently ignores newer arguments.

Download tracking is disabled by default. Enable **Download tracking** in the
addon popup and accept Firefox's optional `downloads` permission. You can revoke
it from the same popup. Website approval is still required independently.

1. Call `regular_browser_downloads` with `action: "watch"`, the approved `tab_id`
   and the observed attachment's `expected_filename`, before clicking Download.
2. Click the site's Download control once.
3. Read `action: "list"` with the returned `watch_id`. Records report progressing,
   complete or interrupted state, bytes, errors and the saved path on completion.
4. `action: "stop"` stops observation only. It never cancels or restarts a file.

The observer listens only for future events while a watch is armed. It never
lists global or previous download history; metadata refreshes query only IDs
captured by that watch. Records omit download URLs and query strings. Watches
accept new candidates for five minutes and expire from memory after ten minutes
(cleanup runs on subsequent operations/events). Maximums: eight watches, ten
records per watch and bounded unmatched-event counts. Restart clears all watches.
Permission revocation, tab closure, navigation or website access revocation
invalidates observation. An empty watch means **not observed**, not proof that
the click failed. `tracking_error` flags a metadata-observation problem.

Firefox's [DownloadItem API](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/downloads/DownloadItem)
does not supply a source-tab ID. Matching uses referrer origin, expected basename
(including Firefox's numbered filename variants), and start time. Results are
explicitly candidates, not proven tab identity. Missing referrers and multiple
matching watches return counts without disclosing paths or unrelated metadata.
Inspect the saved document before treating it as the intended receipt.

Diagnostic CLI examples:

```bash
uv run --frozen mcp-browser-bridge call status
uv run --frozen mcp-browser-bridge call status '{"tab_id":"TAB_ID"}'
uv run --frozen mcp-browser-bridge call downloads '{"tab_id":"TAB_ID","action":"watch","expected_filename":"boleta.pdf"}'
uv run --frozen mcp-browser-bridge call downloads '{"tab_id":"TAB_ID","action":"list","watch_id":"WATCH_ID"}'
```

API permission reference: [Mozilla downloads](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/downloads).
These tools do not provide desktop pointer control. Same-origin frame support is described below.

Saved paths longer than 2048 characters are omitted with `path_omitted: true`, rather than returning a truncated, unusable path.


## Frames, open shadow roots and bounded waits (0.2.6)

Install the signed 0.2.6 addon, reload website tabs and refresh MCP discovery.
No additional permissions are required. `regular_browser_tabs` reports each
approved tab's active state and window ID. `regular_browser_focus(tab_id)`
activates that tab and focuses its window once, using Firefox's
[tabs.update](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/tabs/update)
and [windows.update](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/windows/update).

Snapshots now enumerate visible `frames`, with their selectors and accessibility.
Use an observed selector in `frame_path`, for example `["#embedded"]`, then inspect
that frame. Nested paths accept up to eight selectors. Only same-origin, visible,
non-inert frames are accessible; an approved parent never grants access to an
embedded site of another origin. Unavailable/sandboxed frames are reported.
Frame lists are bounded and report `frames_truncated` when incomplete.

Controls within open shadow roots are included automatically. Their returned
selectors separate each observed host with ` >>> `, for example
`#payment-widget >>> #save`. Use that selector unchanged for scope, actions,
uploads or waits. Each host and target must match uniquely; closed roots remain
inaccessible. Page code cannot be evaluated through these tools.

Each snapshot returns `document_id`. Pass it to subsequent snapshot, act, upload
or wait requests to reject replacement of the inspected document, even when its
URL is unchanged. This optional guard identifies a document, not every DOM update;
inspect again after changing a form or viewer. Uploads preserve the frame and
document context throughout staging and their single commit.

`regular_browser_wait(tab_id, selector, state="visible", timeout_ms=5000)` polls
read-only for visible/hidden/attached/detached state, up to 10 seconds. A timeout
returns `matched:false`; multiple targets are an error. It supports the same
`frame_path`, shadow selectors and `document_id`. No click or other mutation is
repeated while waiting. Lost replies report an unknown outcome with an instruction
to inspect before another action. A missing download event still does not prove
that a click failed.

```bash
uv run --frozen mcp-browser-bridge call snapshot '{"tab_id":"APPROVED_ID","frame_path":["#embedded"]}'
uv run --frozen mcp-browser-bridge call wait '{"tab_id":"APPROVED_ID","selector":"#viewer >>> #save","timeout_ms":3000}'
uv run --frozen mcp-browser-bridge call focus '{"tab_id":"APPROVED_ID"}'
```

## Tabs that need the user (0.2.9)

`regular_browser_attention(action="set", tab_id, reason)` flags an approved tab
for the user: the title gets a red marker and short reason (re-applied after
reloads and site title changes), the toolbar badge shows a red count and the
popup lists flagged tabs with **Go to tab** and **Done**. `notify` sends a desktop
notification when the user enabled the optional `notifications` permission in the
popup; `focus=true` also activates the tab once. `action="clear"` removes a flag
and `action="list"` returns flagged tabs. Flags live in memory, never touch the
site beyond the title text, and disappear when the tab closes.

## Agent window, keyboard and short refs (0.3.0)

- `regular_browser_open(url)` opens an http(s) page in one Firefox window reserved
  for the agent, so pages never land in the user's own windows. It works only in
  automatic access mode; in manual mode the user opens and approves pages. `tabs`
  marks agent-window tabs with `agent_window: true`.
- `regular_browser_navigate(tab_id, action, url)` loads a URL in an allowed tab or
  goes back, forward or reloads. Manual approval still ends on origin change.
- `regular_browser_close(tab_id)` closes an allowed tab that is no longer needed.
- Snapshots give every control a short `ref` (`ref=e12`) that `act`, `wait`,
  `upload` and `scope` accept in place of a CSS selector. Refs belong to one
  document and fail as stale after it changes. `compact=True` (the MCP default)
  omits empty fields and long positional selectors.
- `regular_browser_screenshot(tab_id)` (0.3.1) returns a JPEG of the visible area of an
  allowed tab that is shown in its window. Off by default: the popup's "Enable
  screenshots…" grants Firefox's `<all_urls>` permission, which
  `captureVisibleTab` requires; tab access is still decided by the access mode.
  Large images are re-captured at 75% then 50% scale to fit the native channel.
  CLI: `call screenshot '{"tab_id":"7","out":"/tmp/shot.jpg"}'`.
- `act` gains `press` (one allowlisted key: Enter, Escape, arrows, Backspace,
  Delete, Home, End, PageUp, PageDown, space) and `type` (append up to 500
  characters key by key, for fields that filter as you type). Synthetic keys reach
  page handlers but never trigger native defaults such as form submission.

## File choosers and new windows (0.3.2)

Firefox opens neither a native file chooser nor a pop-up for a synthetic click.

- `regular_browser_upload(..., via_picker=True)` takes the visible control that
  opens the site's chooser (LinkedIn "Upload resume" creates its file input only
  on click). For that one upload, the content script briefly replaces the page's
  `HTMLInputElement.prototype.click`/`showPicker` via Firefox `exportFunction`,
  clicks the control once, and attaches the staged files to the file input the
  site asked to open. Hooks are removed within 2 seconds; no dialog opens.
- While an agent click settles (~0.4 s), `window.open` and unhandled
  `target=_blank` link activations with http(s) addresses are handed to the
  extension, which opens them in the agent window (automatic access mode only).
  The click result lists `opened: [{tab_id, origin}]`; full URLs are not returned.
  Sign-in pop-ups (Google, Microsoft, Apple, GitHub login, LinkedIn OAuth) keep
  native behaviour because they report back to their opener.
- `alert`/`confirm`/`prompt` raised during an act are answered (`dialog`:
  `dismiss` by default, or `accept`) and reported under `dialogs`.
- `act` `scroll` (down/up/top/bottom/into_view) scrolls the target's container or
  the page; `open_frame` opens a frame's page (e.g. a cross-origin embedded ATS
  form) in the agent window. Snapshot frames list `origin` and `ref`.
- Snapshot `text_length`/`next_text_offset` and the `text_offset` parameter page
  long text. `upload(via_drop=True)` delivers files to a drop area by drag events.

### Real-Firefox end-to-end check

`uv run --frozen --with selenium python scripts/e2e_real_firefox.py` installs the
packaged addon temporarily into a throwaway headless Firefox (geckodriver), grants
its optional permissions through the popup, and drives the synthetic job site in
`tests/e2e_site` through the bridge on a private socket: pop-up routing, sign-in
exemption, dialogs, agent window, refs, keyboard, select/radio/checkbox,
contenteditable, shadow DOM, frames, hidden-input/picker/drop uploads, scroll
loading, text paging, screenshots, attention, navigate and close (35 checks).

## Several Firefox profiles

Each native host serves one socket at `$DRIMA_REGULAR_BROWSER_DATA/regular-browser.sock`
(default `~/.local/share/drima-browser`). Firefox passes its environment to the
host, so a second profile can use its own private directory, for example:

```sh
DRIMA_REGULAR_BROWSER_DATA="$HOME/.local/share/drima-browser/work" firefox -P work
```

Give the MCP server the same variable (for example an `env` entry in the client's
MCP configuration). Install the addon in each profile that should be reachable.

Profiles opened from Firefox's profile switcher do not get that variable. For
those, the host reads the profile directory Firefox was started with (its
`--profile` argument, or else the profile folder whose files Firefox holds open)
and looks it up in `~/.local/share/drima-browser/profile-routes.json`, which maps a profile
directory name to a subdirectory:

```json
{"Tez9hEU9.Profile 4": "work"}
```

Unlisted profiles use the default directory. Only one running profile can own a
directory; a second one gets a "Reconnect native host" error naming the route file.
