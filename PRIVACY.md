# MCP Browser Bridge privacy notice

The extension connects HTTP/HTTPS website tabs to a companion native application
on your computer. Manual per-tab approval is the default. Approval ends when
revoked, when the tab closes, when Firefox restarts, or when the tab leaves its
approved origin.

You can explicitly enable automatic access to all regular website tabs in the
toolbar popup after acknowledging the warning and granting Firefox's optional
website permissions. This includes existing and future tabs and persists across
restarts while those permissions remain granted. Switching to manual mode stops
broad access and removes the optional host permissions. Private windows, internal
pages and Firefox-protected pages are not supported. No built-in tab-count limit
is imposed; local resources and protocol message-size limits still apply.

Approved tab titles and URLs (without query strings), visible page text and form
values can be transferred to the local application and its connected MCP client.
This can include your name, contact details, location, profile, messages, search
terms, health or financial information, and authentication information visible in
page text. Requested form actions and their results also pass through
the bridge. Approving a tab permits the connected client to read and interact with
that tab; it is not a read-only permission.

When the local MCP client explicitly requests a file upload, the companion reads
only the named absolute local files and sends their bytes and base filenames in
bounded chunks to the approved tab. Absolute paths are not sent to the extension
or website. The bridge keeps staged bytes in memory until attachment, cancellation,
page navigation or five-minute expiry (cleaned up on the next upload operation).
Selecting files fires the website's normal upload events, which may immediately
send those files to the website's server. File contents may include any personal
data categories disclosed above. The extension does not browse local directories,
choose files autonomously or provide website scripts with filesystem access.
Only the trusted local MCP client names the files; a web page cannot request them.

The extension has no analytics or external network endpoint. It does not read
cookies or website storage. Snapshots exclude password inputs, hidden inputs and
controls identified as token, secret or verification-code fields. This filtering
does not guarantee that page text contains no sensitive information.

The extension keeps manual approvals in memory and stores only the selected mode
in extension-local settings. It does not read website storage or general browsing history. Its companion
application relays messages locally. Your chosen MCP/AI client may retain page
content or send it to its service provider: consult that client's privacy settings
and policy before approving sensitive tabs. No claim of end-to-end local-only AI
processing is made.

Source and issue reports: https://github.com/Drima-code/mcp-browser-bridge

## Optional download tracking

Download tracking requires a separate, optional Firefox `downloads` permission,
requested only by your click in the extension popup. It is disabled by default.
The MCP client cannot grant that permission. Revoke it with the popup button.

When the MCP client arms a watch for an approved website and an explicit filename,
the extension observes future download events. It filters out private downloads,
other origins, older downloads and other filenames. It does not enumerate past
or global download history. The browser API does not identify the source tab;
matching by origin, filename and time is reported as a candidate association.
Missing provenance or ambiguous matches produce counts without file metadata.

A matching record may transfer its basename, byte counts, completion/interruption
state, error code and absolute saved path to the local MCP/AI client. This path
can contain personal information; the website does not receive it. Download URLs,
referrer query strings and file contents are not transferred by this observer.
Only already-observed download IDs are refreshed. The bridge never starts, cancels,
retries, opens or deletes a download through this feature.

Watches accept new candidates for five minutes; memory records are discarded on
restart or after ten minutes when the next operation/event cleans them up. Website
navigation, tab closure, website-access revocation and download-permission
revocation invalidate observation. Existing MCP-client retention policies apply
separately to data already transferred to that client.


## Embedded controls and tab activation

On explicit client requests, the bridge can inspect and act within visible frames
of the approved tab's own origin. A parent approval does not authorize embedded
sites of other origins. Open shadow roots are inspected as part of approved page
content; closed roots are inaccessible. Snapshots also return frame accessibility,
document identity, readiness and active tab/window identifiers. An explicit focus
request activates an approved tab and brings its Firefox window forward. Wait
requests only observe a named control for up to ten seconds; they do not repeat
website actions. These features require no additional permissions.
