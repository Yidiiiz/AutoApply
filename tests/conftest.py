import json
import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import Listing


@pytest.fixture(autouse=True)
def legacy_answer_policy(request, monkeypatch):
    """Existing policy tests assume explicit confirmation; opt in with @pytest.mark.affirmative."""
    if not request.node.get_closest_marker("affirmative"):
        monkeypatch.setattr("autoapply.affirmative.ENABLED", False)


@pytest.fixture(autouse=True)
def offline_liveness(request, monkeypatch):
    """Tests never probe employer listings over the network."""
    if not request.node.get_closest_marker("liveness"):
        monkeypatch.setattr("autoapply.liveness.listing_state", lambda url, timeout=15: "OPEN")


@pytest.fixture(autouse=True)
def local_test_browser(monkeypatch):
    """Use the existing repo installation for temporary test profiles only."""
    installed = Path(__file__).resolve().parents[1] / "data/private/playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and installed.is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(installed))


@pytest.fixture
def config(tmp_path):
    config = Config(tmp_path, {"discord": {"enabled": False}, "gmail": {"enabled": False}, "ai": {"enabled": False},
                              # Legacy submission-flow tests explicitly opt in;
                              # production configuration defaults auto-submit off.
                              "browser": {"headless": True, "timeout_ms": 3000}, "application": {"delay_seconds": 0, "auto_submit": True}})
    profile = {"identity": {"first_name": "Test", "last_name": "Student"},
               "contact": {"email": "student@example.test", "phone": "2025550100"},
               "education": {"degree": "Bachelor's", "major": "Computer Science", "school": "Example University",
                             "graduation_date": "2028-05-01", "currently_enrolled": True},
               "citizenship": {"us_citizen": True},
               "work_authorization": {"us_authorized": True, "sponsorship_now": False, "sponsorship_future": False}}
    (config.private / "profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    config.resume.parent.mkdir(parents=True)
    config.resume.write_bytes(b"%PDF-1.4\n% local fixture only\n%%EOF\n")
    return config


@pytest.fixture
def db(config):
    value = Database(config.private / "test.sqlite3")
    yield value
    value.close()


@pytest.fixture
def listing():
    return Listing("Example Company", "Software Engineering Intern Summer 2027", "New York, NY",
                   "https://jobs.lever.co/example/abc-123", "fixture", posted_at=date.today().isoformat(),
                   description="Software engineering internship for undergraduate students.")
