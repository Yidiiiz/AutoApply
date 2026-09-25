import base64
import email.utils
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def oauth_login(config):
    from google_auth_oauthlib.flow import InstalledAppFlow
    folder = config.private / "oauth"
    folder.mkdir(exist_ok=True)
    client = folder / "gmail-client.json"
    if not client.exists():
        raise ValueError("Place a Desktop OAuth client JSON at data/private/oauth/gmail-client.json")
    credentials = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES).run_local_server(port=0)
    (folder / "gmail-token.json").write_text(credentials.to_json(), encoding="utf-8")


def message_text(payload):
    parts = [payload] + list(payload.get("parts", []))
    result = []
    for part in parts:
        if part is not payload and part.get("parts"):
            result.append(message_text(part))
        if part.get("mimeType") not in {"text/plain", "text/html"}:
            continue
        data = part.get("body", {}).get("data", "")
        if data:
            result.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"))
    return "\n".join(result)


def extract_verification(messages, sender_domains, link_domains, since):
    """Ambiguous messages, codes or links produce no automatic choice."""
    candidates = []
    for message in messages:
        received = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, timezone.utc)
        if received < since:
            continue
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        sender = email.utils.parseaddr(headers.get("from", ""))[1].split("@")[-1].lower()
        if sender not in sender_domains or not re.search(r"verif|one.time|sign.in|authentication|security code", headers.get("subject", ""), re.I):
            continue
        body = message_text(message.get("payload", {}))
        text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
        codes = set(re.findall(r"(?:code|otp|one.time password)\s*(?:is|:)?\s*(\d{4,8})\b", text, re.I))
        links = set()
        soup = BeautifulSoup(body, "html.parser")
        raw_links = [a["href"] for a in soup.find_all("a", href=True)] + re.findall(r"https://[^\s<>\"]+", body)
        for url in raw_links:
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.hostname in link_domains and not parsed.username and re.search(r"verif|confirm|magic|authenticate", parsed.path, re.I):
                links.add(url)
        if len(codes) == 1 and len(links) <= 1:
            candidates.append({"message_id": message["id"], "code": next(iter(codes)), "link": next(iter(links), None)})
        elif len(links) == 1 and not codes:
            candidates.append({"message_id": message["id"], "code": None, "link": next(iter(links))})
        else:
            candidates.append(None)
    return candidates[0] if len(candidates) == 1 else None


class Gmail:
    def __init__(self, config):
        self.config = config

    def verification(self, application_url, since):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        host = urlsplit(application_url).hostname
        senders = self.config["gmail"]["sender_domains"].get(host, [])
        if not self.config["gmail"]["enabled"] or not senders:
            return None
        if any(not re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", s) for s in senders):
            raise ValueError("Gmail sender domains must be exact domain names")
        token = self.config.private / "oauth/gmail-token.json"
        if not token.exists():
            raise ValueError("Gmail OAuth session missing")
        credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token.write_text(credentials.to_json(), encoding="utf-8")
        query = f"after:{int(since.timestamp())} " + "{" + " ".join("from:(@" + s + ")" for s in senders) + "}"
        with build("gmail", "v1", credentials=credentials, cache_discovery=False) as service:
            response = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
            # More than one page is too broad to resolve safely.
            if response.get("nextPageToken"):
                return None
            messages = [service.users().messages().get(userId="me", id=m["id"], format="full").execute() for m in response.get("messages", [])]
        return extract_verification(messages, senders, [host], since)
