import asyncio
import pytest
from autoapply.database import Database
from autoapply.engine import Engine
from autoapply.submission_probe import SubmissionProbe, SubmitObstructed

def test_retired_application_blocks_before_database_or_browser_work():
    db=Database.__new__(Database)
    db.setting=lambda key,default=None: {'permanent':True} if key=='duplicate_submission_guard:6401' else default
    assert db.claim(6401) is None
    with pytest.raises(ValueError,match='permanently excludes'):db.retry(6401)
    engine=Engine.__new__(Engine);engine.db=db
    assert asyncio.run(engine._process_one(6401,preserved_page=object())) is False
    with pytest.raises(ValueError,match='permanently excludes'):asyncio.run(engine.resume_manual(6401))
    probe=SubmissionProbe.__new__(SubmissionProbe);probe.db=db;probe.app_id=6401
    with pytest.raises(SubmitObstructed,match='permanently excludes'):asyncio.run(probe.physical_click())
