"""Inspect the Git index, including staged contents, before publishing or committing."""
import re
import subprocess
from pathlib import Path

FORBIDDEN = re.compile(r"(^|/)(data|logs|browser_profile|oauth|application_history|\.venv[^/]*|\.auth)(/|$)|(^|/)\.env(?:\..*)?$|\.(?:pdf|docx?|db|sqlite\d*|png|jpe?g|har)$", re.I)
SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[\w-]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|[\w-]{24,}\.[\w-]{6}\.[\w-]{25,})")
ASSIGNMENT = re.compile(r"(?:DISCORD_USER_ID|DISCORD_BOT_TOKEN|password|client_secret|refresh_token|api_key)[ \t]*[=:][ \t]*['\"]?([\w.-]{8,})", re.I)


def privacy_check(root, include_untracked=True):
    root = Path(root).resolve()
    base = ["git", "-c", "safe.directory=" + root.as_posix(), "-C", str(root)]
    result = subprocess.run(base + ["ls-files", "-z"], capture_output=True, check=True)
    findings = []
    tracked = {name for name in result.stdout.decode("utf-8").split("\0") if name}
    names = set(tracked)
    if include_untracked:
        untracked = subprocess.run(base + ["ls-files", "--others", "--exclude-standard", "-z"], capture_output=True, check=True)
        names.update(name for name in untracked.stdout.decode("utf-8").split("\0") if name)
    for name in sorted(names):
        if not name:
            continue
        if FORBIDDEN.search(name) and not name.endswith(".env.example"):
            findings.append("Private file is in Git index: " + name)
        blob = subprocess.run(base + ["show", ":" + name], capture_output=True, check=True).stdout if name in tracked else (root / name).read_bytes()
        if len(blob) > 2_000_000:
            findings.append("Large tracked file needs manual privacy review: " + name)
            continue
        text = blob.decode("utf-8", errors="replace")
        if SECRET.search(text) or ASSIGNMENT.search(text):
            findings.append("Possible secret in publishable content: " + name)
    return findings
