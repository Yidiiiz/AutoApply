"""Verify every exact Phase 7 baseline ID against a complete offline JUnit run."""
import json
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
baseline = json.loads((HERE/'baseline-ids.json').read_text())['baseline_ids']
collected = json.loads((HERE/'final-ids.json').read_text())
for name, digest in json.loads((HERE/'validated-tree.json').read_text()).items():
    assert hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest() == digest, name
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'.tmp/phase8/full-suite-offline.xml'
tree = ET.parse(path)
outcomes = {}
for case in tree.findall('.//testcase'):
    identifier = case.attrib['classname'].replace('.', '/')+'.py::'+case.attrib['name']
    assert identifier not in outcomes, identifier
    outcomes[identifier] = next((s for s in ('failure','error','skipped') if case.find(s) is not None), 'passed')
result = dict(total=len(outcomes), passed=sum(v == 'passed' for v in outcomes.values()),
              failures=sum(v == 'failure' for v in outcomes.values()),
              errors=sum(v == 'error' for v in outcomes.values()),
              skipped=sum(v == 'skipped' for v in outcomes.values()),
              phase7_baseline_count=len(baseline),
              phase7_passing=sum(outcomes.get(i) == 'passed' for i in baseline),
              missing=sorted(set(baseline)-outcomes.keys()),
              nonpassing={i:outcomes.get(i) for i in baseline if outcomes.get(i) != 'passed'},
              collected_count=len(collected),
              missing_collected=sorted(set(collected)-outcomes.keys()),
              unexpected=sorted(outcomes.keys()-set(collected)),
              new_ids=sorted(outcomes.keys()-set(baseline)),
              junit_seconds=sum(float(s.attrib['time']) for s in tree.findall('.//testsuite')),
              outcomes=outcomes)
(HERE/'test-results.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'outcomes','new_ids'}}, indent=2))
assert len(baseline) == 934 and result['phase7_passing'] == 934
assert len(collected) == len(set(collected)) == result['total']
assert not result['missing_collected'] and not result['unexpected']
assert result['passed'] == result['total']
