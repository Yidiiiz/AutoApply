"""Offline factual-resolution counters. Never opens live storage/browser/provider."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import yaml
import autoapply.config as config_module
import autoapply.field_mapping as mapping
from autoapply.answers import AnswerResolver, is_writing_question
from autoapply.config import Config
from autoapply.database import Database
from autoapply.disclosures import FACTS
from autoapply.models import Listing, Question, now

FIELDS = [('First name', 'text', []), ('Last name', 'text', []),
          ('Email', 'email', []), ('Phone', 'text', []), ('School', 'text', []),
          ('Degree', 'text', []), ('Major', 'text', []), ('Graduation date', 'date', []),
          ('Are you authorized to work in the US?', 'radio', ['Yes', 'No']),
          ('Do you require sponsorship now?', 'radio', ['Yes', 'No']),
          ('Will you require sponsorship in the future?', 'radio', ['Yes', 'No']),
          ('Have you ever worked for us?', 'radio', ['Yes', 'No']),
          ('Do you have any conflicts of interest with us?', 'radio', ['Yes', 'No']),
          ('SMS recruiting preference', 'radio', ['Yes', 'No']),
          ('Privacy acknowledgement', 'checkbox', ['Yes', 'No'])]

def sample(fields, repeats):
    with tempfile.TemporaryDirectory(prefix='autoapply-phase5-') as folder:
        config = Config(folder, {'ai': {'enabled': False}, 'discord': {'enabled': False},
                                'gmail': {'enabled': False}, 'application': {'auto_submit': False}})
        profile = {'identity': {'first_name': 'Synthetic', 'last_name': 'Student'},
                   'contact': {'email': 'synthetic@example.test', 'phone': '2025550100'},
                   'education': {'school': 'Example University', 'degree': "Bachelor's", 'major': 'Computer Science', 'graduation_date': '2028-05-01'},
                   'work_authorization': {'us_authorized': True, 'sponsorship_now': False, 'sponsorship_future': False},
                   'standing_disclosures': {k: {'source': 'USER_PROVIDED', 'scope': 'standing', 'answer': False, 'fact': v} for k, v in FACTS.items()}}
        (config.private / 'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
        db = Database(config.private / 'measure.sqlite3')
        try:
            db.ingest(Listing('Synthetic', 'Software Intern', 'New York, NY', 'https://example.test/job/1', 'synthetic', posted_at=now()), config)
            app = db.application(1)
            counts = Counter(profile_reads=0, SELECT=0, writes=0, events=0, resolver_calls=0,
                             resolution_computations=0, mapper_calls=0, normalization_calls=0, ai_calls=0)
            read, execute, event, norm = config_module.read_yaml, db.execute, db.event, mapping.normalize
            def read_count(path):
                counts['profile_reads'] += path.name == 'profile.yaml'
                return read(path)
            def sql_count(sql, args=()):
                verb = sql.strip().split()[0].upper()
                counts['SELECT'] += verb == 'SELECT'
                counts['writes'] += verb in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}
                return execute(sql, args)
            def event_count(*args, **kwargs):
                counts['events'] += 1
                return event(*args, **kwargs)
            def norm_count(value):
                counts['normalization_calls'] += 1
                return norm(value)
            async def workload():
                resolver = AnswerResolver(config, db)
                mapper = mapping.FieldMapper('synthetic')
                for _ in range(repeats):
                    for label, kind, options in fields:
                        counts['mapper_calls'] += 1
                        result = await mapper.map({'label': label, 'kind': kind, 'options': options})
                        q = Question(label, label, kind, True, options, semantic_key=result['semantic_key'])
                        counts['resolver_calls'] += 1
                        answer = resolver.resolve(q, app)
                        # This is the engine's narrative admission gate, with a stub provider.
                        if answer is None and is_writing_question(q):
                            counts['ai_calls'] += 1
                counts['resolution_computations'] = getattr(resolver, 'computations', counts['resolver_calls'])
            with patch.object(config_module, 'read_yaml', read_count), patch.object(db, 'execute', sql_count), patch.object(db, 'event', event_count), patch.object(mapping, 'normalize', norm_count):
                start = time.perf_counter()
                asyncio.run(workload())
                seconds = time.perf_counter() - start
            return dict(counts, seconds=round(seconds, 6), fields=len(fields), repeats=repeats)
        finally:
            db.close()

if __name__ == '__main__':
    result = {'method': 'Synthetic temporary storage; resolution considered but not used; AI gate stub; no throughput claim.',
              'first_name': sample(FIELDS[:1], 20), 'representative': sample(FIELDS, 20)}
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
