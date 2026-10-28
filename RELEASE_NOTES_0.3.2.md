Upload buttons that open a file chooser: some sites, such as LinkedIn Easy Apply, have no file field until you click "Upload resume", and Firefox never opens a file chooser for an automated click. For one upload the client explicitly requests, the extension now clicks that button and gives the chosen local files directly to the file field the site asks to open, with no dialog. Drop areas that accept files only by drag and drop are supported the same way. Only the files named in that request are attached, and the page's own behavior is restored right afterward.

Links that open a new window: when an automated click makes a site open a new window or tab (for example "Apply on company website"), Firefox blocks it as a pop-up. The extension now opens those pages in the agent window instead. The client only learns the new tab and the website's domain, never the full address. Sign-in pop-ups (Google, Microsoft, Apple, GitHub, LinkedIn) keep Firefox's normal behavior. This works only in automatic access mode.

Page dialogs: "Are you sure?" alerts and confirmations raised by an automated click no longer freeze the tab. They are answered with Cancel unless the client explicitly asks to accept, and the client sees the dialog text.

Also new: scrolling lists that load more results as you scroll, reading very long pages in parts, and opening application forms embedded from another website in their own tab, again without revealing their full address to the client.

Everything else is unchanged from 0.3.1. Existing website approval, password-field filtering, strict single-target matching and single-shot actions remain in place.
