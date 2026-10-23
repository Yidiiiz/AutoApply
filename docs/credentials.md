# Private reusable account credentials

In the local dashboard, open **Settings → Account credentials**. Enter your standard
employer account password and optionally a private email override, then save.
Without an override, account autofill uses the current profile's `contact.email`.
The password input is masked and write only. Settings returns configured status,
never the password or private email. Clear removes the saved credentials file.
The extension pairing key is separate from the employer account password.

Alternatively, put `AUTOAPPLY_LOGIN_PASSWORD` and optionally
`AUTOAPPLY_LOGIN_EMAIL` in the fixed repository-root `.env`, or the dashboard
process environment. Precedence for each variable is **process environment →
root `.env` → saved Settings**. An explicitly empty password disables autofill;
an empty email uses the profile email. Clear cannot remove environment variables;
Settings reports when these override saved values. No `.env.*` files are loaded.

The small reader recognizes only those two exact variable names. Each assignment
occupies one line. Everything after the first `=` is literal, including spaces,
quotes, backslashes, dollar signs, `#`, `%`, and backticks. Do not wrap values in
shell quotes, add inline comments, use `export`, or expect substitutions/escapes.
It never executes the file. Values must be single line; password limit is 4096
characters and private email limit is 254. Never paste credentials into chat.

Saved values live only in ignored `data/private/credentials.json`, separate from
profile, answers, config, history and backups. Replacement is atomic; temporary
files receive owner-only permissions (Windows protected ACL, POSIX mode 0600)
before any secret is written. A failed replacement preserves the prior file.
The file contains plaintext for local autofill; the local OS account can read it.
Protect your `.env` permissions yourself. No credential source is added to the
profile loaded by AI writers.

On a supported application auth form, press **Autofill**. An active job run can
fill once the form appears, then waits for you. Review and click **Sign in** or
**Create account** yourself; normal application autofill resumes after auth while
the existing form wait is active (60 seconds; press Autofill again afterward).
The extension never activates auth submission, agreements, OAuth or CAPTCHA.
Occupied inputs are preserved. A different occupied email prevents password fill;
confirmation is filled only during account creation when the primary password
matches the configured password.

Credential release requires the paired extension origin/key, a bounded POST,
an existing saved application and HTTPS on the same ATS origin in every frame.
The page must match the saved job, or belong to its currently opened run with the
correct run ID. Supported ATS hosts: Workday, Greenhouse, Lever, SmartRecruiters,
Pinpoint, JazzHR, iCIMS, Taleo and SuccessFactors. A URL or arbitrary application
ID alone cannot authorize a different login site. Cross-origin identity providers,
generic sites, unsupported/ambiguous forms, password reset/change, current/old
password change fields, OTP and CAPTCHA forms require manual entry. Login forms
may use the normal `current-password` autocomplete hint. The detector requires a
visible editable password, account context and exactly one email/username field;
it supports one password and an optional clearly labelled signup confirmation.

Credentials go from the dedicated response directly into the isolated frame via
native input setters and input/change events, without extension messages, normal
answers, session/local storage or result values. Auth documents are excluded from
scan fields, diagnostics, snapshots, chat and learning; marked controls remain
excluded through SPA transitions. Configured private values are also redacted
before other extension routes reach persistence or AI. Authentication forms must
be detectable as such; pages and local code with access to a filled input can read
it, as with manual typing. No live employer services are needed for verification:

```
python -m pytest tests/test_credentials.py -q
```

The browser test loads the actual unpacked MV3 extension with synthetic values,
local fixture pages and stubbed fetch responses. It performs no auth submission
or employer/provider requests. Browser availability is reported separately from
backend test results.
