"""Activity-stream linking for resources that carry *no* identifier in any evidence file.

Deleted account profiles and anonymised analytics rows hold no e-mail/hash/phone, so the only
thing tying them to a request is the deletion worker's timing: it runs a fixed pipeline relative
to the workflow's ``reported_at`` and its ``reported_result`` mirrors ``reported_status``.
The linker solves a one-to-one assignment problem:

* candidate events must sit in the pipeline time band of the request and carry the result the
  claimed status would produce,
* events whose resource is already content-linked to *another* subject are excluded,
* chain adjacency (fixed spacing between the worker steps of one case) pins analytics rows to the
  cases whose support / flow / backup steps are already known, and
* the remainder is solved by constraint propagation (an event only one case can take).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .activity import Activity, ActivityStream
from .casemodel import CaseView

RESULT_FOR_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
ACCOUNT_BAND = (-77.0, -42.0)       # minutes relative to reported_at
SMALL_BAND = 10.0                   # hand-made cases: worker finished within minutes of the report
ANON_BAND = (-31.0, -26.0)
# seconds from the anonymisation step to each later step of the same compliant chain
CHAIN_OFFSETS = {"support": 449.0, "data_flow": 839.0, "backup": 1288.0}
CHAIN_TOL = 20.0


def _m(a: Activity, ref) -> float:
    return (a.t - ref).total_seconds() / 60.0


def _propagate(cands: Dict[int, List[Activity]]) -> Dict[int, Optional[Activity]]:
    out: Dict[int, Optional[Activity]] = {}
    taken: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for c in sorted(cands):
            if c in out:
                continue
            free = [e for e in cands[c] if e.activity_id not in taken]
            if len(free) == 1:
                out[c] = free[0]
                taken.add(free[0].activity_id)
                changed = True
        demand: Dict[str, List[int]] = defaultdict(list)
        for c in cands:
            if c not in out:
                for e in cands[c]:
                    if e.activity_id not in taken:
                        demand[e.activity_id].append(c)
        for eid, cs in demand.items():
            if len(cs) == 1 and eid not in taken and cs[0] not in out:
                out[cs[0]] = next(e for e in cands[cs[0]] if e.activity_id == eid)
                taken.add(eid)
                changed = True
    for c in sorted(cands):
        if c not in out:
            free = sorted((e for e in cands[c] if e.activity_id not in taken), key=lambda e: (e.t, e.activity_id))
            out[c] = free[0] if free else None
            if free:
                taken.add(free[0].activity_id)
    return out


def link_accounts(views: List[CaseView], needs: List[int], stream: ActivityStream, foreign: Set[str],
                  compliant: Set[int] = frozenset(), explicit=None, affinity=None) -> Dict[int, Activity]:
    """Assign key-less account deletions to cases.

    * ``compliant``: cases whose known copies show no violation.  A COMPLETE verdict must be citable, so
      such a case can only own an account the evidence actually shows (``explicit``); violating cases take
      what is left, preferring accounts shown in a file that also shows the case's other records
      (``affinity``).
    """
    events = [e for e in stream.worker if e.event_type == "DELETE_RECORD" and e.target == "account_service"
              and e.resource_id not in foreign]
    cands: Dict[int, List[Activity]] = {}
    for i in needs:
        q = views[i].req
        want = RESULT_FOR_STATUS[q.reported_status]
        cands[i] = [e for e in events if ACCOUNT_BAND[0] <= _m(e, q.reported_at) <= ACCOUNT_BAND[1]
                    and e.result == want and e.t <= q.audit_at]
    res: Dict[int, Optional[Activity]] = {}
    taken: Set[str] = set()
    if explicit is not None:
        phase_a = {i: ([e for e in cands[i] if explicit(i, e.resource_id)] or cands[i]) for i in needs if i in compliant}
        for i, e in _propagate(phase_a).items():
            if e is not None:
                res[i] = e
                taken.add(e.activity_id)
    rest = {}
    for i in needs:
        if i in res:
            continue
        cs = [e for e in cands[i] if e.activity_id not in taken]
        if affinity is not None and len(cs) > 1:
            best = [e for e in cs if affinity(i, e.resource_id)]
            cs = best or cs
        rest[i] = cs
    for i, e in _propagate(rest).items():
        if e is not None:
            res[i] = e
    # fallback band for hand-made cases: the worker finished within minutes of the report
    for i in needs:
        if res.get(i) is None:
            q = views[i].req
            taken = {e.activity_id for e in res.values() if e}
            cs = [e for e in events if abs(_m(e, q.reported_at)) <= SMALL_BAND and e.activity_id not in taken
                  and e.t <= q.audit_at]
            if len(cs) == 1:
                res[i] = cs[0]
    return {i: e for i, e in res.items() if e is not None}


def link_anonymized(views: List[CaseView], stream: ActivityStream, foreign: Set[str],
                    chain_events: Dict[int, Dict[str, Activity]], eligible: Set[int]) -> Dict[int, Activity]:
    """Anonymised (key-less) analytics rows: chain adjacency first, then band + uniqueness."""
    events = [e for e in stream.worker if e.event_type == "ANONYMIZE_RECORD" and e.result == "SUCCESS"
              and e.resource_id not in foreign]
    by_time = sorted(events, key=lambda e: e.t)
    out: Dict[int, Activity] = {}
    used: Set[str] = set()
    # 1. chain adjacency: pick, for every anchored case, the event whose spacing error is smallest;
    #    resolve conflicts globally by ascending error.
    pairs = []
    for i, chain in chain_events.items():
        for kind, off in CHAIN_OFFSETS.items():
            ev = chain.get(kind)
            if not ev:
                continue
            for e in by_time:
                err = abs((ev.t - e.t).total_seconds() - off)
                if err <= CHAIN_TOL:
                    pairs.append((err, i, e))
    for err, i, e in sorted(pairs, key=lambda x: (x[0], x[1], x[2].activity_id)):
        if i in out or e.activity_id in used:
            continue
        out[i] = e
        used.add(e.activity_id)
    # 2. band + propagation for the rest
    cands: Dict[int, List[Activity]] = {}
    for i, v in enumerate(views):
        if i in out or i not in eligible:
            continue
        q = v.req
        if q.reported_status not in RESULT_FOR_STATUS:
            continue
        cs = [e for e in events if e.activity_id not in used and ANON_BAND[0] <= _m(e, q.reported_at) <= ANON_BAND[1]
              and e.t <= q.audit_at]
        if cs:
            cands[i] = cs
    for i, e in _propagate(cands).items():
        if e is not None:
            out[i] = e
    return out
