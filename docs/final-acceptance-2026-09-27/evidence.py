"""Read-only source inventory and exact outcome verifier for final acceptance.

Only writes this audit's evidence directory. Never constructs production Config.
"""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def write(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()


def inventory():
    manifest = json.loads((ROOT / 'docs/phase8-2026-09-27/validated-tree.json').read_text())
    mismatch = [name for name, value in manifest.items() if digest(ROOT / name) != value]
    git = ['git', '-c', 'safe.directory=' + ROOT.as_posix(), '-C', str(ROOT)]
    def run(*args):
        return subprocess.check_output([*git, *args], text=True, stderr=subprocess.DEVNULL).strip()
    integrity = json.loads((ROOT / 'docs/phase8-2026-09-27/integrity.json').read_text())
    prior_bad = [r['path'] for r in integrity['files'] if digest(ROOT / r['path']) != r['sha256']]
    write('repository.json', dict(commit=run('rev-parse', 'HEAD'), branch=run('branch', '--show-current'),
        status=run('status', '--short').splitlines(), phase8_manifest_files=len(manifest),
        phase8_manifest_mismatches=mismatch, phase8_prior_integrity_mismatches=prior_bad,
        normalized_source_hashes={p.as_posix().removeprefix(ROOT.as_posix()+'/'): digest(p)
                                  for p in (ROOT/'autoapply').rglob('*.py')}))
    assert not mismatch and not prior_bad
    pattern = re.compile(r'applications\.status|application_state|retry_allowed|retry_at|\battempts\b|submit_intent_at|submission_confirmation_seen|manual_resume_allowed|session_preserved|security_state|verification_state|reconstruction_verified|auto_submit|\.(?:transition|fail|retry|draft)\(|UPDATE\s+applications|\.click\(|click_element\(|generate_response\(|provider\.generate|importlib\.reload|__class__|submission_candidate|final_request')
    rows = []
    for folder in ('autoapply', 'scripts', 'tests'):
        for path in sorted((ROOT/folder).rglob('*.py')):
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for line, content in enumerate(source.splitlines(), 1):
                matches = pattern.findall(content)
                if not matches:
                    continue
                owner = next((n.name for n in sorted(functions, key=lambda n:n.lineno, reverse=True)
                              if n.lineno <= line <= n.end_lineno), '<module/schema>')
                if folder == 'tests':
                    kind, reason = 'TEST/FIXTURE', 'Synthetic assertion, setup, instrumentation or deliberately injected violation.'
                elif folder == 'scripts' or path.stem in {'eligibility_repair', 'maintenance'}:
                    kind, reason = 'MAINTENANCE/INCIDENT TOOL', 'Explicit operational entry point; see report for guarded incident exceptions. Not executed.'
                elif path.stem == 'database' and owner in {'transition', 'fail', 'retry', 'recover', 'claim', 'update_security'}:
                    kind, reason = 'COMPATIBILITY FACADE', 'Delegates to Lifecycle; legacy transition relaxes graph only, retaining safety guards.'
                else:
                    kind, reason = 'AUTHORIZED OWNER', 'Owner implementation, guarded call to owner, schema initialization, or read-only projection; see report by module.'
                rows.append(dict(path=path.relative_to(ROOT).as_posix(), line=line, function=owner,
                                 classification=kind, rationale=reason, source=content.strip()))
    write('bypass-inventory.json', dict(scope='All Python source in autoapply, scripts, tests; line-level occurrences include reads.',
                                      counts=dict(Counter(r['classification'] for r in rows)), occurrences=rows))
    # Scan only publishable paths, never ignored credentials/private history.
    from autoapply.privacy import SECRET, ASSIGNMENT, FORBIDDEN
    names = run('ls-files', '--cached', '--others', '--exclude-standard').splitlines()
    findings = []
    for name in names:
        path = ROOT/name
        if not path.is_file():
            continue
        if FORBIDDEN.search(name) and not name.endswith('.env.example'):
            findings.append(dict(path=name, reason='private filename'))
        content = path.read_text(encoding='utf-8', errors='replace')
        if SECRET.search(content) or ASSIGNMENT.search(content):
            findings.append(dict(path=name, reason='possible credential pattern; content suppressed'))
    write('publishable-secret-scan.json', dict(files=len(names), findings=findings,
          limitation='Pattern scan of current publishable files; not an all-history credential or entropy proof. Private ignored files intentionally unread.'))
    print(json.dumps(dict(manifest_files=len(manifest), mismatches=mismatch, prior_mismatches=prior_bad,
                          inventory_occurrences=len(rows), secret_findings=len(findings))))


def results(xml_path):
    tree = ET.parse(xml_path)
    outcomes = {}
    for case in tree.findall('.//testcase'):
        key = case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name']
        assert key not in outcomes, key
        outcomes[key] = next((s for s in ('failure', 'error', 'skipped') if case.find(s) is not None), 'passed')
    baseline = json.loads((ROOT/'docs/phase8-2026-09-27/final-ids.json').read_text())
    prior = {}
    for phase, filename in ((1, '.tmp/audit-2026-09-25/baseline.xml'), (2, '.tmp/phase2/full-suite.xml')):
        cases = ET.parse(ROOT/filename).findall('.//testcase')
        ids = [c.attrib['classname'].replace('.', '/')+'.py::'+c.attrib['name'] for c in cases]
        assert len(ids)==len(set(ids))
        prior[str(phase)] = dict(count=len(ids), missing=sorted(set(ids)-outcomes.keys()),
                                 nonpassing=[i for i in ids if outcomes.get(i) != 'passed'])
    ids = json.loads((ROOT/'docs/phase4-2026-09-25/baseline-ids.json').read_text())['baseline_ids']
    prior['3'] = dict(count=len(ids), missing=sorted(set(ids)-outcomes.keys()),
                     nonpassing=[i for i in ids if outcomes.get(i) != 'passed'])
    for phase, day in ((4,25),(5,25),(6,26),(7,27)):
        data = json.loads((ROOT/f'docs/phase{phase}-2026-09-{day}/test-results.json').read_text())
        ids = data['outcomes']
        prior[str(phase)] = dict(count=len(ids), missing=sorted(set(ids)-outcomes.keys()),
                                 nonpassing=[i for i in ids if outcomes.get(i) != 'passed'])
    value = dict(total=len(outcomes), counts=dict(Counter(outcomes.values())), baseline_count=len(baseline),
                 missing=sorted(set(baseline)-outcomes.keys()), unexpected=sorted(outcomes.keys()-set(baseline)),
                 nonpassing={k:v for k,v in outcomes.items() if v!='passed'}, prior_baselines=prior,
                 junit_seconds=sum(float(s.attrib['time']) for s in tree.findall('.//testsuite')),
                 outcomes=outcomes)
    write('test-results.json', value)
    print(json.dumps({k:v for k,v in value.items() if k!='outcomes'}, indent=2))
    assert len(baseline) == len(set(baseline)) == 1023
    assert not value['missing'] and not value['unexpected'] and not value['nonpassing']
    assert all(not p['missing'] and not p['nonpassing'] for p in prior.values())


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    inventory() if len(sys.argv) == 1 else results(Path(sys.argv[1]))
