"""Identity graph: current + historical identifiers of every user.

``user_identities.csv`` is the *current* snapshot; ``user_audit_log.csv`` records historical
changes of e-mail, e-mail hash and phone.  A deletion request may name a current or a
historical e-mail, so each user is expanded into every identifier that may still be stored in a
secondary system (old e-mails, their SHA-256 hashes, old phones, the stable ``user_id``).
"""
from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .model import classify_key, norm_email, norm_phone


def sha256_email(email: str) -> str:
    return hashlib.sha256(norm_email(email).encode("utf-8")).hexdigest()


@dataclass
class Subject:
    user_id: str
    name: str = ""
    emails: Set[str] = field(default_factory=set)      # current + historical
    hashes: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    current_email: str = ""
    history: List[dict] = field(default_factory=list)   # audit-log rows (audit ids)

    def keys(self) -> FrozenSet[Tuple[str, str]]:
        ks = {("uref", self.user_id)}
        ks |= {("email", e) for e in self.emails}
        ks |= {("hash", h) for h in self.hashes}
        ks |= {("phone", p) for p in self.phones}
        return frozenset(ks)

    def strong_keys(self) -> FrozenSet[Tuple[str, str]]:
        return frozenset(k for k in self.keys() if k[0] in ("email", "hash", "uref"))


class IdentityGraph:
    def __init__(self, identities_csv: str, audit_csv: str):
        self.subjects: Dict[str, Subject] = {}
        self.by_email: Dict[str, Set[str]] = defaultdict(set)
        self.by_hash: Dict[str, Set[str]] = defaultdict(set)
        self.by_phone: Dict[str, Set[str]] = defaultdict(set)
        with open(identities_csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                s = self.subjects.setdefault(r["user_id"], Subject(r["user_id"]))
                s.name = r.get("name", "")
                e = norm_email(r["email"])
                s.current_email = e
                s.emails.add(e)
                s.hashes.add((r.get("email_hash") or sha256_email(e)).lower())
                s.hashes.add(sha256_email(e))
                if r.get("phone"):
                    s.phones.add(norm_phone(r["phone"]))
        with open(audit_csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                s = self.subjects.setdefault(r["user_id"], Subject(r["user_id"]))
                s.history.append(r)
                f = r["field_name"]
                for v in (r["old_value"], r["new_value"]):
                    if not v:
                        continue
                    if f == "email":
                        e = norm_email(v)
                        s.emails.add(e)
                        s.hashes.add(sha256_email(e))
                    elif f == "email_hash":
                        s.hashes.add(v.strip().lower())
                    elif f == "phone":
                        s.phones.add(norm_phone(v))
        for uid, s in self.subjects.items():
            for e in s.emails:
                self.by_email[e].add(uid)
            for h in s.hashes:
                self.by_hash[h].add(uid)
            for p in s.phones:
                self.by_phone[p].add(uid)

    # ------------------------------------------------------------------ resolution
    def resolve_email(self, email: str) -> List[Subject]:
        """Users that ever owned ``email`` (current holder first)."""
        e = norm_email(email)
        ids = set(self.by_email.get(e, ()))
        ids |= self.by_hash.get(sha256_email(e), set())
        cur = sorted(u for u in ids if self.subjects[u].current_email == e)
        old = sorted(u for u in ids if u not in cur)
        return [self.subjects[u] for u in cur + old]

    def resolve_key(self, kind: str, value: str) -> Set[str]:
        if kind == "email":
            return set(self.by_email.get(value, ()))
        if kind == "hash":
            return set(self.by_hash.get(value, ()))
        if kind == "uref":
            return {value} if value in self.subjects else set()
        if kind == "phone":
            return set(self.by_phone.get(value, ()))
        return set()
