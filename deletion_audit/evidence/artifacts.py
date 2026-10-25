"""PDF / screenshot evidence -> observations.

Business artifacts (console screenshots, statements, attachments) carry no deletion verdict; they show
a record.  Two grounded ways connect them to a subject:

1. **Catalog links** - ``LINK_RESOURCE_IDENTITY`` activity rows ("Data catalog linked <resource> to
   <email> and <user_id>") are emitted a fixed 7 minutes after the artifact that proves the record,
   so link time - 7 min == artifact ``captured_at`` binds artifact, resource and subject.
2. **Single-record documents** - a document that names exactly one resource id (plus identifiers)
   is a view of that record; every identifier in it belongs to it.

The visible text is extracted with pdftotext / tesseract (no network, no API key) and the record state
is read from words on the page (e.g. "Continuous sync", "Retained reference", "Ready").
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from datetime import timedelta
from functools import lru_cache
from typing import Dict, List, Optional

from ..activity import ActivityStream
from ..identity import IdentityGraph
from ..model import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, IDENTIFIABLE, PRESENT, REDACTED,
                     RESTORABLE, EvidenceFile, Obs, PREFIX_SYSTEM, classify_key, fmt_ts, parse_ts, prefix_of)

_RID = re.compile(r"\b(account|search|support|analytics|export|data_flow|backup|audit_record|tombstone|legal_hold|"
                  r"marketing|cache|email_delivery|object)_(\d+)\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_UREF = re.compile(r"\buser_\d+\b")
_PHONE = re.compile(r"\+\d[\d ()\-]{7,}\d")
_LINK_RE = re.compile(r"linked (\S+) \(.*?\) to (\S+) and (user_\d+)")
LINK_LAG = timedelta(minutes=7)


EXTRACTION_LOG = {}          # path -> "ok" | "empty" | "no-extractor" | "error"  (read by diagnostics.py)


def _cache_file(path: str, tool: str):
    """Content-hash cache of OCR/PDF text.  Disabled with GHOSTGUARD_NO_CACHE=1; dir via GHOSTGUARD_CACHE_DIR."""
    if os.environ.get("GHOSTGUARD_NO_CACHE") == "1":
        return None
    base = os.environ.get("GHOSTGUARD_CACHE_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), ".cache", "ocr")
    try:
        with open(path, "rb") as fh:
            h = hashlib.sha256(fh.read()).hexdigest()
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{tool}-{h}.txt")
    except OSError:
        return None


@lru_cache(maxsize=None)
def extract_text(path: str) -> str:
    """Visible text of a PDF/PNG (empty string when no extractor is installed)."""
    pdf = path.lower().endswith(".pdf")
    tool = "pdftotext" if pdf else "tesseract"
    if not shutil.which(tool):
        EXTRACTION_LOG[path] = "no-extractor"
        return ""
    cf = _cache_file(path, tool)
    if cf and os.path.exists(cf):
        with open(cf, encoding="utf-8") as fh:
            txt = fh.read()
        EXTRACTION_LOG[path] = "ok" if txt.strip() else "empty"
        return txt
    try:
        cmd = ["pdftotext", "-layout", path, "-"] if pdf else ["tesseract", path, "-", "--psm", "6"]
        txt = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    except Exception:
        EXTRACTION_LOG[path] = "error"
        return ""
    if cf:
        try:
            with open(cf, "w", encoding="utf-8") as fh:
                fh.write(txt)
        except OSError:
            pass
    EXTRACTION_LOG[path] = "ok" if txt.strip() else "empty"
    return txt


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def state_from_text(rid: str, text: str, has_direct_identifier: bool):
    """Read the state of ``rid`` from the words of the page.  Returns (category, state)."""
    p = prefix_of(rid)
    t = text.lower()
    if p == "account":
        return "USER_PROFILE", PRESENT
    if p == "search":
        return "SEARCH_INDEX_ENTRY", PRESENT
    if p == "data_flow":
        off = re.search(r"\b(paused|disabled|stopped|suspended|inactive)\b", t)
        return "DATA_FLOW", DISABLED if off and not re.search(r"continuous|healthy", t) else ACTIVE
    if p == "analytics":
        return "ANALYTICS_PROFILE", IDENTIFIABLE if has_direct_identifier else ANONYMIZED
    if p == "export":
        return "USER_EXPORT", PRESENT
    if p == "support":
        if "attachment" in t or ".pdf" in t:
            return "SUPPORT_ATTACHMENT", PRESENT
        return "SUPPORT_MESSAGE", EXPOSED if has_direct_identifier else REDACTED
    if p == "backup":
        blocked = re.search(r"restore (blocked|disabled|denied)|deletion[ _]block", t)
        return "BACKUP_ARCHIVE", BLOCKED if blocked else RESTORABLE
    if p == "object":
        return "SUPPORT_ATTACHMENT", PRESENT
    if p == "marketing":
        return "MARKETING_PROFILE", PRESENT
    if p == "audit_record":
        return "SECURITY_AUDIT_LOG", PRESENT
    if p == "tombstone":
        return "ACCOUNT_TOMBSTONE", EXPOSED if has_direct_identifier else REDACTED
    if p == "legal_hold":
        return "LEGAL_HOLD_RECORD", PRESENT
    return "", PRESENT


def artifact_observations(dataset_dir: str, manifest: Dict[str, EvidenceFile], stream: ActivityStream,
                          graph: IdentityGraph) -> List[Obs]:
    out: List[Obs] = []
    arts = {fid: ef for fid, ef in manifest.items() if ef.kind in ("pdf", "png")}
    texts = {fid: _norm(extract_text(os.path.join(dataset_dir, ef.path))) for fid, ef in arts.items()}
    claimed = set()
    # ---- 1. catalog links ------------------------------------------------------------
    for e in stream.events:
        if e.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        rid = e.resource_id
        em = _EMAIL.search(e.details)
        um = _UREF.search(e.details)
        subj_email = em.group(0).lower() if em else ""
        uref = um.group(0) if um else ""
        want = e.t - LINK_LAG
        best = None
        for fid, ef in arts.items():
            dt = abs((parse_ts(ef.captured_at) - want).total_seconds())
            if dt <= 120 and (best is None or dt < best[0]):
                best = (dt, fid)
        if best is None:
            continue
        fid = best[1]
        claimed.add(fid)
        ef = arts[fid]
        text = texts[fid]
        keys = set()        # only the identifiers the catalog attests - a screen also shows other people's rows
        k = classify_key(subj_email) if subj_email else None
        if k:
            keys.add(k)
        if uref:
            keys.add(("uref", uref))
        # identifiers shown on the page that belong to the linked subject
        direct = bool(subj_email and subj_email in text.lower()) or bool(_PHONE.search(text))
        if prefix_of(rid) == "tombstone":
            body = text.lower()
            direct = bool(re.search(r"@|\+\d{6,}|email|phone|full name", body.replace("customer key", "")))
        cat, state = state_from_text(rid, text, direct)
        system = e.target if e.target not in ("object_storage",) else "object_storage"
        system = PREFIX_SYSTEM.get(prefix_of(rid) or "", system) if prefix_of(rid) != "object" else "object_storage"
        out.append(Obs(fid, ef.captured_at, ef.captured_at, system, rid, cat, state, frozenset(keys),
                       "artifact:link", {"link": e.activity_id}, ""))
    # ---- 2. single-record documents ---------------------------------------------------
    for fid, ef in arts.items():
        if fid in claimed:
            continue
        text = texts[fid]
        ids = {m.group(0) for m in _RID.finditer(text)}
        ids = {i for i in ids if prefix_of(i) not in ("object",)}
        if len(ids) != 1:
            continue
        rid = next(iter(ids))
        keys = set()
        for x in _EMAIL.findall(text) + _UREF.findall(text):
            k = classify_key(x)
            if k:
                keys.add(k)
        direct = bool(_EMAIL.search(text) or _PHONE.search(text))
        cat, state = state_from_text(rid, text, direct)
        if prefix_of(rid) == "audit_record":
            direct = False
        out.append(Obs(fid, ef.captured_at, ef.captured_at, PREFIX_SYSTEM.get(prefix_of(rid), ""), rid, cat, state,
                       frozenset(keys), "artifact:document", {}, ""))
    return out
