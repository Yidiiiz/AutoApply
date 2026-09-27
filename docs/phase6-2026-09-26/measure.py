"""Reproduce the nine local browser-work samples with the Phase 5 offline guard."""
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    # Use a new destination by default, preserving the reviewed before/after pair.
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.tmp/phase6/repeated-work.json'
    identifiers = list(json.loads((HERE / 'before.json').read_text()))
    environment = dict(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join([
        str(ROOT / 'docs/phase5-2026-09-25'), str(HERE),
        environment.get('PYTHONPATH', ''),
    ])
    return subprocess.call([
        sys.executable, '-u', '-m', 'pytest', '-p', 'offline_validation',
        '-p', 'browser_work', '-q', '--tb=short', '--browser-work',
        str(destination.resolve()), *identifiers,
    ], cwd=ROOT, env=environment)


if __name__ == '__main__':
    raise SystemExit(main())
