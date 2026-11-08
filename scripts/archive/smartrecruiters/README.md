# SmartRecruiters #5310 incident tools

These four tools preserve the bounded historical incident described in the
[repair report](../../../docs/history/smartrecruiters-5310-repair.md).

| Tool | Historical purpose |
| --- | --- |
| `audit_smartrecruiters_5310.py` | Compare isolation snapshots and write the incident audit |
| `smartrecruiters_5310_once.py` | One authorized reconstruction and guarded continuation |
| `finalize_smartrecruiters_5310_hold.py` | Record the restriction and unavailable session |
| `repair_5310_once.py` | Bounded eligibility repair and controlled run |

Archiving does not renew the original authorization. The recorded session ended;
the report permits no further reconstruction under that run. Any future live
work requires separate authorization and the existing safeguards.

Do not import these tools for inspection: the audit and finalization modules
access private storage and write records at module level. Use source review,
AST parsing or compilation only. They are excluded from normal startup and are
not general automation entry points.

Each tool resolves the shared module in `../../controlled_once.py`; that module
continues to determine the repository root. The reconstruction tool also resolves
its own root from this archive location. Existing guards and incident evidence
paths are retained.
