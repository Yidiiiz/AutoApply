"""Execute a prepared authorization once. Invoke only when a live run is requested."""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autoapply.config import Config
from autoapply.engine import Engine
from autoapply.runtime import ProcessLock
from scripts.fill_only_batch import FillDatabase


async def run(config, db, app_id, command_id):
    engine = Engine(config, db, fill_only=True)
    try:
        db.set_setting('paused', False)
        await engine.process_one(app_id, continuation_id=command_id)
    finally:
        db.set_setting('paused', True)
        await engine.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application_id', type=int)
    parser.add_argument('--authorization', required=True)
    args = parser.parse_args()
    config = Config(ROOT, {'application': {'auto_submit': False}, 'discord': {'enabled': False},
                          'gmail': {'enabled': False}, 'ai': {'enabled': False}})
    with ProcessLock(config.private / 'worker.lock'):
        db = FillDatabase(config.private / 'autoapply.sqlite3', startup_maintenance=False)
        try:
            asyncio.run(run(config, db, args.application_id, args.authorization))
        finally:
            db.close()
