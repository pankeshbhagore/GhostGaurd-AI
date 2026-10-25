"""Turn reconstructed resource states + policy into findings, remediation and verification steps."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .model import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, IDENTIFIABLE, PRESENT, REDACTED,
                    RESTORABLE)
from .policy import Policy
from .state import Resolved

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]

REMEDIATION_FOR_END = {ABSENT: ("DELETE_RECORD", ABSENT), REDACTED: ("REDACT_RECORD", REDACTED),
                       ANONYMIZED: ("ANONYMIZE_RECORD", ANONYMIZED), DISABLED: ("DISABLE_DATA_FLOW", DISABLED),
                       BLOCKED: ("BLOCK_RESTORE", BLOCKED)}
VERIFY_FOR_END = {ABSENT: ("VERIFY_RECORD_ABSENT", ABSENT), PRESENT: ("VERIFY_RECORD_PRESENT", PRESENT),
                  REDACTED: ("VERIFY_RECORD_REDACTED", REDACTED), ANONYMIZED: ("VERIFY_RECORD_ANONYMIZED", ANONYMIZED),
                  DISABLED: ("VERIFY_DATA_FLOW_DISABLED", DISABLED), BLOCKED: ("VERIFY_RESTORE_BLOCKED", BLOCKED)}


@dataclass
class Assessment:
    res: Resolved
    end_state: str
    compliant: bool
    finding_op: Optional[Tuple[str, str]]     # (operation, state)
    verifiable: bool = True                   # False: required retained record is missing -> no verification step


def assess(res: Resolved, policy: Policy) -> Optional[Assessment]:
    rule = policy.rules.get(res.category)
    if rule is None:
        return None
    end = rule.end_state
    st = res.state
    if end == ABSENT:
        ok = st == ABSENT
        f = ("REPORT_REMAINING_RECORD", PRESENT)
    elif end in (REDACTED, ANONYMIZED):
        ok = st in (end, ABSENT)
        f = ("REPORT_EXPOSED_PII", PRESENT)
    elif end == PRESENT:
        # A required retained record that is missing cannot be repaired with the allowed operations and must not be
        # "verified" as present; it is left out of the plan (reported by diagnostics / the audit findings instead).
        return Assessment(res, end, True, None, verifiable=(st == PRESENT))
    elif end == DISABLED:
        ok = st in (DISABLED, ABSENT)           # a removed flow cannot copy or recreate data
        f = ("REPORT_ACTIVE_DATA_FLOW", ACTIVE)
    elif end == BLOCKED:
        ok = st in (BLOCKED, ABSENT)            # a deleted archive cannot be restored
        f = ("REPORT_RESTORE_RISK", ACTIVE)
    else:
        ok, f = True, None
    return Assessment(res, end, ok, None if ok else f)


def build_steps(assessments: List[Assessment]) -> Tuple[str, List[dict]]:
    """Return (deletion_status, steps) for the assessed resources of one case."""
    violations = [a for a in assessments if not a.compliant and a.finding_op]
    status = "INCOMPLETE" if violations else "COMPLETE"
    raw: List[Tuple[str, int, str, str, str, str, str]] = []   # phase,order,system,resource,op,state,evidence

    def key(op_order, op, a):
        return (op_order.index(op), a.res.system, a.res.resource_id)

    steps: List[dict] = []
    if violations:
        for op in FINDING_ORDER:
            for a in sorted((a for a in violations if a.finding_op[0] == op), key=lambda a: (a.res.system, a.res.resource_id)):
                steps.append(_s("FINDING", a.res, op, a.finding_op[1], a.res.cite))
        rem = []
        for a in violations:
            op, st = REMEDIATION_FOR_END[a.end_state]
            rem.append((REMEDIATION_ORDER.index(op), a.res.system, a.res.resource_id, op, st, a))
        for _, _, _, op, st, a in sorted(rem, key=lambda x: x[:3]):
            steps.append(_s("REMEDIATION", a.res, op, st, ""))
    ver = []
    for a in assessments:
        if a.end_state not in VERIFY_FOR_END or not a.verifiable:
            continue
        op, st = VERIFY_FOR_END[a.end_state]
        ver.append((VERIFICATION_ORDER.index(op), a.res.system, a.res.resource_id, op, st, a))
    for _, _, _, op, st, a in sorted(ver, key=lambda x: x[:3]):
        steps.append(_s("VERIFICATION", a.res, op, st, "" if violations else a.res.cite))
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return status, steps


def _s(phase, res: Resolved, op, state, evidence):
    return {"step_no": 0, "phase": phase, "system_id": res.system, "resource_id": res.resource_id,
            "operation": op, "state": state, "evidence_id": evidence}


def dumps(steps: List[dict]) -> str:
    return json.dumps(steps, separators=(",", ":"), ensure_ascii=False)
