"""TEST ONLY synthetic contained worker. No provider or employer calls."""
import json
import os
from pathlib import Path
import sys
import time

mode = sys.argv[1]
assert not any(k.startswith('AUTOAPPLY_LOGIN_') or k in {'OPENAI_API_KEY', 'UNRELATED_SECRET'} for k in os.environ)
folder = max(Path('.agent/runs').iterdir(), key=lambda p: p.stat().st_mtime_ns)
task = json.loads((folder / 'task.json').read_text(encoding='utf-8'))
print(json.dumps(dict(type='thread.started', thread_id='offline-fake')), flush=True)
if mode == 'slow':
    time.sleep(30)
if mode == 'quota':
    print(json.dumps(dict(type='error', message='Usage limit reached')), flush=True)
    time.sleep(30)
result = dict(task_id=task['task_id'], status='completed', summary='Synthetic offline result',
              changed_files=[], checks_claimed=[], blockers=[], suggested_next_action='Review')
(folder / 'worker-result.json').write_text(json.dumps(result), encoding='utf-8')
