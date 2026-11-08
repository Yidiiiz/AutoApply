"""Runner-owned launch gate: no descendants exist before containment is ready."""
import json
import subprocess
import sys

def main():
    payload = json.loads(sys.stdin.readline())
    # The runner assigns this process to its Job Object / process group first.
    with open(payload['stdin'], 'rb') as prompt:
        process = subprocess.Popen(payload['argv'], cwd=payload['cwd'], stdin=prompt)
        return process.wait()

if __name__ == '__main__':
    raise SystemExit(main())
