"""Compare protected-set scans with the reviewed baseline function on temporary data."""
import ast
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from autoapply.config import Config
from autoapply.database import Database
from autoapply.jobs import job_identity, ats_identity, normalize
from autoapply.models import Listing, now

BASELINE = '146293a06d3c8d41c430a5c469a735925762ac1d'
source = subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'show',BASELINE+':autoapply/database.py'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
function = next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='submission_conflict')
namespace = dict(job_identity=job_identity,ats_identity=ats_identity,normalize=normalize)
exec(compile(ast.Module(body=[function],type_ignores=[]),'<reviewed-baseline-conflict>','exec'),namespace)
results = {}
with tempfile.TemporaryDirectory(prefix='phase7-conflicts-') as root:
    config=Config(root, {'ai':{'enabled':False},'discord':{'enabled':False}})
    db=Database(config.private/'fixture.sqlite3')
    try:
        for number in range(2):
            db.ingest(Listing('Synthetic','Software Intern','New York, NY',f'https://jobs.lever.co/synthetic/{number}','fixture',posted_at=now()),config)
        db.transition(1,'SUBMITTED',confirmation_text='Synthetic receipt')
        for name, callback in [('before',lambda:namespace['submission_conflict'](db,2)),('after',lambda:db.submission_conflict(2))]:
            counts=Counter()
            rows, history=db.rows,db.history.list_applications
            def read(sql,*args,**kwargs):
                if 'a.id!=?' in sql: counts['protected_sql_scans']+=1
                return rows(sql,*args,**kwargs)
            def records(*args,**kwargs):
                counts['history_scans']+=1
                return history(*args,**kwargs)
            with patch.object(db,'rows',read), patch.object(db.history,'list_applications',records):
                assert callback() is None
            results[name]=dict(counts)
    finally:
        db.close()
results['method']='One nonconflicting known requisition against one submitted same-title record. Counts are per policy call, not time or asymptotic improvements.'
Path(__file__).with_name('conflict-scans.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
