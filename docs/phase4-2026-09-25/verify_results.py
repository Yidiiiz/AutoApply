"""Compare final JUnit outcomes against every saved Phase 3 test ID."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
baseline = json.loads((HERE / 'baseline-ids.json').read_text(encoding='utf-8'))['baseline_ids']
path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.tmp/phase4/full-suite.xml'
tree = ET.parse(path)
outcomes = {}
for case in tree.findall('.//testcase'):
    identifier = case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name']
    outcomes[identifier] = next((status for status in ('failure', 'error', 'skipped') if case.find(status) is not None), 'passed')
result = dict(total=len(outcomes), passed=sum(v == 'passed' for v in outcomes.values()),
              failures=sum(v == 'failure' for v in outcomes.values()),
              errors=sum(v == 'error' for v in outcomes.values()),
              skipped=sum(v == 'skipped' for v in outcomes.values()),
              phase3_baseline_count=len(baseline),
              phase3_passing=sum(outcomes.get(i) == 'passed' for i in baseline),
              missing=sorted(set(baseline) - outcomes.keys()),
              nonpassing={i: outcomes.get(i) for i in baseline if outcomes.get(i) != 'passed'},
              new_ids=sorted(outcomes.keys() - set(baseline)),
              junit_seconds=sum(float(s.attrib['time']) for s in tree.findall('.//testsuite')),
              outcomes=outcomes)
(HERE / 'test-results.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k not in {'outcomes', 'new_ids'}}, indent=2))
assert len(baseline) == 690 and result['phase3_passing'] == 690
assert result['passed'] == result['total']
