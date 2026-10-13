# AutoApply Chrome extension

1. Open `chrome://extensions` in Chrome and enable **Developer mode**.
2. Choose **Load unpacked** and select this repository's `extension/` directory.
3. Open the extension's **Details → Extension options** (or **Pairing settings** in the in-page sidebar).
4. Enter the dashboard URL (default `http://127.0.0.1:8765`) and copy the Chrome extension pairing key from dashboard **Settings → Dashboard**.
5. Choose **Save**, then **Test connection**. Your profile name should appear.
6. Open an application form, click the extension toolbar action to open its in-page sidebar, and choose **Autofill this page**. Use **Alt+Shift+S** to toggle the sidebar or **Alt+Shift+F** to open it and autofill; change it at `chrome://extensions/shortcuts` if needed.

Review the filled values, orange required fields, and the **Needs your input** / **Needs writing** lists. Existing values and attached files are preserved. The extension never clicks submission or navigation buttons; final submission is manual. Saved profile answers fill instantly; written questions are drafted by the AI writer, cover letters are generated and attached when an upload field exists (otherwise use Generate cover letter), and missing answers are asked in the in-page sidebar and remembered. The in-page sidebar also has an **Ask AI** answer helper (refine, copy, back/forward between versions) and a chat box. From the dashboard queue, select jobs and choose **Run in Chrome (N)** to open, fill and move through them one by one. On Workday, sign in when asked; each later step is filled automatically after you continue.

After installing or reloading the extension, reload any application tabs that were already open. Chrome's internal pages and other restricted URLs cannot be scanned. The all-sites permission allows scanning forms across ATS hosts and embedded frames; scanning starts only when you request autofill, and API requests use your paired server. Pair only with a server you trust: answers and resume bytes come from that server.

The extension uses plain JavaScript modules with no build step, dependencies or remote scripts. The background service worker calls the content script in its isolated world in every accessible frame, with frame-prefixed keys to keep identical field IDs separate. Last results and sidebar visibility live per tab in session storage. The Answers tab supports inline changes; your own edits on the page are learned automatically, excluding private input types and essays. Confirmed manual submission marks the matched listing as applied and offers Undo.

Comboboxes are opened during scanning to discover their available choices. Autofill waits up to six seconds for search options to appear and settle, prefers the exact full answer, and retries a shorter search prefix when needed. It checks that the selection committed; failed searches are cleared and listed under **Needs your input**. Hidden Resume/CV upload controls use their field labels and attributes instead of generic Attach/Upload button text, and their displayed filename is checked after attachment. Cover-letter upload fields remain listed under **Needs writing**. Education start/end month and year use the profile's `education.start_date` and `education.graduation_date` when the field is in an Education section.

Local fixture verification (no real dashboard or employer requests):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp/pytest-extension tests/test_extension.py
```
