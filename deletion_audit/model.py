"""Core data model shared by every stage of the pipeline.

Everything the agent learns from participant data is normalised into :class:`Obs`
(an *observation*): "at time ``t`` evidence file ``file_id`` showed resource
``resource_id`` of category ``category`` in state ``state``, linked to identity
keys ``keys``".  All later stages (identity linking, state reconstruction, policy,
planning) work on observations only, never on raw files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import FrozenSet, Optional, Tuple

# --------------------------------------------------------------------------- time
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def parse_ts(value: str) -> datetime:
    """Parse the ISO-8601 ``...Z`` timestamps used across the dataset (UTC)."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def looks_like_ts(value: str) -> bool:
    return bool(_TS.match(value.strip()))


# ----------------------------------------------------------------- resource kinds
# Data categories of deletion_rules.csv and the system that owns each of them.
CATEGORY_SYSTEM = {
    "USER_PROFILE": "account_service",
    "SECONDARY_IDENTIFIER": "account_service",
    "SEARCH_INDEX_ENTRY": "search_service",
    "CACHE_ENTRY": "cache_service",
    "SUPPORT_MESSAGE": "support_platform",
    "SUPPORT_ATTACHMENT": "support_platform",
    "ANALYTICS_PROFILE": "analytics_platform",
    "PRODUCT_TELEMETRY": "analytics_platform",
    "USER_EXPORT": "export_service",
    "MARKETING_PROFILE": "analytics_platform",
    "EMAIL_DELIVERY_LOG": "email_delivery_service",
    "SECURITY_AUDIT_LOG": "security_service",
    "LEGAL_HOLD_RECORD": "legal_service",
    "BACKUP_ARCHIVE": "backup_service",
    "ACCOUNT_TOMBSTONE": "identity_service",
    "DATA_FLOW": "integration_service",
}

# resource-id prefix -> default category (context may override, see evidence parsers)
PREFIX_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "support": "SUPPORT_MESSAGE",
    "analytics": "ANALYTICS_PROFILE",
    "export": "USER_EXPORT",
    "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "marketing": "MARKETING_PROFILE",
    "cache": "CACHE_ENTRY",
    "email_delivery": "EMAIL_DELIVERY_LOG",
}

PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
}

_PREFIX_RE = re.compile(r"^(account|search|support|analytics|export|data_flow|backup|audit_record|"
                        r"tombstone|legal_hold|marketing|cache|email_delivery|object)_")


def prefix_of(resource_id: str) -> Optional[str]:
    m = _PREFIX_RE.match(resource_id)
    return m.group(1) if m else None


def default_category(resource_id: str) -> Optional[str]:
    p = prefix_of(resource_id)
    return PREFIX_CATEGORY.get(p) if p else None


def default_system(resource_id: str) -> Optional[str]:
    p = prefix_of(resource_id)
    return PREFIX_SYSTEM.get(p) if p else None


# ------------------------------------------------------------- normalised states
PRESENT = "PRESENT"          # record exists
ABSENT = "ABSENT"           # record confirmed missing
EXPOSED = "EXPOSED"         # record exists and still carries direct identifiers
REDACTED = "REDACTED"       # record exists with identifiers stripped
IDENTIFIABLE = "IDENTIFIABLE"   # analytics-style record still attributable to a user
ANONYMIZED = "ANONYMIZED"   # record exists but unlinked from the user
ACTIVE = "ACTIVE"           # data flow can run
DISABLED = "DISABLED"       # data flow cannot run
RESTORABLE = "RESTORABLE"   # backup can be restored (restore access open)
BLOCKED = "BLOCKED"         # backup restore blocked


@dataclass(frozen=True)
class Key:
    """One identity key: kind in {email, hash, phone, uref, name, rid}."""
    kind: str
    value: str

    def __str__(self) -> str:  # pragma: no cover - debugging helper
        return f"{self.kind}:{self.value}"


@dataclass
class Obs:
    file_id: str                    # evidence file that proves this observation
    captured_at: str                # manifest capture time of that file
    t: str                          # observation time (falls back to captured_at)
    system: str
    resource_id: str
    category: str
    state: str
    keys: FrozenSet[Tuple[str, str]] = field(default_factory=frozenset)
    src: str = ""                   # parser / table that produced it (diagnostics)
    attrs: dict = field(default_factory=dict)
    # replica topology: "primary" | "replica:<name>" | "" (not applicable)
    topo: str = ""

    def ident(self) -> Tuple[str, str]:
        return (self.system, self.resource_id)


@dataclass
class EvidenceFile:
    file_id: str
    path: str
    origin: str
    captured_at: str
    kind: str                       # sql | json | log | txt | pdf | png
    # Resources a *snapshot-style* file enumerates per system (used for absence by omission)
    scope: dict = field(default_factory=dict)
    # enumerating JSON snapshots inside the file: (system, collection, snapshot time)
    snaps: list = field(default_factory=list)


# ---------------------------------------------------------------------- key utils
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UREF_RE = re.compile(r"^user_\d+$")
PHONE_RE = re.compile(r"^\+\d[\d\s().\-]{6,}$")


def norm_email(v: str) -> str:
    return v.strip().lower()


def norm_phone(v: str) -> str:
    return re.sub(r"[^\d+]", "", v)


def classify_key(v) -> Optional[Tuple[str, str]]:
    """Return ``(kind, normalised value)`` for a raw scalar, or ``None``."""
    if not isinstance(v, str):
        return None
    s = v.strip().strip('"').strip("'")
    if not s or s.lower() in ("null", "none", "n/a", "-", "[redacted]", "redacted"):
        return None
    if EMAIL_RE.match(s):
        return ("email", norm_email(s))
    if HASH_RE.match(s.lower()):
        return ("hash", s.lower())
    if UREF_RE.match(s):
        return ("uref", s)
    if PHONE_RE.match(s):
        return ("phone", norm_phone(s))
    return None
