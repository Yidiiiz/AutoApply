"""Offline synthetic writing workload; the CLI transport is replaced entirely."""
import asyncio
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from autoapply.ai import AIManager, ProviderUnavailable
from autoapply.codex_writer import CodexWritingProvider
from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import Listing, Question

LABELS = ['Why are you interested in this company?', 'Why are you interested in this role?',
          'Describe relevant experience.', 'Describe a project.', 'Why this location?',
          'Additional information.', 'Cover-letter-style response.']


async def workload(labels):
    with tempfile.TemporaryDirectory(prefix='phase8-measure-') as folder:
        config = Config(folder, {'ai': {'enabled': True, 'providers': [dict(
            name='fixture', type='codex_cli', billing='included', tier=3,
            executable='FAKE-NEVER-EXECUTED', model='fixture')]},
            'application': {'auto_submit': False}, 'gmail': {'enabled': False},
            'discord': {'enabled': False}})
        profile = dict(education={'school': 'Example University', 'major': 'Computer Science',
                                 'graduation_date': '2028-05-01'}, skills=['Python'],
                       verified_facts={'project': 'I built a Python course availability tracker.',
                                       'leadership': 'I organized the student club.'})
        (config.private/'profile.yaml').write_text(yaml.safe_dump(profile), encoding='utf-8')
        db = Database(config.private/'test.sqlite3')
        db.ingest(Listing('Example', 'Engineering Intern', 'New York, NY',
                         'https://jobs.lever.co/example/abc', 'fixture',
                         posted_at=date.today().isoformat(),
                         description='Software engineering internship for undergraduate students.'), config)
        app = db.application(1)
        counts, prompts = Counter(), []
        def profiler(frame, event, arg):
            if event != 'call':
                return
            name, filename = frame.f_code.co_name, frame.f_code.co_filename.replace('\\', '/')
            if name == '__init__' and filename.endswith('/codex_writer.py'):
                counts['provider_constructions'] += 1
            for function, key in [('read_yaml', 'profile_file_reads'), ('profile_snapshot', 'snapshot_checks'),
                                  ('written_reuse', 'writing_bank_lookups'), ('build_context', 'context_builds'),
                                  ('select_providers', 'provider_selections')]:
                if name == function and '/autoapply/' in filename:
                    counts[key] += 1
        original_execute = db.execute
        def execute(sql, params=()):
            verb = sql.lstrip().split()[0].upper()
            counts['selects' if verb == 'SELECT' else 'writes' if verb in {'INSERT','UPDATE','DELETE'} else 'other_sql'] += 1
            return original_execute(sql, params)
        async def run(self, args, cwd, prompt=None):
            if args == ['login', 'status']:
                counts['readiness_checks'] += 1
                return 'Logged in using ChatGPT'
            counts['provider_calls'] += 1
            counts['model_selections'] += int('--model' in args)
            prompts.append(prompt)
            request = json.loads(prompt.split('\n', 1)[1])
            if request.get('task') == 'verify_grounding':
                context_id = 'job.description' if 'job.description' in request['sources'] else 'job.role'
                value = dict(supported=True, needs_input=False, unsupported_claims=[], evidence=[
                    dict(source_id='project', quote=profile['verified_facts']['project']),
                    dict(source_id=context_id, quote=request['sources'][context_id])])
            else:
                counts['prompt_builds'] += 1
                value = dict(answer=profile['verified_facts']['project'], fact_ids=['project'], needs_input=False)
            Path(args[args.index('-o')+1]).write_text(json.dumps(value), encoding='utf-8')
            return ''
        manager = AIManager(config, db, None)
        start = time.perf_counter()
        with patch.object(db, 'execute', execute), patch.object(CodexWritingProvider, 'run', run), patch.dict(os.environ, CODEX_HOME=str(Path(folder)/'offline-codex')):
            sys.setprofile(profiler)
            try:
                for label in labels:
                    try:
                        await manager.draft(Question('writing', label, 'textarea'), app)
                        counts['proposals'] += 1
                    except ProviderUnavailable:
                        counts['input_required'] += 1
            finally:
                sys.setprofile(None)
        counts['seconds'] = round(time.perf_counter()-start, 6)
        counts['input_characters'] = sum(map(len, prompts))
        counts['repeated_identical_calls'] = len(prompts)-len(set(prompts))
        counts['events'] = len(db.rows("SELECT * FROM events WHERE kind='grounded_narrative'"))
        counts.update(getattr(manager, 'metrics', {}))
        # Before the context builder existed, one context was assembled per draft prompt.
        counts.setdefault('context_builds', counts['prompt_builds'])
        db.close()
        return dict(counts)


async def main():
    result = {'representative': await workload(LABELS),
              'repeated_20': await workload([LABELS[0]]*20)}
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
