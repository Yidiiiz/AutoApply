"""TEST ONLY. Real bounded runner/containment with a synthetic subprocess, no AI CLI."""
import json
from pathlib import Path
import sys
import time

source, root, task_path, mode = map(str, sys.argv[1:])
sys.path.insert(0, str(Path(source) / 'tools'))
import agent_runner

if sys.stdin.readline() != 'start\n':
    raise SystemExit(1)
if mode == 'preclaim':
    time.sleep(30)
    raise SystemExit(1)
report = agent_runner.run(Path(root), Path(task_path), require_doctor=False,
                          worker_argv=[sys.executable, str(Path(__file__).with_name('fake_worker.py')), mode])
raise SystemExit(0 if report['checks_passed'] else 1)
