import json
from datetime import date

import pytest
import yaml

from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import Listing


@pytest.fixture
def config(tmp_path):
    config = Config(tmp_path, {"discord": {"enabled": False}, "gmail": {"enabled": False}, "ai": {"enabled": False},
                              "browser": {"headless": True, "timeout_ms": 3000}, "application": {"delay_seconds": 0}})
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
