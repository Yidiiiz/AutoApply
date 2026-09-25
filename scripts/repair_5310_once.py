"""Authorized incident repair and exactly one controlled production application."""
import asyncio
import getpass
import json

from controlled_once import ROOT, Config, Database, ProcessLock, load_dotenv, run, setup_logging, snapshot
from autoapply.eligibility_repair import repair_5310


def main():
    if getpass.getuser().lower() == 'codexsandboxoffline':
        raise RuntimeError('EXTERNAL_EXECUTION_APPROVAL_REQUIRED')
    load_dotenv(ROOT / '.env')
    config = Config(ROOT)
    if config['browser']['headless']:
        raise RuntimeError('Headed browser required')
    setup_logging(config)
    with ProcessLock(config.private / 'worker.lock'):
        baseline = snapshot(config)
        audit = config.private / 'eligibility-repair-5310'
        audit.mkdir(exist_ok=True)
        before = audit / 'before.json'
        already_repaired = before.exists()
        if not already_repaired:
            before.write_text(json.dumps(baseline, indent=2), encoding='utf-8')
        db = Database(config.private / 'autoapply.sqlite3', startup_maintenance=False)
        try:
            if already_repaired:
                app = db.application(5310)
                if (app['status'] != 'QUEUED' or app['attempts'] != 1 or app['submit_intent_at']
                        or not db.one("SELECT id FROM events WHERE application_id=5310 AND kind='ELIGIBILITY_REPAIR_APPLIED'")
                        or not (audit / 'repair.json').exists()):
                    raise RuntimeError('Already attempted or unverified repair; inspect existing session')
            else:
                evidence = repair_5310(db, config.profile)
                (audit / 'repair.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
                print(json.dumps(evidence), flush=True)
            asyncio.run(run(config, db, 5310, baseline))
        finally:
            db.close()


if __name__ == '__main__':
    main()
