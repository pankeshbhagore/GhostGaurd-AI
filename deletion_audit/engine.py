"""End-to-end audit pipeline: evidence -> identity -> linking -> state at audit_at -> policy -> plan."""
from __future__ import annotations

import csv
from datetime import timedelta
import os
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .activity import Activity, ActivityStream
from .actlink import link_accounts, link_anonymized, _m
from .casemodel import CaseView, Request, build_views, load_requests
from .evidence.artifacts import artifact_observations
from .evidence.loader import load_manifest, load_observations
from .identity import IdentityGraph
from .intent import classify_intent
from .model import Obs, default_category, default_system, fmt_ts, prefix_of
from .planner import Assessment, assess, build_steps, dumps
from .policy import Policy
from .state import StateEngine
from .timeutil import T

CHAIN_KINDS = {"REDACT_RECORD": "support", "DISABLE_DATA_FLOW": "data_flow", "BLOCK_RESTORE": "backup"}


class Audit:
    def __init__(self, dataset_dir: str, artifacts=None, omission: bool = True, strict_capture: bool = False, extra_requests: List[str] = ()):
        self.dir = dataset_dir
        j = lambda f: os.path.join(dataset_dir, f)
        self.manifest = load_manifest(dataset_dir)
        self.obs: List[Obs] = load_observations(dataset_dir, self.manifest, artifacts)
        self.graph = IdentityGraph(j("user_identities.csv"), j("user_audit_log.csv"))
        self.stream = ActivityStream(j("system_activity.csv"))
        self.obs += artifact_observations(dataset_dir, self.manifest, self.stream, self.graph)
        self.policy = Policy(j("deletion_rules.csv"))
        self.state = StateEngine(self.obs, self.manifest, omission=omission, strict_capture=strict_capture)
        self.extra = [load_requests(j(p)) for p in extra_requests]

    # ------------------------------------------------------------------ linking
    def link(self, reqs: List[Request]) -> List[CaseView]:
        views = build_views(reqs, self.graph, self.obs)
        for v in views:
            v.na = not classify_intent(v.req.text)[0]
            if v.na:
                v.resources.clear()
        self._time_filter(views)
        self._link_activity(views)
        return views

    def _resolve(self, v: CaseView, system: str, rid: str):
        q = v.req
        lr = v.resources.get((system, rid))
        return self.state.resolve(system, rid, fmt_ts(q.requested_at), fmt_ts(q.audit_at),
                                  exists_hint=bool(lr and lr.activity))

    def _valid(self, v: CaseView, system: str) -> List[Tuple[str, str]]:
        q = v.req
        out = []
        for (s, r) in v.resources:
            if s == system and self._resolve(v, s, r):
                out.append((s, r))
        return out

    def _time_filter(self, views: List[CaseView]) -> None:
        for v in views:
            q = v.req
            drop = [k for k in v.resources
                    if self._resolve(v, k[0], k[1]) is None]
            for k in drop:
                del v.resources[k]

    def _link_activity(self, views: List[CaseView]) -> None:
        linked_acc = {r for v in views for (s, r) in v.resources if s == "account_service"}
        needs = [i for i, v in enumerate(views) if v.subject and not v.na and not self._valid(v, "account_service")]

        def explicit(i, rid):
            q = views[i].req
            return any(q.requested_at <= T(o.t) <= q.audit_at for o in self.state.by_res.get(("account_service", rid), ()))

        files_of = {i: {o.file_id for lr in v.resources.values() for o in lr.obs} for i, v in enumerate(views)}

        def affinity(i, rid):
            q = views[i].req
            return any(q.requested_at <= T(o.t) <= q.audit_at and o.file_id in files_of[i]
                       for o in self.state.by_res.get(("account_service", rid), ()))

        compliant = {i for i in needs if not self._violations(views[i])}
        for i, e in link_accounts(views, needs, self.stream, linked_acc, compliant, explicit, affinity).items():
            self._attach(views[i], "account_service", e.resource_id, "USER_PROFILE", "activity", e)
        # compliant chain anchors (content-linked support/flow/backup with worker success events)
        chains: Dict[int, Dict[str, Activity]] = {}
        for i, v in enumerate(views):
            c = {}
            for (s, r) in v.resources:
                for e in self.stream.by_resource.get(r, ()):
                    k = CHAIN_KINDS.get(e.event_type)
                    if k and e.source == "deletion_worker" and e.result == "SUCCESS" and -26 <= _m(e, v.req.reported_at) <= -5:
                        c[k] = e
            if c:
                chains[i] = c
        linked_an = {r for v in views for (s, r) in v.resources if s == "analytics_platform"}
        # only cases whose known copies are all compliant can own a (key-less) anonymised analytics row:
        # a violating family keeps its analytics row identifiable, hence content-linked.
        eligible = {i for i, v in enumerate(views) if v.subject and not v.na and not self._violations(v)}
        for i, e in link_anonymized(views, self.stream, linked_an, chains, eligible).items():
            self._attach(views[i], "analytics_platform", e.resource_id, "ANALYTICS_PROFILE", "activity", e)

    def _fallback_cite(self, v: CaseView, res, lr) -> None:
        """Files captured after audit_at cannot be cited; cite the resource's own operational event instead."""
        if res.cite:
            return
        q = v.req
        evs = list(lr.activity) or self.stream.by_resource.get(res.resource_id, [])
        ok = [e for e in evs if q.requested_at <= e.t <= q.audit_at]
        if ok:
            res.cite = max(ok, key=lambda e: (e.t, e.activity_id)).activity_id

    def _violations(self, v: CaseView) -> bool:
        q = v.req
        for (s, r), lr in v.resources.items():
            res = self._resolve(v, s, r)
            if res is None:
                continue
            self._fallback_cite(v, res, lr)
            if not res.category:
                res.category = sorted(lr.categories)[0] if lr.categories else (default_category(r) or "")
            a = assess(res, self.policy)
            if a and not a.compliant:
                return True
        return False

    def _attach(self, v: CaseView, system, rid, cat, link, ev) -> None:
        q = v.req
        from .casemodel import LinkedResource
        k = (system, rid)
        lr = v.resources.get(k) or LinkedResource(system, rid, link=link)
        lr.activity.append(ev)
        lr.categories.add(cat)
        v.resources[k] = lr
        if not self._resolve(v, system, rid):
            del v.resources[k]

    # ------------------------------------------------------------------ audit
    def audit(self, v: CaseView) -> Tuple[str, List[dict]]:
        q = v.req
        if v.na:
            return "NOT_APPLICABLE", []
        ass: List[Assessment] = []
        for (s, r), lr in v.resources.items():
            res = self._resolve(v, s, r)
            if res is None:
                continue
            self._fallback_cite(v, res, lr)
            if not res.category:
                res.category = sorted(lr.categories)[0] if lr.categories else (default_category(r) or "")
            a = assess(res, self.policy)
            if a:
                ass.append(a)
        return build_steps(ass)
