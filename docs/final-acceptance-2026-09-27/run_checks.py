"""Sequential audit-only regressions and existing Phase 3–8 harnesses.

Run after the complete Phase 8 suite. No existing evidence is overwritten.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT/'.tmp/final-acceptance-2026-09-27'
OUT.mkdir(parents=True, exist_ok=True)
env = dict(os.environ)
env['PYTHONPATH'] = os.pathsep.join(str(ROOT/p) for p in (
    'docs/phase5-2026-09-25', 'docs/phase7-2026-09-27', 'docs/phase8-2026-09-27'))
# The browser harness launches its own pytest and already adds offline_validation.
env['PYTEST_ADDOPTS'] = '-p offline_providers'
commands = [
    ('audit', ['-m', 'pytest', '-p', 'offline_validation', '-q', '--tb=short',
               str(HERE/'test_audit_contracts.py'), '--junitxml='+str(OUT/'audit.xml')]),
    ('phase3', ['docs/phase3-2026-09-25/measure.py']),
    ('phase4', ['docs/phase4-2026-09-25/measure.py', str(OUT/'phase4.json')]),
    ('phase5', ['docs/phase5-2026-09-25/measure.py', str(OUT/'phase5.json')]),
    ('phase6', ['docs/phase6-2026-09-26/measure.py', str(OUT/'phase6.json')]),
    ('phase7', ['docs/phase7-2026-09-27/measure.py', str(OUT/'phase7.json')]),
    ('control-work', ['-m', 'pytest', '-p', 'offline_validation', '-p', 'control_work',
                      '--phase7-work', str(OUT/'control-work.json'), '-q', '--tb=short',
                      'tests/test_phase7_lifecycle.py', 'tests/test_phase7_fill.py',
                      '--junitxml='+str(OUT/'control-work.xml')]),
    ('phase8', ['docs/phase8-2026-09-27/measure.py', str(OUT/'phase8.json')]),
    ('ratio', ['docs/phase8-2026-09-27/ratio.py', str(OUT/'ratio.json')]),
]
results = []
for name, args in commands:
    print('Starting '+name, flush=True)
    start = time.monotonic()
    with (OUT/(name+'.log')).open('w', encoding='utf-8') as stream:
        completed = subprocess.run([sys.executable, '-u', *args], cwd=ROOT, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT)
    results.append(dict(name=name, exit_code=completed.returncode,
                        seconds=round(time.monotonic()-start, 3), command=args))
    (HERE/'harness-runs.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(results[-1]), flush=True)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    source = OUT/(name+'.json')
    if name=='phase3':
        source.write_text((OUT/'phase3.log').read_text(encoding='utf-8'), encoding='utf-8')
    if source.exists():
        (HERE/(name+'.json')).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
