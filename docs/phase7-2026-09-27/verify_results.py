"""Verify every exact Phase 6 baseline ID against a complete offline JUnit run."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
baseline = json.loads((HERE/'baseline-ids.json').read_text())['baseline_ids']
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'.tmp/phase7/full-suite-offline.xml'
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
              phase6_baseline_count=len(baseline),
              phase6_passing=sum(outcomes.get(i) == 'passed' for i in baseline),
              missing=sorted(set(baseline)-outcomes.keys()),
              nonpassing={i:outcomes.get(i) for i in baseline if outcomes.get(i) != 'passed'},
              new_ids=sorted(outcomes.keys()-set(baseline)),
              junit_seconds=sum(float(s.attrib['time']) for s in tree.findall('.//testsuite')),
              outcomes=outcomes)
(HERE/'test-results.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'outcomes','new_ids'}}, indent=2))
assert len(baseline) == 851 and result['phase6_passing'] == 851
assert result['passed'] == result['total']
