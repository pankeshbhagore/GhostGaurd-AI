"""State reconstruction: what was the state of a resource *at audit_at*?

Rules (all derived from the task statement):
* only information observed at or before ``audit_at`` may be used;
* the latest observation wins; when several copies exist at that moment (primary + replicas of
  the same snapshot) the *worst* one wins - a lingering replica copy is still a copy;
* an explicit "not found" is proof of absence; a *covering* snapshot that lists no row for a resource
  of the system it covers is proof of absence too (absence by omission) - but an empty export proves
  nothing, and an absence "observed" before the request even existed proves nothing either;
* a citation must be a file whose manifest ``captured_at`` is not after ``audit_at``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .model import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, IDENTIFIABLE, PRESENT, REDACTED,
                    RESTORABLE, EvidenceFile, Obs)
from .timeutil import T

# lower = better; used to pick the worst copy when several coexist at the same instant
_SEVERITY = {ABSENT: 0, REDACTED: 1, ANONYMIZED: 1, BLOCKED: 1, DISABLED: 1,
             PRESENT: 2, IDENTIFIABLE: 3, EXPOSED: 3, ACTIVE: 3, RESTORABLE: 3}


@dataclass
class Resolved:
    system: str
    resource_id: str
    category: str
    state: str
    t: str
    cite: str                      # evidence file id usable as citation ("" if none)
    via: str                       # "obs" | "omission"
    support: List[Obs] = field(default_factory=list)


class StateEngine:
    def __init__(self, obs: Iterable[Obs], manifest: Dict[str, EvidenceFile], omission: bool = True,
                 strict_capture: bool = False):
        self.manifest = manifest
        self.omission = omission
        self.strict_capture = strict_capture   # ablation: ignore files captured after audit_at entirely
        self.by_res: Dict[Tuple[str, str], List[Obs]] = defaultdict(list)
        self.file_rows: Dict[str, int] = defaultdict(int)
        self.listed_sql: Dict[Tuple[str, str], Set[str]] = defaultdict(set)            # (file, system)
        self.listed_snap: Dict[Tuple[str, str, str, str], Set[str]] = defaultdict(set)  # (file, system, coll, t)
        for o in obs:
            self.by_res[(o.system, o.resource_id)].append(o)
            self.file_rows[o.file_id] += 1
            if o.src.startswith("json:"):
                self.listed_snap[(o.file_id, o.system, o.src[5:], o.t)].add(o.resource_id)
            else:
                self.listed_sql[(o.file_id, o.system)].add(o.resource_id)
        # coverage: only NON-EMPTY exports can prove anything by omission
        self.cover_sql: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        self.cover_snap: Dict[str, List[Tuple[str, str, str, str]]] = defaultdict(list)
        for fid, ef in manifest.items():
            if self.file_rows.get(fid, 0) == 0:
                continue
            for system, t in (ef.scope or {}).items():
                self.cover_sql[system].append((t, fid))
            for system, coll, t in (ef.snaps or []):
                self.cover_snap[system].append((t, fid, coll, t))

    # ------------------------------------------------------------------------------
    def observations(self, system: str, rid: str, requested_at: str, audit_at: str,
                     exists_hint: bool = False) -> List[Obs]:
        """Usable observations (explicit + omission) of one resource within the case window."""
        lo, hi = T(requested_at), T(audit_at)
        out: List[Obs] = []
        existed = exists_hint
        colls: Set[str] = set()
        for o in self.by_res.get((system, rid), ()):
            t = T(o.t)
            if t > hi or (self.strict_capture and T(o.captured_at) > hi):
                continue
            if o.src.startswith("json:"):
                colls.add(o.src[5:])
            if t < lo:
                continue                      # before the request: another request's history, not this case
            if o.state != ABSENT:
                existed = True
            out.append(o)
        # omission is a *fallback*: it is only consulted when no explicit observation exists in the window
        in_window = any(T(o.t) >= lo for o in out)
        if self.omission and existed and not in_window:
            cat = out[0].category if out else ""
            seen_state = {(o.file_id, o.t) for o in out}
            for t, fid in self.cover_sql.get(system, ()):
                tt = T(t)
                if lo <= tt <= hi and rid not in self.listed_sql.get((fid, system), ()) and (fid, t) not in seen_state:
                    out.append(Obs(fid, self.manifest[fid].captured_at, t, system, rid, cat, ABSENT,
                                   frozenset(), "omission", {}, ""))
            for t, fid, coll, _ in self.cover_snap.get(system, ()):
                tt = T(t)
                if not (lo <= tt <= hi) or (colls and coll not in colls):
                    continue
                if rid in self.listed_snap.get((fid, system, coll, t), ()):
                    continue
                if any(rid in self.listed_snap.get((fid, system, c2, t), ()) for c2 in colls):
                    continue
                out.append(Obs(fid, self.manifest[fid].captured_at, t, system, rid, cat, ABSENT,
                               frozenset(), "omission", {}, ""))
        return out

    def _omission_cite(self, system: str, rid: str, lo, hi) -> str:
        best = None
        for t, fid in self.cover_sql.get(system, ()):
            tt = T(t); cap = T(self.manifest[fid].captured_at)
            if lo <= tt <= hi and cap <= hi and rid not in self.listed_sql.get((fid, system), ()):
                if best is None or (cap, tt) > best[0]: best = ((cap, tt), fid)
        for t, fid, coll, _ in self.cover_snap.get(system, ()):
            tt = T(t); cap = T(self.manifest[fid].captured_at)
            if lo <= tt <= hi and cap <= hi and not any(rid in v for k, v in self.listed_snap.items() if k[0] == fid and k[1] == system and k[3] == t):
                if best is None or (cap, tt) > best[0]: best = ((cap, tt), fid)
        return best[1] if best else ""

    def resolve(self, system: str, rid: str, requested_at: str, audit_at: str,
                exists_hint: bool = False) -> Optional[Resolved]:
        obs = self.observations(system, rid, requested_at, audit_at, exists_hint)
        if not obs:
            return None
        obs.sort(key=lambda o: (T(o.t), _SEVERITY.get(o.state, 2)))
        latest_t = T(obs[-1].t)
        # same instant (or same file) -> worst copy wins
        group = [o for o in obs if T(o.t) == latest_t]
        worst = max(group, key=lambda o: _SEVERITY.get(o.state, 2))
        final = worst.state
        category = next((o.category for o in reversed(obs) if o.category), "")
        hi = T(audit_at)
        usable = [o for o in obs if o.state == final and T(o.captured_at) <= hi]
        cite, via, support = "", worst.src == "omission" and "omission" or "obs", [worst]
        if usable:
            best = max(usable, key=lambda o: (T(o.captured_at), T(o.t)))
            cite = best.file_id
            support = [best]
        elif final == ABSENT:
            # the explicit "not found" line sits in a file captured after audit_at: it fixes the STATE (its event time is
            # <= audit_at) but cannot be cited; look for an enumerating snapshot captured in time that omits the record
            cite = self._omission_cite(system, rid, T(requested_at), hi)
        return Resolved(system, rid, category or worst.category, final, worst.t, cite, via, support)
