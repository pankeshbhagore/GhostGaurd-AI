"""Parsers for structured evidence: SQLite-compatible SQL snapshots, JSON bundles/exports and
ledger-style JSON / text records.

Replication drift
-----------------
SQL captures can hold a primary table and one or more replicas (regional, read, reporting,
failover).  A record that exists in *any* copy still exists, so every table is emitted as a
separate observation tagged with its topology; the state layer later unions them.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from ..model import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, IDENTIFIABLE, PRESENT,
                     REDACTED, RESTORABLE, Obs, classify_key, default_category, default_system)
from .logsem import ledger_obs

_NULL = (None, "", "null", "None")


def _keys_from(*vals: Any) -> frozenset:
    out = set()
    for v in vals:
        k = classify_key(v) if isinstance(v, str) else None
        if k:
            out.add(k)
    return frozenset(out)


def _has(*vals: Any) -> bool:
    return any(v not in _NULL for v in vals)


# ------------------------------------------------------------------ record -> obs
def record_obs(rec: Dict[str, Any], system: str, collection: str, file_id: str, captured_at: str,
               t: str, src: str, topo: str = "") -> Optional[Obs]:
    """Convert one structured record (any collection) into an observation."""
    rid = rec.get("resource_id")
    if not rid:
        return None
    base = dict(file_id=file_id, captured_at=captured_at, t=t or captured_at, resource_id=rid,
                src=src, topo=topo)
    coll = collection

    def mk(category: str, state: str, keys: frozenset, system_=None, attrs=None) -> Obs:
        return Obs(system=system_ or system or default_system(rid) or "", category=category, state=state,
                   keys=keys, attrs=attrs or {}, **base)

    if coll == "customer_profiles":
        return mk("USER_PROFILE", PRESENT, _keys_from(rec.get("email"), rec.get("phone"),
                  rec.get("customer_reference"), rec.get("user_id"), rec.get("email_hash")),
                  attrs={"status": rec.get("account_status", "")})
    if coll == "customer_search_documents":
        doc = rec.get("document") if isinstance(rec.get("document"), dict) else rec
        return mk("SEARCH_INDEX_ENTRY", PRESENT, _keys_from(doc.get("email"), doc.get("customer_reference"),
                  doc.get("phone"), doc.get("email_hash")))
    if coll in ("customer_analytics_reports", "customer_analytics_profiles"):
        ident = _has(rec.get("user_id"), rec.get("customer_reference"), rec.get("email"), rec.get("phone"),
                     rec.get("email_hash"))
        return mk("ANALYTICS_PROFILE", IDENTIFIABLE if ident else ANONYMIZED,
                  _keys_from(rec.get("email"), rec.get("phone"), rec.get("email_hash"), rec.get("user_id"),
                             rec.get("customer_reference")))
    if coll == "support_conversations":
        txt = str(rec.get("message_text", ""))
        red = "[redacted]" in txt.lower()
        return mk("SUPPORT_MESSAGE", REDACTED if red else EXPOSED,
                  _keys_from(rec.get("subject_reference")) | _emails_in_text(txt),
                  attrs={"ticket": rec.get("ticket_id", "")})
    if coll == "ticket_attachments":
        return mk("SUPPORT_ATTACHMENT", PRESENT, _keys_from(rec.get("uploaded_by")),
                  attrs={"ticket": rec.get("ticket_id", ""), "file": rec.get("file_name", "")})
    if coll == "backup_catalog":
        allowed = rec.get("restore_allowed")
        allowed = allowed if isinstance(allowed, bool) else str(allowed).lower() in ("true", "1")
        return mk("BACKUP_ARCHIVE", RESTORABLE if allowed else BLOCKED, _keys_from(rec.get("subject_reference")))
    if coll == "connector_configurations":
        en = rec.get("enabled")
        en = en if isinstance(en, bool) else str(en).lower() in ("true", "1")
        return mk("DATA_FLOW", ACTIVE if en else DISABLED, _keys_from(rec.get("subject_selector")),
                  attrs={"connector": rec.get("connector_name", "")})
    if coll == "retained_order_records":
        return mk("LEGAL_HOLD_RECORD", PRESENT, _keys_from(rec.get("subject_reference")),
                  attrs={"reason": rec.get("retention_reason", "")})
    if coll == "security_audit_events":
        return mk("SECURITY_AUDIT_LOG", PRESENT, _keys_from(rec.get("subject_reference")))
    if coll == "customer_export_jobs":
        avail = rec.get("download_available", True)
        return mk("USER_EXPORT", PRESENT, _keys_from(rec.get("owner_email")), attrs={"available": bool(avail)})
    if coll == "deleted_customer_references":
        ident = _has(rec.get("name"), rec.get("email"), rec.get("phone"), rec.get("email_hash"))
        return mk("ACCOUNT_TOMBSTONE", EXPOSED if ident else REDACTED,
                  _keys_from(rec.get("subject_reference"), rec.get("email"), rec.get("email_hash"), rec.get("phone")))
    if coll == "operational_record_history":
        return ledger_obs(file_id, captured_at, rec.get("recorded_at", t), rec.get("system", system), rid,
                          rec.get("record_type", ""), rec.get("result", ""),
                          rec.get("lookup_value") or rec.get("subject_reference", ""), src)
    return None


_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _emails_in_text(txt: str) -> frozenset:
    return frozenset(("email", m.lower()) for m in _EMAIL_IN_TEXT.findall(txt))


# ------------------------------------------------------------------------- SQL
_HEADER_TS = re.compile(r"Captured at:\s*(\S+)")


def sql_observations(path: str, file_id: str, captured_at: str) -> List[Obs]:
    text = open(path, encoding="utf-8", errors="replace").read()
    m = _HEADER_TS.search(text)
    snap_t = m.group(1) if m else captured_at
    con = sqlite3.connect(":memory:")
    con.executescript(text)
    out: List[Obs] = []
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    scope: Dict[str, str] = {}
    if "snapshot_scope" in tables:
        scope = {a: b for a, b in con.execute("select origin_system, captured_at from snapshot_scope")}

    def colnames(tbl: str) -> List[str]:
        return [c[1] for c in con.execute(f'pragma table_info("{tbl}")')]

    for tbl in tables:
        if tbl == "snapshot_scope":
            continue
        cols = colnames(tbl)
        rows = con.execute(f'select * from "{tbl}"').fetchall()
        topo = "primary" if (tbl.endswith("_primary") or tbl == "customer_data_primary") else f"replica:{tbl}"
        if "record_json" in cols:                                   # generic collection tables
            for row in rows:
                d = dict(zip(cols, row))
                try:
                    rec = json.loads(d["record_json"])
                except Exception:
                    continue
                rec.setdefault("resource_id", d["resource_id"])
                o = record_obs(rec, d["origin_system"], d["collection_name"], file_id, captured_at,
                               scope.get(d["origin_system"], snap_t), f"sql:{tbl}", topo)
                if o:
                    out.append(o)
            continue
        # typed inventory tables ---------------------------------------------------------
        for row in rows:
            d = dict(zip(cols, row))
            rec: Dict[str, Any] = dict(d)
            rid = d.get("resource_id")
            if not rid:
                continue
            if tbl in ("customer_profiles", "customer_profiles_primary", "customer_profiles_failover_replica"):
                rec["email"] = d.get("email")
                rec.setdefault("customer_reference", d.get("customer_reference") or d.get("user_id"))
                o = record_obs(rec, "account_service", "customer_profiles", file_id, captured_at,
                               scope.get("account_service", snap_t), f"sql:{tbl}", topo)
            elif tbl in ("customer_search_documents", "search_documents_primary"):
                rec = {"resource_id": rid, "document": {"email": d.get("email"), "customer_reference": d.get("customer_reference"),
                                                       "phone": d.get("phone"), "email_hash": d.get("email_hash")}}
                o = record_obs(rec, "search_service", "customer_search_documents", file_id, captured_at,
                               scope.get("search_service", snap_t), f"sql:{tbl}", topo)
            elif tbl == "customer_analytics_profiles":
                o = record_obs(rec, "analytics_platform", "customer_analytics_profiles", file_id, captured_at,
                               scope.get("analytics_platform", snap_t), f"sql:{tbl}", topo)
            elif tbl == "support_records":
                ident = _has(d.get("requester_name"), d.get("requester_email"), d.get("requester_phone"))
                o = Obs(file_id=file_id, captured_at=captured_at, t=scope.get("support_platform", snap_t),
                        system="support_platform", resource_id=rid, category="SUPPORT_MESSAGE",
                        state=EXPOSED if ident else REDACTED,
                        keys=_keys_from(d.get("requester_email"), d.get("requester_phone"), d.get("customer_reference")),
                        src=f"sql:{tbl}", topo=topo, attrs={"excerpt": d.get("message_excerpt", "")})
            elif tbl == "customer_exports":
                o = Obs(file_id=file_id, captured_at=captured_at, t=scope.get("export_service", snap_t),
                        system="export_service", resource_id=rid, category="USER_EXPORT", state=PRESENT,
                        keys=_keys_from(d.get("owner_email"), d.get("owner_phone"), d.get("customer_reference")),
                        src=f"sql:{tbl}", topo=topo)
            elif tbl == "connector_subscriptions":
                o = Obs(file_id=file_id, captured_at=captured_at, t=scope.get("integration_service", snap_t),
                        system="integration_service", resource_id=rid, category="DATA_FLOW",
                        state=ACTIVE if int(d.get("enabled") or 0) else DISABLED,
                        keys=_keys_from(d.get("customer_email"), d.get("customer_reference")),
                        src=f"sql:{tbl}", topo=topo, attrs={"connector": d.get("connector_name", "")})
            elif tbl == "backup_restore_entries":
                o = Obs(file_id=file_id, captured_at=captured_at, t=scope.get("backup_service", snap_t),
                        system="backup_service", resource_id=rid, category="BACKUP_ARCHIVE",
                        state=RESTORABLE if int(d.get("restore_allowed") or 0) else BLOCKED,
                        keys=_keys_from(d.get("customer_email"), d.get("customer_reference")),
                        src=f"sql:{tbl}", topo=topo)
            elif tbl == "security_audit_records":
                o = Obs(file_id=file_id, captured_at=captured_at, t=scope.get("security_service", snap_t),
                        system="security_service", resource_id=rid, category="SECURITY_AUDIT_LOG", state=PRESENT,
                        keys=_keys_from(d.get("subject_reference")), src=f"sql:{tbl}", topo=topo)
            else:
                continue
            if o:
                out.append(o)
    return out


def sql_scope(path: str, captured_at: str) -> Dict[str, str]:
    """Systems a SQL snapshot enumerates -> capture time (for absence-by-omission)."""
    text = open(path, encoding="utf-8", errors="replace").read()
    m = _HEADER_TS.search(text)
    snap_t = m.group(1) if m else captured_at
    con = sqlite3.connect(":memory:")
    con.executescript(text)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    scope: Dict[str, str] = {}
    if "snapshot_scope" in tables:
        scope.update({a: b for a, b in con.execute("select origin_system, captured_at from snapshot_scope")})
    tbl_sys = {"customer_profiles": "account_service", "customer_profiles_primary": "account_service",
               "customer_profiles_failover_replica": "account_service", "customer_search_documents": "search_service",
               "search_documents_primary": "search_service", "customer_analytics_profiles": "analytics_platform",
               "support_records": "support_platform", "customer_exports": "export_service",
               "connector_subscriptions": "integration_service", "backup_restore_entries": "backup_service",
               "security_audit_records": "security_service"}
    for t, s in tbl_sys.items():
        if t in tables:
            scope.setdefault(s, snap_t)
    for t in tables:
        cols = [c[1] for c in con.execute(f'pragma table_info("{t}")')]
        if "origin_system" in cols and t != "snapshot_scope":
            for (s,) in con.execute(f'select distinct origin_system from "{t}"'):
                scope.setdefault(s, snap_t)
    return scope


# ------------------------------------------------------------------------- JSON
def json_observations(path: str, file_id: str, captured_at: str) -> List[Obs]:
    d = json.load(open(path, encoding="utf-8"))
    out: List[Obs] = []
    fname = path.rsplit("/", 1)[-1]
    # 1) records_assurance snapshot bundle ------------------------------------------------------
    if isinstance(d, dict) and "snapshots" in d:
        for s in d["snapshots"]:
            store, coll, cap = s.get("data_store", ""), s.get("collection", ""), s.get("captured_at", captured_at)
            for rec in s.get("records", []):
                o = record_obs(rec, store, coll, file_id, captured_at, cap, f"json:{coll}")
                if o:
                    out.append(o)
        return out
    # 2) service_resource_export ---------------------------------------------------------------
    if isinstance(d, dict) and "service_exports" in d:
        exp_t = d.get("exported_at", captured_at)
        for sysname, cols in d["service_exports"].items():
            for col, recs in cols.items():
                for rec in recs:
                    t = rec.get("observed_at", exp_t)
                    rid = rec.get("resource_id", "")
                    if col == "profile_lookups":
                        prof = rec.get("profile")
                        keys = _keys_from(*(prof or {}).values()) if isinstance(prof, dict) else frozenset()
                        gone = prof is None or rec.get("status_code") == 404
                        out.append(Obs(file_id, captured_at, t, "account_service", rid, "USER_PROFILE",
                                       ABSENT if gone else PRESENT, keys, "json:profile_lookups"))
                    elif col == "profiles":
                        o = record_obs({**rec, "resource_id": rid}, sysname, "customer_analytics_profiles",
                                       file_id, captured_at, t, "json:profiles")
                        if o:
                            out.append(o)
                    elif col == "restore_entries":
                        allowed = bool(rec.get("restore_allowed"))
                        out.append(Obs(file_id, captured_at, t, "backup_service", rid, "BACKUP_ARCHIVE",
                                       RESTORABLE if allowed else BLOCKED,
                                       _keys_from(rec.get("customer_email"), rec.get("customer_reference")),
                                       "json:restore_entries"))
                    elif col == "exports":
                        out.append(Obs(file_id, captured_at, t, "export_service", rid, "USER_EXPORT", PRESENT,
                                       _keys_from(rec.get("owner_email"), rec.get("owner_phone"),
                                                  rec.get("customer_reference")), "json:exports"))
                    elif col == "connectors":
                        out.append(Obs(file_id, captured_at, t, "integration_service", rid, "DATA_FLOW",
                                       ACTIVE if rec.get("enabled") else DISABLED,
                                       _keys_from(rec.get("customer_email"), rec.get("customer_reference")),
                                       "json:connectors", attrs={"connector": rec.get("connector_name", "")}))
                    elif col == "index_queries":
                        docs = ((rec.get("hits") or {}).get("documents")) or []
                        for doc in docs:
                            out.append(Obs(file_id, captured_at, t, "search_service", doc.get("resource_id", rid),
                                           "SEARCH_INDEX_ENTRY", PRESENT,
                                           _keys_from(doc.get("email"), doc.get("customer_reference"),
                                                      doc.get("phone"), doc.get("email_hash")), "json:index_queries"))
                    elif col == "audit_records":
                        out.append(Obs(file_id, captured_at, t, "security_service", rid, "SECURITY_AUDIT_LOG", PRESENT,
                                       _keys_from(rec.get("subject_reference")), "json:audit_records"))
                    elif col == "tickets":
                        ident = _has(rec.get("requester_name"), rec.get("requester_email"), rec.get("requester_phone"))
                        out.append(Obs(file_id, captured_at, t, "support_platform", rid, "SUPPORT_MESSAGE",
                                       EXPOSED if ident else REDACTED,
                                       _keys_from(rec.get("requester_email"), rec.get("requester_phone"),
                                                  rec.get("customer_reference")), "json:tickets",
                                       attrs={"excerpt": rec.get("message_excerpt", "")}))
        return out
    # 3) ledger-style record list --------------------------------------------------------------
    if isinstance(d, dict) and isinstance(d.get("records"), list) and d["records"] and "record_type" in d["records"][0]:
        for rec in d["records"]:
            o = ledger_obs(file_id, captured_at, rec.get("recorded_at", captured_at), rec.get("system", ""),
                           rec.get("resource_id", ""), rec.get("record_type", ""), rec.get("result", ""),
                           rec.get("subject_reference", ""), "json:ledger")
            if o:
                out.append(o)
        return out
    # 4) single-collection records (analytics report / export jobs) -----------------------------
    if isinstance(d, dict) and isinstance(d.get("records"), list):
        coll = d.get("collection", "")
        store = d.get("source", "")
        for rec in d["records"]:
            o = record_obs(rec, "", coll, file_id, captured_at, d.get("captured_at", captured_at), f"json:{coll}")
            if o:
                o.system = default_system(o.resource_id) or o.system
                out.append(o)
    return out


# ------------------------------------------------------------------- plain text
_TXT_ROW = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*)$")


def txt_observations(path: str, file_id: str, captured_at: str) -> List[Obs]:
    out: List[Obs] = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = _TXT_ROW.match(line.strip())
        if not m:
            continue
        t, system, rid, rtype, result, subj = m.groups()
        o = ledger_obs(file_id, captured_at, t, system, rid, rtype, result, subj.strip(), "txt:ledger")
        if o:
            out.append(o)
    return out


def json_snaps(path: str, captured_at: str):
    """Enumerating snapshots of a JSON bundle: [(system, collection, snapshot time)].

    Only ``snapshots[]`` bundles enumerate; per-resource lookups/exports do not prove absence."""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    out = []
    if isinstance(d, dict) and isinstance(d.get("snapshots"), list):
        for sn in d["snapshots"]:
            if sn.get("data_store"):
                out.append((sn["data_store"], sn.get("collection", ""), sn.get("captured_at", captured_at)))
    return out
