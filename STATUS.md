# Document discovery and control update — 2026-10-22

Prepared 0.2.6. Adds explicit approved-tab/window activation, active/window metadata,
visible frame inventories and same-origin frame_path descent (up to eight levels).
Cross-origin, hidden/inert and unavailable embedded documents are rejected.
Open shadow controls are discovered with strict host >>> target selectors; scopes,
actions and uploads share this addressing. Closed roots remain inaccessible.
Visibility/disabled/ARIA ancestry crosses shadow hosts. Form events cross open
shadow boundaries. Upload staging remains a single 25 MiB area across all frame
contexts, tied to its originating document, with a single non-replayed commit.

Snapshots return document_id and readiness. Optional document_id guards reject
replacement, including same-URL frame reloads; they do not promise DOM stability.
Read-only waits observe one target for 0..10 seconds and return matched:false on
expiry. No action replay. Transport loss now gives an explicit unknown-outcome
error, including uploads; capability checks reject unsupported optional arguments.
Removed duplicate content-script injection per operation. Permissions/IDs unchanged.

Validation: full 33-test suite passes; the added frame/shadow upload regression
brings coverage to 34 tests, with all seven viewer tests passing. Installed-addon
smoke in a disposable Firefox profile verifies tab activation, same-origin frame
fill, shadow fill, wait, stale-document rejection, plus existing upload SHA256,
exactly one submission and a completed synthetic download verified on disk.
Ruff, JavaScript syntax, diff and Mozilla lint pass with zero lint notices/warnings.
All fixtures are synthetic. No live Gmail action or completed email collection
is claimed. Unsigned artifact: artifacts/mcp-browser-bridge-0.2.6.zip.

Sign/install the same-ID update, reload website tabs and reconnect MCP discovery
before using it in normal Firefox. This includes 0.2.4/0.2.5 changes. Resume the
private receipt workflow only after installation; its progress stays outside git.

# Protocol negotiation and download observation — 2026-10-22

Prepared 0.2.5. Added status reporting for server/addon versions, protocol,
capabilities and download permission. MCP and diagnostic actions negotiate before
sending the requested operation. The background also checks each website's loaded
content listener, rejecting stale code with an explicit reload instruction. No
fallback or automatic replay of an uncertain mutation.

Added future-only download watches, enabled separately through Firefox's optional
`downloads` permission in the popup. An approved tab and explicit expected basename
are required. Candidates match referrer origin, filename (including numbered
variants) and time; Firefox does not provide a source-tab ID. Ambiguous/missing
provenance yields counts without paths. Records expose bytes, completion,
interruption/error and a usable saved path, with oversized paths explicitly omitted.
No transfer is started, retried, cancelled, opened or deleted by this feature.
No global/past history enumeration; only observed IDs are refreshed. Watches and
metadata are bounded, expire, and are invalidated on navigation/access revocation.
Permission revocation during a metadata refresh blocks the reply too.

Validation: 31 tests covered by the full 30-test run plus the added popup consent
test; all 10 protocol/download tests pass after the final metadata-bound change.
Installed-addon smoke in a disposable Firefox profile observed a real synthetic
HTTP attachment complete, verified its bytes and path on disk, and also passed
handshake, upload SHA256/replay, dropdown, viewer hover and pagination checks.
Ruff, JS syntax, diff checks and Mozilla lint pass (zero errors/warnings/notices).
Read-only local status diagnosis confirms the installed addon cannot negotiate
the new protocol; no website action was sent. No live Gmail download is claimed.

Unsigned package: artifacts/mcp-browser-bridge-0.2.5.zip. Required base permissions
and extension/native host IDs unchanged. Optional downloads permission is new and
must be granted through the popup by the user. Install the Mozilla-signed same-ID
update, reload website tabs and reconnect MCP discovery. Includes the 0.2.4 fixes.

# Viewer control discovery — 2026-10-22

Prepared MCP Browser Bridge 0.2.4. Fixed two reproducible discovery defects:
aria-hidden was incorrectly treated as visual invisibility, and a hard 100-control
cutoff made later controls unreachable. Snapshots now prefer visible dialogs,
accept an explicit visible CSS scope, and expose bounded pagination and totals.
Large controls retain a forward offset when option metadata needs truncation.
Added synthetic hover without clicks, desktop cursor movement or new permissions.
Sensitive fields, inert/CSS-hidden targets, strict matching, access grants and
single-shot mutations retain their checks.

Validation: 21 tests pass, including four viewer regressions in real headless
Firefox. Installed-addon smoke in a disposable Firefox profile passes viewer
hover/ARIA/paging, binary SHA256 upload, replay rejection, custom dropdown selection
and exactly one synthetic form submission. Ruff, JavaScript syntax, diff checks
and Mozilla lint pass; lint has zero errors, warnings and notices. The additional
html-scope selector edge case is covered by the viewer tests.

Unsigned package: artifacts/mcp-browser-bridge-0.2.4.zip. Same extension/native
host IDs and permissions. Needs Mozilla signing, installation in normal Firefox,
website-tab reload and MCP rediscovery for the expanded arguments. This is a
synthetic reproduction and correction, not a claim that live Gmail downloads are
verified. The email collection remains incomplete pending addon installation and
continuation of the authorized task; its private progress is outside this repo.

# Repository split — 2026-10-21

Independent MCP Browser Bridge repository, preserving history through 92f52a1. Contains the Firefox addon, native host, MCP connector and synthetic tests. No dedicated browser runtime. Extension/native-host IDs and socket path preserved for installed-addon compatibility.

Validation: 17 bridge tests pass after namespace separation; Ruff, diff and package build pass. Real stdio discovery identifies MCP Browser Bridge, exposes four compatible tools, and lists existing Firefox tabs successfully. Codex and the Firefox native-host registration now point to this checkout. No extension code or signing identity changed.

Historical notes below describe the combined repository before separation.

# Status — 2026-10-05

## Custom dropdown fix — 2026-10-20

Prepared extension 0.2.3. Normal Firefox's `.click()`-only activation skipped
mouse-down handlers, so Greenhouse/React Select's Latin America field contained
typed “Yes” without a selected value and failed required-field validation.
Clicks now dispatch pointer/mouse down/up, honor cancellation of compatibility
mouse events and focus defaults, then activate exactly once. A target removed,
disabled, hidden, made sensitive or navigated away during those handlers stops
before activation, reports failure, and is never retried automatically.
Snapshots include ARIA-required, expanded, selected and controlled-list metadata.
Strict matching and existing permissions remain unchanged; synthetic events
do not bypass sites requiring trusted native input.

Validation: **36 tests passed**, including a regression that distinguishes typed
combobox text from selected state, focus preservation, pointer cancellation,
target changes and exactly one form activation. Installed-extension smoke in a
disposable Firefox profile passed custom dropdown selection and one synthetic
submission alongside binary upload SHA256/event/replay checks. Ruff, JavaScript
syntax, diff check and Mozilla lint passed (zero errors/warnings/notices).

Unsigned package: `artifacts/mcp-browser-bridge-0.2.3.zip`. Needs Mozilla signing
as a new version of the same extension ID, then installation in normal Firefox.
No signed update or live Cresteo fix is claimed yet. User specifically wants
normal Firefox tab 38; its current CV/contacts are filled, but LATAM dropdown
selection is pending. No live application was submitted as a regression test.

## Normal Firefox file upload — 2026-10-19

Implemented 0.2.2 upload through `regular_browser_upload` and diagnostic
`regular-call upload`. Explicit absolute regular-file paths only; max 10 files,
25 MiB combined. Native messages stay within 256 KiB using 96 KiB binary chunks.
Filename/MIME metadata only, never absolute paths, reaches the approved tab.
Every stage rechecks website authorization and document/target identity. Type,
multiple/disabled/directory inputs and incomplete/duplicate chunks are checked.
Only commit assigns FileList and emits input/change once; never replayed after a
lost reply. Hidden file inputs require visible associated labels. Iframes remain
unsupported. Privacy/popup disclosure and signing guide updated; no new permissions.

31 tests pass, plus a disposable installed-Firefox-extension smoke test validating
256,000 binary bytes by SHA256, one input/change event, and replay rejection.
Ruff/JavaScript syntax and existing native/authenticated transport smoke pass;
Mozilla lint reports no errors, warnings or notices. Python CLI is ready locally.
The user must sign/install 0.2.2 before normal Firefox gains upload support; no
signing or live application upload claimed. Refresh MCP discovery for the new tool,
or use the documented diagnostic CLI with the same checks.
## Regular Firefox release review — 2026-10-19

Prepared version 0.2.1 for Mozilla signing; no signed/installed release claimed.
Preserved and tested existing uncommitted rich-editor fix. Added snapshot CSS
selectors, widget labels/roles, state/filename metadata and bounded native replies.
Actions reject read-only, disabled, sensitive-label and disabled-option targets;
failed value/checkbox changes report errors. Navigation is rechecked in content
before actions. Popup reconnect restores native connection without retrying actions
or changing access grants. Manual/automatic consent boundaries remain intact.

28 tests pass, including reconnect, custom controls, controlled inputs, navigation
races, native framing and oversized snapshots. JavaScript syntax and Ruff pass.
Mozilla web-ext lint: zero errors, warnings or notices. Firefox minimum 142 avoids
Mozilla's cross-platform data-disclosure schema warning; Linux native host only.
Reproducible unsigned package in ignored artifacts/; PERMANENT_INSTALL.md provides
unlisted signing, same-ID updates, native-host and normal-Firefox MCP instructions.
Signing/download/install require the user's Mozilla/browser interaction.
## Rich-text fix — 2026-10-07

Live read-back exposed contenteditable DOM rewrites not updating an editor model.
Use Firefox's insertText/delete editing pipeline, retaining strict visible matching
and single-shot behavior, with no direct DOM fallback. Synthetic Firefox test
checks InputEvent-backed model updates, replacement, literal text and clearing.
Seven connector tests pass; Ruff and diff checks pass. Loaded temporary extension
must be reloaded before live verification; application remains unsubmitted.

## Access mode choices — 2026-10-07

Version 0.2.0 removes job-site allowlisting and supports manual per-tab approval
on regular HTTP/HTTPS sites by default. Packaged popup offers explicitly confirmed
automatic access to current/future website tabs via optional host permissions.
Automatic opt-in persists; manual switch/revocation stops broad access. Internal,
file and private pages excluded; Firefox protected-page restrictions retained.
Expanded truthful data-transfer disclosure and privacy notice. No tab-count cap;
existing resource/message-size limits remain. Live 0.2.0 install/AMO pending.
Seven connector tests passed, including popup consent/denial, all-site permission
revocation, persistence, private/internal exclusion and arbitrary-site approval.
Full release run: 26 tests passed in 34.13 seconds. Ruff and diff checks passed.

## Public Firefox listing preparation — 2026-10-07

David requested public Mozilla distribution and the name MCP Browser Bridge.
Updated display name and toolbar title; existing extension ID and minimal
permissions preserved. Source remains MIT in the public GitHub repository.
Mozilla upload/signing and listing approval are pending; not yet published on AMO.
AMO rejected the initial archive for missing data_collection_permissions. Version
0.1.1 declares approved-tab content and personal data transferred via native
messaging, requires Firefox 140+ built-in consent, and includes a privacy notice.

## Optional regular-Firefox connector

Added experimental activeTab + nativeMessaging connector for explicitly approved
Wellfound/Chiletrabajos tabs. Toolbar approval/revocation; leaving origin revokes.
Separate regular-stdio MCP and regular-call diagnostics; original MCP unchanged.
Private Unix socket; no cookies, OAuth pages, arbitrary evaluation or all-site
permissions. Top-frame snapshots and one strict action per call; manual uploads.
Six synthetic connector tests pass, including real native-host IPC roundtrip,
origin revocation, duplicate-match rejection and sensitive-control exclusion.
Full suite: 25 tests passed in 27.07 seconds; Ruff/lockfile/diff checks passed.
Original authenticated transport smoke test still passes.
Native host registered locally and separate MCP configured. Live regular-browser
use awaits user loading temporary extension and toolbar approval; not yet verified.

## Public distribution preparation

David authorized public GitHub distribution under MIT. Added license, package
metadata, clone instructions and Google sign-in troubleshooting. Reviewed tracked
files and all four existing commits for credential/personal-data patterns; no
credentials, browser profiles or personal screenshots found. Runtime state stays
outside git. Historical synthetic tests and transport checks rerun before release.
Release checks: 19 tests passed in 25.37 seconds; Ruff and lockfile checks passed;
authenticated HTTP/stdio transport, image forwarding and reconnect smoke test passed.
Google rejected the dedicated Firefox OAuth flow too; use supported site login
or a regular non-automated browser, never a security bypass.

The older notes below describe initial September verification, not current job
application state. Actual application receipts remain in the private job-search
repository and are not part of this public project's release materials.

Implemented 14 browser MCP tools using official MCP SDK and Playwright.
Default engine Firefox; optional Chromium, isolated persistent profiles.
Local authenticated HTTP service plus stdio proxy for Codex/Kimi; no extension.

## Verified

- 19 tests passed in 36.65s: Firefox/Chromium synthetic forms, uploads, downloads,
  strict locators, iframe fields, dialogs, cookies across restart, batch stopping,
  MCP image output and local authentication/Origin rejection.
- Transport smoke test passed using Firefox: authenticated HTTP, stdio discovery,
  screenshot forwarding, browser survives two client reconnects.
- Ruff checks passed. Matching Firefox 1538 and Chromium 1234 installed.
- User service installed and active; Codex and Kimi configuration registered.
  Blueprint disabled in Codex. Native tools appear after client reload; diagnostic
  CLI already uses real MCP to work in the current session.
- Dedicated visible Firefox opened GetOnBoard and returned a verified screenshot.
- Initial Chromium Google OAuth was rejected; Firefox email sign-in subsequently succeeded.
  No stealth or account-security changes. Existing regular Firefox profile untouched.
- Job-search handoff updated with current schema and bounded Kimi tasks.
- Dedicated headed Firefox windows are hidden from focus and normal Alt-Tab space
  shortly after launch using the local window manager when `DISPLAY` and `wmctrl`
  are available. The browser remains authenticated and can be brought forward for
  manual CAPTCHA or sign-in work.

## Remaining

Human email sign-in verified. Live GetOnBoard CV upload, Trix form filling and
Chosen dropdown selection verified. No real job application submitted or receipt
observed: STEUART requires a portfolio website, rejecting a GitHub profile URL.
Pointer actions later stalled at Firefox stability checks; verified enabled buttons
worked using normal keyboard Enter. Custom Chosen dropdown required its visible
search input rather than the hidden select. These observations are recorded in
the job-search skill; no force-click, hidden mutation or validation bypass used.
Kimi model-driven browser discovery is not yet tested and model alias is not
confirmed as 2.8. Application-specific skill remains pending live success.

Repository is prepared for public distribution. Browser data lives outside git under
`~/.local/share/drima-browser`. Do not publish tokens, profiles or personal evidence.
