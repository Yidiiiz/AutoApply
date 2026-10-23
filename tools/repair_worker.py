"""Fixed automatic repair entrypoint; existing runner remains the policy owner."""
import argparse
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autoapply.repair_queue import verified_workspace, worker_environment
import agent_runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\.agent/tasks/Repair_[a-f0-9]{40}\.json', args.task):
        return 1
    if sys.stdin.readline() != 'start\n':
        return 1
    root = Path(__file__).resolve().parents[1]
    work, reason = verified_workspace(root)
    if reason or work != root:
        return 1
    # Also enforce privacy when this fixed helper is invoked outside the dashboard.
    clean = worker_environment()
    os.environ.clear()
    os.environ.update(clean)
    original = agent_runner.executable

    def executable(name):
        argv = original(name)
        return argv + ['-c', 'windows.sandbox="mxc"'] if name == 'codex' and os.name == 'nt' else argv

    agent_runner.executable = executable
    sys.argv = [str(root / 'tools/agent_runner.py'), 'run', '--task', args.task]
    return agent_runner.main()


if __name__ == '__main__':
    raise SystemExit(main())
