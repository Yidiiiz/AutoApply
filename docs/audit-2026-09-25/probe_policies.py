"""Reproduce two audit findings using synthetic temporary records only."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from autoapply.answers import AnswerResolver
from autoapply.config import Config
from autoapply.database import Database
from autoapply.field_mapping import FieldMapper
from autoapply.models import Listing, Question, now


async def main():
    with tempfile.TemporaryDirectory(prefix='autoapply-policy-audit-') as folder:
        config = Config(folder, {'ai': {'enabled': False}, 'discord': {'enabled': False}, 'gmail': {'enabled': False}})
        (config.private / 'profile.yaml').write_text('contact:\n  email: synthetic@example.test\n', encoding='utf-8')
        db = Database(config.private / 'audit.sqlite3', startup_maintenance=False)
        try:
            with db.ingest_batch():
                for ident in ('distinct-one', 'distinct-two'):
                    db.ingest(Listing('Synthetic Employer', 'Software Intern', 'New York, NY',
                        'https://jobs.lever.co/audit/' + ident, 'synthetic', posted_at=now()), config)
            db.transition(1, 'SUBMITTED', confirmation_text='Synthetic fixture confirmation', submitted_at=now())
            conflict = db.submission_conflict(2)
            mapping = await FieldMapper('smartrecruiters').map({'label':'Email', 'autocomplete':'given-name', 'kind':'email'})
            answer = AnswerResolver(config, db).resolve(Question('email', 'Email', 'email', semantic_key=mapping['semantic_key']), db.application(2))
            print(json.dumps({'distinct_requisitions_same_title': {'application_ids':[1,2], 'conflict_id':conflict['id'] if conflict else None},
                'conflicting_field_signals': {'mapper_result':mapping, 'resolver_supplied_answer':answer is not None,
                                              'answer_source':answer.source if answer else None}}, indent=2))
        finally:
            db.close()


if __name__ == '__main__':
    asyncio.run(main())
