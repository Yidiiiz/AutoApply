"""Compare rerun work counts with preserved phase evidence; timings are separate."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def differences(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            if key == 'seconds':
                continue
            result += differences(before.get(key), after.get(key), prefix+'/'+key)
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [d for i, (b, a) in enumerate(zip(before, after))
                for d in differences(b, a, prefix+'/'+str(i))]
    return [] if before == after else [dict(path=prefix, before=before, after=after)]


pairs = {
    'phase3': ('phase7-2026-09-27/maintenance-after.json', 'samples'),
    'phase4': ('phase7-2026-09-27/persistence-after.json', 'workloads'),
    'phase5': ('phase5-2026-09-25/after.json', None),
    'phase6': ('phase6-2026-09-26/after.json', None),
    'phase7': ('phase7-2026-09-27/after.json', None),
    'control-work': ('phase7-2026-09-27/control-work.json', None),
    'phase8': ('phase8-2026-09-27/after.json', None),
    'ratio': ('phase8-2026-09-27/ratio.json', None),
}
report = {}
for name, (prior, key) in pairs.items():
    before, after = read(DOCS/prior), read(HERE/(name+'.json'))
    if key:
        before, after = before[key], after[key]
    report[name] = dict(baseline=prior, non_timing_differences=differences(before, after))
(HERE/'measurement-comparison.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
