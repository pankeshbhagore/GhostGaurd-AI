"""Semantic extraction from parsed log records -> observations.

A *trace* (all lines sharing a trace id) usually starts with a ``*_started`` line that
carries the lookup key (an e-mail, hash, ``user_NN`` reference, ...) and ends with a
``*_completed`` line that carries the resource id and the outcome.  Identity keys are
therefore attached to every observation of the same trace.  Ledger-style
``record_lookup`` lines carry everything on a single line.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from ..model import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, IDENTIFIABLE, PRESENT,
                     REDACTED, RESTORABLE, Obs, classify_key, default_category, default_system,
                     prefix_of)
from .logparse import LogRec, read_log

_NULLS = (None, "", "null", "None", "none", "-")


def _set(kv: Dict[str, str], name: str) -> bool:
    return kv.get(name) not in _NULLS


def _keys(values: Iterable[str]) -> frozenset:
    out = set()
    for v in values:
        k = classify_key(v)
        if k:
            out.add(k)
    return frozenset(out)


# Ledger record_type -> (category, {result -> state})
LEDGER_STATE = {
    "USER_PROFILE": {"FOUND": PRESENT, "NOT_FOUND": ABSENT, "ACTIVE": PRESENT, "DELETED": ABSENT},
    "SEARCH_INDEX_ENTRY": {"FOUND": PRESENT, "NOT_FOUND": ABSENT},
    "SECURITY_AUDIT_LOG": {"FOUND": PRESENT, "RETAINED": PRESENT, "NOT_FOUND": ABSENT},
    "DATA_FLOW": {"ACTIVE": ACTIVE, "DISABLED": DISABLED, "ENABLED": ACTIVE},
    "BACKUP_ARCHIVE": {"ACTIVE": RESTORABLE, "BLOCKED": BLOCKED, "RESTORABLE": RESTORABLE},
    "SUPPORT_MESSAGE": {"IDENTIFIABLE": EXPOSED, "REDACTED": REDACTED, "FOUND": EXPOSED, "NOT_FOUND": ABSENT},
    "SUPPORT_ATTACHMENT": {"FOUND": PRESENT, "NOT_FOUND": ABSENT, "IDENTIFIABLE": PRESENT},
    "ANALYTICS_PROFILE": {"IDENTIFIABLE": IDENTIFIABLE, "ANONYMIZED": ANONYMIZED, "NOT_FOUND": ABSENT, "FOUND": IDENTIFIABLE},
    "PRODUCT_TELEMETRY": {"IDENTIFIABLE": IDENTIFIABLE, "ANONYMIZED": ANONYMIZED},
    "USER_EXPORT": {"FOUND": PRESENT, "NOT_FOUND": ABSENT},
    "MARKETING_PROFILE": {"FOUND": PRESENT, "NOT_FOUND": ABSENT, "IDENTIFIABLE": PRESENT},
    "ACCOUNT_TOMBSTONE": {"IDENTIFIABLE": EXPOSED, "REDACTED": REDACTED, "FOUND": EXPOSED, "NOT_FOUND": ABSENT},
    "LEGAL_HOLD_RECORD": {"FOUND": PRESENT, "RETAINED": PRESENT, "ACTIVE": PRESENT},
    "CACHE_ENTRY": {"FOUND": PRESENT, "NOT_FOUND": ABSENT},
    "EMAIL_DELIVERY_LOG": {"IDENTIFIABLE": EXPOSED, "REDACTED": REDACTED, "FOUND": EXPOSED},
    "SECONDARY_IDENTIFIER": {"FOUND": PRESENT, "NOT_FOUND": ABSENT},
}


def ledger_obs(file_id: str, captured_at: str, t: str, system: str, rid: str, rtype: str,
               result: str, subject: str, src: str, extra: Optional[dict] = None) -> Optional[Obs]:
    rtype = (rtype or "").upper()
    result = (result or "").upper()
    states = LEDGER_STATE.get(rtype)
    if states is None or result not in states:
        return None
    keys = _keys([subject]) if subject else frozenset()
    return Obs(file_id=file_id, captured_at=captured_at, t=t or captured_at,
               system=system or default_system(rid) or "", resource_id=rid, category=rtype,
               state=states[result], keys=keys, src=src, attrs=dict(extra or {}))


def _trace_groups(recs: List[LogRec]) -> Dict[str, List[LogRec]]:
    groups: Dict[str, List[LogRec]] = defaultdict(list)
    for r in recs:
        if r.trace:
            groups[r.trace].append(r)
    return groups


def log_observations(path: str, file_id: str, captured_at: str) -> List[Obs]:
    recs = read_log(path)
    out: List[Obs] = []
    groups = _trace_groups(recs)

    # keys by trace: every identity-like value in the whole trace except resource ids
    trace_keys: Dict[str, frozenset] = {}
    for tr, lines in groups.items():
        vals = []
        for r in lines:
            for k, v in r.kv.items():
                if k in ("resource_id", "instance", "pod", "region", "file", "archive_key", "object_key"):
                    continue
                vals.append(v)
        trace_keys[tr] = _keys(vals)

    for r in recs:
        kv = r.kv
        ev = r.event
        rid = kv.get("resource_id", "")
        # ------------------------------------------------------ ledger style ----------
        if ev == "record_lookup":
            o = ledger_obs(file_id, captured_at, r.ts, kv.get("system", ""), rid, kv.get("record_type", ""),
                           kv.get("result", ""), kv.get("subject_reference", ""), "log:record_lookup")
            if o:
                out.append(o)
            continue
        if not rid:
            continue
        keys = trace_keys.get(r.trace, frozenset()) if r.trace else _keys(kv.values())
        own = _keys([kv.get(x, "") for x in ("lookup_key", "correlation_key", "customer_email", "requester_email",
                                              "owner_email", "email", "matched_value", "subject_ref",
                                              "customer_reference", "subject_reference", "email_hash")])
        keys = keys | own
        sysname = default_system(rid) or r.service
        src = f"log:{ev}"
        base = dict(file_id=file_id, captured_at=captured_at, t=r.ts, resource_id=rid, src=src)

        def add(category: str, state: str, system: Optional[str] = None, k: Optional[frozenset] = None,
                attrs: Optional[dict] = None):
            out.append(Obs(system=system or default_system(rid) or sysname, category=category, state=state,
                           keys=keys if k is None else k, attrs=attrs or {}, **base))

        # ------------------------------------------------------ accounts -------------
        if ev == "profile_lookup_completed":
            gone = kv.get("rows") == "0" or kv.get("http_status") == "404"
            add("USER_PROFILE", ABSENT if gone else PRESENT, "account_service")
        elif ev == "index_query_completed":
            add("SEARCH_INDEX_ENTRY", PRESENT if kv.get("result", "hit") == "hit" else ABSENT, "search_service")
        elif ev == "audit_record_read_completed":
            add("SECURITY_AUDIT_LOG", PRESENT if kv.get("rows", "1") != "0" else ABSENT, "security_service")
        elif ev == "retained_order_loaded":
            add("LEGAL_HOLD_RECORD", PRESENT, "legal_service")
        # ------------------------------------------------------ data flows -----------
        elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
            en = kv.get("enabled", "false").lower() == "true"
            sched = kv.get("scheduler_registered", "true").lower() != "false"
            add("DATA_FLOW", ACTIVE if (en and sched) else DISABLED, "integration_service",
                attrs={"connector": kv.get("connector_name", ""), "delivery": kv.get("last_delivery_status", "")})
        # ------------------------------------------------------ backups -------------
        elif ev == "restore_policy_read_completed":
            allowed = kv.get("restore_allowed", "false").lower() == "true" and kv.get("archive_present", "true").lower() == "true"
            add("BACKUP_ARCHIVE", RESTORABLE if allowed else BLOCKED, "backup_service")
        elif ev == "restore_authorization_completed":
            add("BACKUP_ARCHIVE", BLOCKED if kv.get("decision", "").lower() == "deny" else RESTORABLE, "backup_service")
        elif ev == "restore_candidate_evaluated":
            ok = kv.get("eligible", "false").lower() == "true" and kv.get("deletion_block", "false").lower() != "true"
            add("BACKUP_ARCHIVE", RESTORABLE if ok else BLOCKED, "backup_service")
        # ------------------------------------------------------ analytics ------------
        elif ev == "analytics_profile_read_completed":
            ident = any(_set(kv, x) for x in ("customer_reference", "email", "phone", "email_hash"))
            add("ANALYTICS_PROFILE", IDENTIFIABLE if ident else ANONYMIZED, "analytics_platform",
                attrs={"cohort": kv.get("audience_cohort", "")})
        elif ev == "analytics_profile_read":
            ident = kv.get("direct_identifiers", "0") not in ("0", "") or _set(kv, "email") or _set(kv, "phone")
            add("ANALYTICS_PROFILE", IDENTIFIABLE if ident else ANONYMIZED, "analytics_platform")
        elif ev == "analytics_identity_scan":
            add("ANALYTICS_PROFILE", IDENTIFIABLE, "analytics_platform")
        elif ev == "contact_lookup_completed":
            add("MARKETING_PROFILE", PRESENT, "analytics_platform", attrs={"opt_in": kv.get("email_opt_in", "")})
        # ------------------------------------------------------ support -------------
        elif ev == "support_ticket_read_completed":
            ident = any(_set(kv, x) for x in ("requester_name", "requester_email", "requester_phone"))
            add("SUPPORT_MESSAGE", EXPOSED if ident else REDACTED, "support_platform")
        elif ev == "message_pii_scan":
            add("SUPPORT_MESSAGE", EXPOSED, "support_platform")
        elif ev == "attachment_pii_scan":
            add("SUPPORT_ATTACHMENT", PRESENT, "support_platform", attrs={"file": kv.get("file", "")})
        elif ev == "message_read":
            red = "redacted" in kv.get("message_text", "").lower()
            add("SUPPORT_MESSAGE", REDACTED if red else EXPOSED, "support_platform")
        elif ev == "contact_query_completed":
            add("SUPPORT_ATTACHMENT", PRESENT if kv.get("rows") != "0" else ABSENT, "support_platform")
        # ------------------------------------------------------ exports -------------
        elif ev in ("export_download_check_completed", "export_metadata_read_completed"):
            avail = kv.get("available", "true").lower() == "true"
            add("USER_EXPORT", PRESENT if avail else ABSENT, "export_service")
    return out
