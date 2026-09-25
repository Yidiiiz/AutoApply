import copy
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def read_yaml(path):
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected YAML mapping: {path.name}")
    return value


class Config:
    def __init__(self, root=ROOT, override=None):
        self.root = Path(root).resolve()
        self.private = self.root / "data/private"
        self.private.mkdir(parents=True, exist_ok=True)
        self.data = merge(read_yaml(ROOT / "config/config.example.yaml"), read_yaml(self.private / "config.yaml"))
        if override:
            self.data = merge(self.data, override)
        from .freshness import window
        window(self.data["jobs"]["max_listing_age_days"])
        from .cursor.types import CursorConfig
        CursorConfig(**self.data.get("cursor", {}))
        for section, key in [("jobs", "max_listing_age_days"), ("processing", "max_retries"),
                             ("processing", "max_applications_per_day"), ("browser", "timeout_ms"),
                             ("discovery", "max_results_per_query"), ("discovery", "max_pages_per_query"), ("application", "max_pages"), ("application", "confirmation_timeout_seconds")]:
            if type(self.data[section][key]) is not int or self.data[section][key] < 1:
                raise ValueError(f"{section}.{key} must be a positive integer")
        for section, key in [("application", "auto_submit"), ("browser", "headless"), ("ai", "allow_paid"),
                             ("discord", "enabled"), ("gmail", "enabled"), ("ai", "enabled")]:
            if type(self.data[section][key]) is not bool:
                raise ValueError(f"{section}.{key} must be a YAML boolean")
        if not 0.9 <= self.data["application"]["min_confidence"] <= 1:
            raise ValueError("application.min_confidence must be between 0.9 and 1")
        if self.data["application"]["delay_seconds"] < 0 or any(v <= 0 for v in self.data["polling"].values()):
            raise ValueError("Polling must be positive and application delay nonnegative")

    def __getitem__(self, key):
        return self.data[key]

    @property
    def profile(self):
        return read_yaml(self.private / "profile.yaml")

    @property
    def resume(self):
        return self.private / "resumes/resume.pdf"

    def setup_issues(self):
        import os
        profile = self.profile
        required = ["identity.first_name", "identity.last_name", "contact.email", "contact.phone",
                    "education.school", "education.degree", "education.major", "education.graduation_date",
                    "citizenship.us_citizen", "work_authorization.us_authorized",
                    "work_authorization.sponsorship_now", "work_authorization.sponsorship_future"]
        issues = [f"Missing profile: {key}" for key in required if fact(profile, key) in (None, "")]
        # False is a verified answer, not a missing value.
        if not self.resume.exists():
            issues.append("Missing resume: data/private/resumes/resume.pdf")
        if self["discord"]["enabled"]:
            for key in ["DISCORD_BOT_TOKEN", "DISCORD_USER_ID"]:
                if not os.getenv(key):
                    issues.append(f"Missing environment variable: {key}")
        if self["gmail"]["enabled"] and not (self.private / "oauth/gmail-token.json").exists():
            issues.append("Gmail is not authorized; run gmail-auth after providing OAuth client JSON")
        if not (self.private / "browser_profile").exists():
            issues.append("Browser profile not initialized; run login")
        if self["ai"]["enabled"] and not self["ai"]["providers"]:
            issues.append("No AI provider configured; writing will request user input")
        return issues


def fact(profile, path):
    value = profile
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def setup_logging(config):
    folder = config.root / "logs"
    folder.mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler(folder / "autoapply.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", handlers=handlers)
    for name in ["httpx", "httpcore", "googleapiclient", "discord"]:
        logging.getLogger(name).setLevel(logging.WARNING)
