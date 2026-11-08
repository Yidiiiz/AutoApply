import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import agent_runner as runner

@pytest.fixture
def workspace(tmp_path):
    root=tmp_path/'space Unicode \u00e9'; root.mkdir()
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    source=Path(__file__).resolve().parents[1]
    for name in ('agent-config.json','agent-task.schema.json','agent-worker-result.schema.json'):
        (root/name).write_bytes((source/name).read_bytes())
    cfg=json.loads((root/'agent-config.json').read_text())
    cfg['checks']={'ok':dict(argv=['{python}','-c','print("independent check")'],timeout_seconds=10),
                  'fail':dict(argv=['{python}','-c','raise SystemExit(1)'],timeout_seconds=10),
                  'slow':dict(argv=['{python}','-c','import time; time.sleep(30)'],timeout_seconds=1)}
    cfg['worker_timeout_seconds']=5; cfg['check_timeout_seconds']=10
    (root/'agent-config.json').write_text(json.dumps(cfg),encoding='utf-8')
    (root/'.gitignore').write_text('.agent/\n',encoding='utf-8')
    (root/'src').mkdir(); (root/'src/preexisting.py').write_text('# user work\n',encoding='utf-8')
    return root

def task(root,*,attempt=1,parent=None,check='ok',scope=None):
    value=dict(task_id='T001',attempt=attempt,objective='Fake worker verification',context='No model usage',
        scope_paths=scope or ['src/'],acceptance_criteria=['Observable fake output'],check_ids=[check],constraints=[],previous_report_id=parent)
    path=root/'.agent/tasks/T001.json'; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8'); return path

def fake(root,mode='valid'):
    path=root/'fake-worker.py'
    path.write_text('''import json,sys,time,subprocess
from pathlib import Path
mode=sys.argv[1]
folder=max(Path('.agent/runs').iterdir(),key=lambda p:p.stat().st_mtime_ns)
task=json.loads((folder/'task.json').read_text(encoding='utf-8'))
result=dict(task_id=task['task_id'],status='completed',summary='Fake completion',changed_files=['src/result.py'],checks_claimed=['I passed'],blockers=[],suggested_next_action='Review')
print(json.dumps(dict(type='thread.started',thread_id='fake-thread')),flush=True)
if mode in ('quota','plain_auth'):
 print(json.dumps(dict(type='error',message='Usage limit reached')) if mode=='quota' else 'Error: authentication failed (401)',flush=True)
 time.sleep(20)
if mode in ('slow','descendant'):
 if mode=='descendant':
  p=subprocess.Popen([sys.executable,'-c','import time; from pathlib import Path; time.sleep(2); Path("escaped.txt").write_text("bad"); time.sleep(30)'])
  (folder/'child.pid').write_text(str(p.pid))
 time.sleep(30)
if mode=='nonzero': sys.exit(2)
if mode=='wrong': result['task_id']='wrong'
if mode=='malformed': (folder/'worker-result.json').write_text('not json')
elif mode!='missing': (folder/'worker-result.json').write_text(json.dumps(result))
if mode=='outscope': Path('outside.txt').write_text('unreviewed')
if mode=='edit':
 p=Path('src/preexisting.py'); p.write_text(p.read_text()+'# worker addition\\n')
Path('src/result.py').write_text('# fake output')
''',encoding='utf-8')
    return [sys.executable,str(path),mode]

def launch(root,mode='valid',**kw):
    argv=fake(root,mode)
    return runner.run(root,task(root,**kw),worker_argv=argv,require_doctor=False)

def test_valid_preserves_preexisting_work_and_needs_supervisor_acceptance(workspace):
    root=workspace; before=(root/'src/preexisting.py').read_bytes()
    report=launch(root)
    assert report['runner_outcome']=='checks_passed'
    assert report['worker_finished'] and report['checks_passed'] and not report['supervisor_accepted']
    assert report['worker_process']['session_id']=='fake-thread'
    assert report['changed_files']==['src/result.py']
    assert (root/'src/preexisting.py').read_bytes()==before
    original=Path(report['full_report']).read_bytes()
    state=runner.State(root)
    runner.review(state,report['run_id'],'accepted','Inspected output and actual check log')
    assert state.rows()[0]['task_state']=='accepted'
    assert Path(report['full_report']).read_bytes()==original
    state.db.close()

@pytest.mark.parametrize('mode',['missing','malformed','wrong','nonzero'])
def test_invalid_output_or_exit_cannot_be_accepted(workspace,mode):
    report=launch(workspace,mode)
    assert report['runner_outcome']=='needs_followup' and not report['checks_passed']
    state=runner.State(workspace)
    with pytest.raises(ValueError): runner.review(state,report['run_id'],'accepted','Claimed success')
    state.db.close()

def test_actual_check_failure_overrides_worker_claim(workspace):
    report=launch(workspace,check='fail')
    assert report['worker_claim']['status']=='completed' and not report['checks_passed']
    assert report['check_results'][0]['exit_code']==1

def test_out_of_scope_changes_are_preserved_and_blocked(workspace):
    report=launch(workspace,'outscope')
    assert report['runner_outcome']=='blocked'
    assert report['out_of_scope_changes']==['outside.txt']
    assert (workspace/'outside.txt').exists()

def test_staged_and_unstaged_baseline_are_preserved(workspace):
    runner.git(workspace,'add','src/preexisting.py')
    path=workspace/'src/preexisting.py'; path.write_text('# user staged work\n# user unstaged work\n')
    report=launch(workspace,'edit')
    assert report['changed_files']==['src/preexisting.py','src/result.py']
    assert path.read_text().startswith('# user staged work\n# user unstaged work\n')
    baseline=json.loads(Path(report['full_report']).with_name('baseline.json').read_text())
    after=json.loads(Path(report['full_report']).with_name('after.json').read_text())
    assert baseline['index']==after['index']

@pytest.mark.parametrize('path',['../escape','C:/outside','/absolute','src/../../bad','.agent/','data/','.','.agent','Data/','src/file:stream'])
def test_scope_boundaries(workspace,path):
    with pytest.raises(ValueError): launch(workspace,scope=[path])

def test_link_escape(workspace,tmp_path):
    link=workspace/'escape'
    try: link.symlink_to(tmp_path,target_is_directory=True)
    except OSError: pytest.skip('Symlink creation unavailable; Windows junction checked by same resolve boundary')
    with pytest.raises(ValueError): runner.relative(workspace,'escape/outside')

def test_duplicate_claim_review_and_budget(workspace):
    report=launch(workspace)
    with pytest.raises(ValueError): launch(workspace)
    state=runner.State(workspace)
    runner.review(state,report['run_id'],'needs_followup','Scoped correction')
    state.db.execute('UPDATE budget SET used=6 WHERE id=1'); state.db.close()
    with pytest.raises(ValueError,match='budget'): launch(workspace,attempt=2,parent=report['run_id'])

def test_attempt_limit(workspace):
    with pytest.raises(ValueError,match='attempt'): launch(workspace,attempt=4)

def test_atomic_concurrent_claim(workspace):
    path=task(workspace); value=json.loads(path.read_text()); cfg=runner.config(workspace)
    # Initialize schema before racing independent connections.
    runner.State(workspace).db.close()
    outcomes=[]
    def claim():
        state=runner.State(workspace)
        try: state.claim(value,cfg,__import__('uuid').uuid4().hex); outcomes.append('claimed')
        except ValueError: outcomes.append('rejected')
        finally: state.db.close()
    threads=[threading.Thread(target=claim) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(outcomes)==['claimed','rejected']

@pytest.mark.parametrize('mode',['quota','plain_auth'])
def test_quota_latches_stop_and_never_retries(workspace,mode):
    report=launch(workspace,mode)
    assert report['worker_process']['process_state']=='auth_or_quota'
    state=runner.State(workspace)
    runner.review(state,report['run_id'],'needs_followup','Must wait for explicit reset')
    state.db.close()
    with pytest.raises(ValueError,match='latched'): launch(workspace,attempt=2,parent=report['run_id'])

@pytest.mark.parametrize('mode,check',[('slow','ok'),('descendant','ok'),('valid','slow')])
def test_deadline_kills_owned_tree(workspace,mode,check):
    cfg=runner.config(workspace); cfg['worker_timeout_seconds']=1
    (workspace/'agent-config.json').write_text(json.dumps(cfg))
    report=launch(workspace,mode,check=check)
    proc=report['check_results'][0] if check=='slow' else report['worker_process']
    assert proc['process_state']=='timed_out'
    time.sleep(2.2)
    assert not (workspace/'escaped.txt').exists()
    child_path=Path(report['full_report']).with_name('child.pid')
    if child_path.exists(): assert runner.identity(int(child_path.read_text())) is None

def test_cancel_preserves_partial_logs(workspace):
    argv=fake(workspace,'slow'); path=task(workspace)
    result=[]
    def work(): result.append(runner.run(workspace,path,worker_argv=argv,require_doctor=False))
    thread=threading.Thread(target=work); thread.start()
    state=None
    for _ in range(100):
        if (workspace/'.agent/state.sqlite3').exists():
            try:
                state=runner.State(workspace); rows=state.rows()
                if rows: break
                state.db.close()
            except sqlite3.Error: pass
        time.sleep(.05)
    assert rows
    state.db.execute('UPDATE runs SET cancel=1 WHERE id=?',(rows[0]['id'],)); state.db.close()
    thread.join(timeout=10)
    assert result[0]['worker_process']['process_state']=='cancelled'
    assert Path(result[0]['full_report']).exists()

def test_restart_never_replays_or_claims_success(workspace):
    state=runner.State(workspace); value=json.loads(task(workspace).read_text())
    state.claim(value,runner.config(workspace),'a'*32)
    state.db.execute('UPDATE runs SET pid=-1,birth=?',('stale',))
    state.reconcile()
    assert state.rows()[0]['task_state']=='blocked'
    report=json.loads((workspace/'.agent/runs'/('a'*32)/'runner-report.json').read_text())
    assert report['runner_outcome']=='interrupted' and not report['worker_finished']
    with pytest.raises(ValueError): state.claim(value,runner.config(workspace),'b'*32)
    state.db.close()

def test_report_delivery_allows_next_scoped_task_in_same_caller(workspace):
    first=launch(workspace)
    state=runner.State(workspace)
    runner.review(state,first['run_id'],'needs_followup','Correction requested from main conversation')
    state.db.close()
    second=launch(workspace,attempt=2,parent=first['run_id'])
    assert second['parent_report_id']==first['run_id'] and second['runner_outcome']=='checks_passed'

def test_log_budget_is_bounded(workspace):
    cfg=runner.config(workspace); cfg['max_log_bytes_per_run']=10
    (workspace/'agent-config.json').write_text(json.dumps(cfg))
    report=launch(workspace)
    assert report['worker_process']['log_bytes']<=100
    assert not report['checks_passed']

def test_boolean_attempt_invalid(workspace):
    with pytest.raises(ValueError): launch(workspace,attempt=True)
