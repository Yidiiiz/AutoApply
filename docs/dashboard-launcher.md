# Start the dashboard from Chrome (Windows)

The extension shows **☾ Start dashboard** when its local dashboard is offline.
Click it once; connection checks continue during startup, including startups
taking two minutes. The action disappears when the dashboard responds. If the
dashboard needs pairing, open dashboard Settings and copy its extension key.
Starting the dashboard does not resume autofill, writing or submission.

## One-time setup

Use this repository's existing `.venv` with its installed requirements. Load the
unpacked `extension/` directory in Chrome, then copy its 32-character ID from
`chrome://extensions`. From the repository, run:

```powershell
.\.venv\Scripts\python.exe -m autoapply.dashboard_launcher install --extension-id <extension-id>
```

Replace `<extension-id>` with that ID (without angle brackets). Reload the
extension after updating it so Chrome grants the `nativeMessaging` permission.
Reload open application tabs to refresh their sidebar. **Extension options**
also offers Start dashboard, including before pairing.

Setup registers only
`HKCU\Software\Google\Chrome\NativeMessagingHosts\com.autoapply.dashboard`
and creates the host manifest and quoted command wrapper in
`data/private/launcher/`. No administrator access, Chrome profile edits, extension
key changes, scheduled tasks or startup services are used. Repeating setup keeps
previously allowed extension IDs for this installation; repeat `--extension-id`
to add another ID. A registration or file that does not match this installation
is refused rather than overwritten. Repository paths containing `%`, `!`, quotes
or newlines are unsupported by the Windows command wrapper.

## Capability and recovery

The background worker accepts this action only from its own sidebar or options
page. The native host verifies Chrome's caller origin against its installed
allowlist. Its only input is exactly `{"action":"start_dashboard","port":8765}`,
with a port from 1024 through 65535. It accepts no executable, shell command,
arguments, URL, working directory, pairing key or applicant data. Start supports
HTTP `127.0.0.1` or `localhost` URLs with no path, credentials, query or fragment;
other dashboard URLs continue to support normal pairing but cannot launch locally.

The host starts only this installation's `.venv\Scripts\python.exe`, running
`-m autoapply dashboard --no-browser --port <port>` from the repository. The child
is detached with Windows job breakaway, has no console, receives no native-message input, and writes output
to `data/private/launcher/dashboard.log`. The host responds and exits promptly.
The existing dashboard process lock reuses an existing instance. A bounded
record of the spawned child's PID and creation time prevents repeated parents
while it starts. The host checks that record with query-only Windows process
handles; exited children, recycled PIDs and corrupt records do not block a new
explicit start. No process is killed. The UI reports readiness only after an
HTTP response, never from the native acknowledgement alone.

If setup is missing, the extension displays the install command with its current
ID. If startup fails or stays offline, check the launcher log and `.venv`, check
the configured port, and retry when the Start button is enabled. A stopped or
crashed dashboard can be started immediately after it is detected offline,
including in the same Chrome session. There is no saved browser launch lease.
During slow startup the button stays disabled for up to 150 seconds; read-only
connection checks continue afterward. Pairing rejection and HTTP busy responses are displayed separately and
do not offer Start. After moving the repository, uninstall from the old location
before installing from the new location. Uninstall with:

```powershell
.\.venv\Scripts\python.exe -m autoapply.dashboard_launcher uninstall
```

Uninstall removes only the owned registry key, manifest and wrapper. Logs and
other private files are preserved. Foreign or modified registrations/files need
manual review; the helper will not remove them.

## Verification

Offline tests mock process creation, Windows process liveness and the registry,
using temporary files only. They cover concurrent starts, recent child exit,
PID reuse, corrupt markers and restarts across browser/worker sessions:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_dashboard_launcher.py tests/test_extension_launcher.py -m "not browser"
```

The two `browser` cases in `tests/test_extension_launcher.py` exercise actual MV3
pages with fixture-only HTTP responses and a stubbed native call. A separate
Windows integration gate must install the observed extension ID and verify real
Chrome native IPC: start from offline, click twice, wait for HTTP readiness, and
confirm the dashboard survives native-host and Chrome exit without a terminal.
Then stop only the owned test dashboard and verify an immediate explicit start
works both in a fresh Chrome context and after ready-to-offline in the same context.
Offline tests cannot establish those OS/Chrome lifetime guarantees.
