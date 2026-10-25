"""Per-case evidence view: which resources belong to the subject and what was observed."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .activity import Activity, ActivityStream
from .identity import IdentityGraph, Subject
from .model import EvidenceFile, Obs, parse_ts, prefix_of, EMAIL_RE


@dataclass
class Request:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    text: str
    raw: dict = field(default_factory=dict)


def load_requests(path: str) -> List[Request]:
    out: List[Request] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(Request(r["case_id"], r["email"].strip(), parse_ts(r["requested_at"]), r["reported_status"],
                               parse_ts(r["reported_at"]), parse_ts(r["audit_at"]), r["request_text"], r))
    return out


@dataclass
class LinkedResource:
    system: str
    resource_id: str
    categories: Set[str] = field(default_factory=set)
    obs: List[Obs] = field(default_factory=list)
    link: str = ""                       # how the resource was tied to the subject
    activity: List[Activity] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.system, self.resource_id)


@dataclass
class CaseView:
    req: Request
    subject: Optional[Subject]
    resources: Dict[Tuple[str, str], LinkedResource] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    na: bool = False                     # request does not authorise deletion

    def add(self, o: Obs, link: str):
        k = (o.system, o.resource_id)
        r = self.resources.get(k)
        if r is None:
            r = self.resources[k] = LinkedResource(o.system, o.resource_id, link=link)
        r.categories.add(o.category)
        r.obs.append(o)


_CATALOG_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CATALOG_RE_USER = re.compile(r"\buser_\d+\b")


def catalog_links(stream: ActivityStream, graph: IdentityGraph) -> Dict[str, Set[str]]:
    """resource_id -> {user_id}: explicit identity links published by the data catalog."""
    out: Dict[str, Set[str]] = defaultdict(set)
    for e in stream.events:
        if e.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        for em in _CATALOG_RE_EMAIL.findall(e.details):
            for s in graph.resolve_email(em):
                out[e.resource_id].add(s.user_id)
        for u in _CATALOG_RE_USER.findall(e.details):
            if u in graph.subjects:
                out[e.resource_id].add(u)
    return out


def build_views(reqs: List[Request], graph: IdentityGraph, obs: List[Obs]) -> List[CaseView]:
    """Content linking: attach every observation that carries one of the subject's identity keys."""
    idx: Dict[Tuple[str, str], List[Obs]] = defaultdict(list)
    for o in obs:
        for k in o.keys:
            idx[k].append(o)
    views: List[CaseView] = []
    for q in reqs:
        subs = graph.resolve_email(q.email)
        subj = subs[0] if subs else None
        v = CaseView(q, subj)
        if len(subs) > 1:
            v.notes.append("ambiguous-email:" + ",".join(s.user_id for s in subs))
        if subj:
            seen = set()
            for k in subj.keys():
                for o in idx.get(k, ()):
                    if id(o) in seen:
                        continue
                    seen.add(id(o))
                    v.add(o, "content:" + k[0])
        views.append(v)
    return views
