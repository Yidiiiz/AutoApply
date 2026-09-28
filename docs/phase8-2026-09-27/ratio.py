"""Architecture counts using existing repository fixtures; no live services."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
import pytest
from conftest import config as config_fixture, listing as listing_fixture
from test_narratives import grounded as grounded_fixture
from autoapply.ai import AIManager, ProviderUnavailable
from autoapply.answers import AnswerResolver, is_writing_question
from autoapply.database import Database
from autoapply.models import Question


async def main():
    with tempfile.TemporaryDirectory(prefix='phase8-ratio-') as folder, pytest.MonkeyPatch.context() as patch:
        config = config_fixture.__wrapped__(Path(folder))
        config.data['application']['auto_submit'] = False
        listing = listing_fixture.__wrapped__()
        db = Database(config.private/'test.sqlite3')
        _, requests = grounded_fixture.__wrapped__(config, db, listing, patch)
        db.ingest(listing, config)
        app = db.application(1)
        resolver = AnswerResolver(config, db)
        manager = AIManager(config, db, None, resolver)
        questions = [Question(str(i), label, 'text') for i, label in enumerate(
            ['First name', 'Last name', 'Email', 'Phone number', 'School', 'Degree', 'Major', 'Graduation date'])]
        questions += [Question('citizen', 'Are you a US citizen?', 'select', True, ['Yes','No']),
                      Question('sponsor', 'Sponsorship now', 'select', True, ['Yes','No']),
                      Question('privacy', 'Privacy consent', 'select', True, ['Yes','No']),
                      Question('legal', 'Describe your legal history', 'textarea', True),
                      Question('unknown', 'Unknown applicant identifier', 'text', True),
                      Question('education', 'Describe your education', 'textarea'),
                      Question('company', 'Why this company?', 'textarea'),
                      Question('role', 'Why are you interested in this role?', 'textarea')]
        counts, rows = Counter(), []
        for q in questions:
            answer = resolver.resolve(q, app)
            outcome = 'deterministically_resolved' if answer else 'user_input_required'
            if not answer and is_writing_question(q):
                try:
                    answer = await manager.draft(q, app)
                    outcome = 'narrative_template' if answer.source == 'narrative_template' else 'provider_generated'
                except ProviderUnavailable:
                    pass
            counts[outcome] += 1
            rows.append(dict(question=q.label, outcome=outcome))
        counts['provider_calls'] = len(requests)
        result = dict(counts=counts, fields=rows, meaning='Synthetic architecture counts, not a quality score')
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(counts, indent=2))
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
