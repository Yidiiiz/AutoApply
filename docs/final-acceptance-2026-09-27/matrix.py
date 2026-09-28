"""Bind report safety references to exact executed baseline/audit test IDs."""
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
outcomes = json.loads((HERE/'test-results.json').read_text())['outcomes']
audit = ET.parse(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'.tmp/final-acceptance-2026-09-27/audit.xml')
audit_outcomes = {}
for case in audit.findall('.//testcase'):
    key = case.attrib['classname'].replace('.', '/')+'.py::'+case.attrib['name']
    audit_outcomes[key] = next((s for s in ('failure','error','skipped') if case.find(s) is not None), 'passed')
(HERE/'audit-results.json').write_text(json.dumps(audit_outcomes, indent=2)+'\n')
rows = []
for line in (ROOT/'docs/final-acceptance-2026-09-27.md').read_text(encoding='utf-8').splitlines():
    if not re.match(r'\| S\d+ \|', line):
        continue
    identifier, enforcement, references, secondary = [p.strip() for p in line.split('|')[1:-1]]
    matches = {}
    current_file = None
    for ref in re.findall(r'`([^`]+)`', references):
        if '.py::' in ref:
            current_file, name = ref.split('::')
        else:
            name = ref
        assert name.startswith('test_'), ref
        prefix = 'tests/'+current_file+'::'+name
        found = {k:v for k,v in outcomes.items() if k == prefix or k.startswith(prefix+'[')}
        assert found, (identifier, ref)
        matches.update(found)
    rows.append(dict(id=identifier, production_enforcement=enforcement, primary_outcomes=matches,
                     secondary_defense_or_limit=secondary,
                     validation_only=identifier=='S25',
                     result='PASS' if all(v=='passed' for v in matches.values()) else 'FAIL'))
assert len(rows) == 25
(HERE/'safety-matrix.json').write_text(json.dumps(rows, indent=2)+'\n', encoding='utf-8')
print(json.dumps(dict(invariants=len(rows), failed=[r['id'] for r in rows if r['result']!='PASS'],
                      audit_cases=len(audit_outcomes), audit_nonpassing={k:v for k,v in audit_outcomes.items() if v!='passed'})))
assert len(audit_outcomes)==12 and all(v=='passed' for v in audit_outcomes.values())
