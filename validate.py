#!/usr/bin/env python3
"""Independent output validator (imports nothing from deletion_audit/).

Two layers, reported separately:
  STRUCTURAL - the output contract (columns, order, JSON, fields, enums, numbering, op order, status rules).
  SEMANTIC   - cross-checks against the dataset: citations exist, are not future-dated, mention the resource,
               treatment matches deletion_rules.csv, findings/remediation/verification are mutually consistent.
A structurally valid file can still be semantically wrong; semantic WARNINGS mean "weakly supported".

    python code/validate.py [output.csv] [dataset_dir] [--structural-only] [--no-ocr]
Exit code 1 on any structural or semantic ERROR.
"""
import csv, json, os, re, subprocess, sys
from datetime import datetime, timezone


def ts(v):
    """UTC datetime for any ISO-8601 value (Z, offset or naive=UTC)."""
    d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE if os.path.isdir(os.path.join(HERE, "dataset")) else os.path.dirname(HERE)
OPS = {"FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
       "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
       "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                        "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]}
STATE = {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
         "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
         "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
         "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
         "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"}
PH = ["FINDING", "REMEDIATION", "VERIFICATION"]
KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
REM_TO_VER = {"DELETE_RECORD": "VERIFY_RECORD_ABSENT", "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
              "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED", "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
              "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED"}
FIND_TO_REM = {"REPORT_REMAINING_RECORD": {"DELETE_RECORD"}, "REPORT_EXPOSED_PII": {"REDACT_RECORD", "ANONYMIZE_RECORD"},
               "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"}, "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"}}
# resource-id prefix -> policy data category (naming convention of supplied ids)
PREFIX_CAT = {"account": "USER_PROFILE", "search": "SEARCH_INDEX_ENTRY", "support": "SUPPORT_MESSAGE",
              "analytics": "ANALYTICS_PROFILE", "audit_record": "SECURITY_AUDIT_LOG", "data_flow": "DATA_FLOW",
              "backup": "BACKUP_ARCHIVE", "export": "USER_EXPORT", "marketing": "MARKETING_PROFILE",
              "tombstone": "ACCOUNT_TOMBSTONE", "legal_hold": "LEGAL_HOLD_RECORD", "object": "SUPPORT_ATTACHMENT"}
ACTION_VERIFY = {"DELETE": {"VERIFY_RECORD_ABSENT"}, "REDACT": {"VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ABSENT"},
                 "ANONYMIZE": {"VERIFY_RECORD_ANONYMIZED", "VERIFY_RECORD_ABSENT"}, "RETAIN": {"VERIFY_RECORD_PRESENT"}}


def prefix(rid):
    return re.sub(r"_[0-9a-z]+$", "", rid)


class Report:
    def __init__(self):
        self.err, self.warn = [], []
    def e(self, m): self.err.append(m)
    def w(self, m): self.warn.append(m)


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evidence_text(ds, path, cache, ocr):
    if path in cache:
        return cache[path]
    full = os.path.join(ds, path); txt = ""
    try:
        if path.endswith((".log", ".json", ".sql", ".txt")):
            with open(full, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
        elif ocr and path.endswith(".pdf"):
            txt = subprocess.run(["pdftotext", "-layout", full, "-"], capture_output=True, text=True).stdout
        elif ocr and path.endswith(".png"):
            txt = subprocess.run(["tesseract", full, "-", "--psm", "6"], capture_output=True, text=True).stdout
    except Exception:
        txt = ""
    cache[path] = txt
    return txt


def validate(path, ds, structural_only=False, ocr=True):
    R = Report(); S = Report()                       # structural, semantic
    try:
        rows = read_csv(path)
        with open(path, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
    except Exception as ex:
        R.e(f"cannot read output: {ex}"); return R, S, {}
    reqs = {r["case_id"]: r for r in read_csv(os.path.join(ds, "deletion_requests.csv"))}
    if header != ["case_id", "deletion_status", "steps_json"]: R.e(f"header {header}")
    ids = [r["case_id"] for r in rows]
    if len(ids) != len(set(ids)): R.e("duplicate case ids")
    if ids != sorted(ids): R.e("rows not sorted by case_id")
    if set(ids) != set(reqs): R.e(f"case set mismatch: missing {sorted(set(reqs)-set(ids))[:5]} extra {sorted(set(ids)-set(reqs))[:5]}")
    if len(ids) != 518 and len(reqs) == 518: R.e(f"row count {len(ids)} != 518")
    man = {r["file_id"]: r for r in read_csv(os.path.join(ds, "evidence_files.csv"))}
    acts = {r["activity_id"]: r for r in read_csv(os.path.join(ds, "system_activity.csv"))}
    audits = {r["audit_id"] for r in read_csv(os.path.join(ds, "user_audit_log.csv"))}
    rules = {}
    for r in read_csv(os.path.join(ds, "deletion_rules.csv")): rules.setdefault(r["data_category"], r["required_action"])
    cache = {}; stats = {"cases": len(rows), "steps": 0, "citations": 0, "weak_citations": 0}
    for r in rows:
        c = r["case_id"]; st = r["deletion_status"]; q = reqs.get(c)
        if st not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"): R.e(f"{c} bad status {st!r}")
        try:
            steps = json.loads(r["steps_json"])
        except Exception:
            R.e(f"{c} steps_json is not valid JSON"); continue
        if not isinstance(steps, list): R.e(f"{c} steps_json not an array"); continue
        stats["steps"] += len(steps)
        if st == "NOT_APPLICABLE" and r["steps_json"].strip() != "[]": R.e(f"{c} NOT_APPLICABLE must be exactly []")
        last = None; ok_shape = True
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict) or set(s) != KEYS: R.e(f"{c} step {i} keys"); ok_shape = False; continue
            if s["step_no"] != i or isinstance(s["step_no"], bool): R.e(f"{c} step {i} step_no {s['step_no']}")
            if s["phase"] not in PH: R.e(f"{c} {i} phase"); ok_shape = False; continue
            if s["operation"] not in OPS[s["phase"]]: R.e(f"{c} {i} operation {s['operation']} not allowed in {s['phase']}"); ok_shape = False; continue
            if STATE[s["operation"]] != s["state"]: R.e(f"{c} {i} state {s['state']} for {s['operation']}")
            for f in ("system_id", "resource_id", "evidence_id"):
                if not isinstance(s[f], str): R.e(f"{c} {i} {f} not a string")
            if not s["system_id"] or not s["resource_id"]: R.e(f"{c} {i} empty system/resource")
            key = (PH.index(s["phase"]), OPS[s["phase"]].index(s["operation"]), s["system_id"], s["resource_id"])
            if last is not None and key < last: R.e(f"{c} {i} violates phase/operation/system/resource order")
            if last is not None and key == last: R.e(f"{c} {i} duplicate step")
            last = key
            e = s["evidence_id"]
            if st == "COMPLETE":
                if s["phase"] != "VERIFICATION": R.e(f"{c} {i} COMPLETE contains {s['phase']}")
                if not e: R.e(f"{c} {i} COMPLETE verification without evidence")
            if st == "INCOMPLETE":
                if s["phase"] == "FINDING" and not e: R.e(f"{c} {i} finding without evidence")
                if s["phase"] != "FINDING" and e: R.e(f"{c} {i} proposed {s['phase']} step must have empty evidence_id")
        if st == "COMPLETE" and not steps: R.e(f"{c} COMPLETE with no verification steps")
        if st == "INCOMPLETE":
            if not any(s["phase"] == "FINDING" for s in steps if isinstance(s, dict)): R.e(f"{c} INCOMPLETE without findings")
        if structural_only or not ok_shape or q is None or st == "NOT_APPLICABLE":
            continue
        # ----------------------------------------------------------- semantic layer
        audit_at = q["audit_at"]
        for i, s in enumerate(steps, 1):
            e = s["evidence_id"]
            if not e: continue
            stats["citations"] += 1
            if e.startswith("evidence_"):
                if e not in man: S.e(f"{c} {i} unknown file {e}"); continue
                if ts(man[e]["captured_at"]) > ts(audit_at): S.e(f"{c} {i} {e} captured {man[e]['captured_at']} after audit_at {audit_at}")
                txt = evidence_text(ds, man[e]["file_path"], cache, ocr)
                if s["resource_id"] not in txt:
                    if s["operation"] == "VERIFY_RECORD_ABSENT":
                        stats["absence_by_omission_citations"] = stats.get("absence_by_omission_citations", 0) + 1
                    else:
                        stats["weak_citations"] += 1
                        S.w(f"{c} {i} {e} does not literally mention {s['resource_id']} ({s['operation']})")
            elif e.startswith("activity_"):
                a = acts.get(e)
                if not a: S.e(f"{c} {i} unknown activity {e}"); continue
                if ts(a["event_time"]) > ts(audit_at): S.e(f"{c} {i} {e} after audit_at")
                if a["resource_id"] != s["resource_id"]: S.e(f"{c} {i} {e} concerns {a['resource_id']} not {s['resource_id']}")
            elif e.startswith("audit_"):
                if e not in audits: S.e(f"{c} {i} unknown audit id {e}")
            else:
                S.e(f"{c} {i} unrecognised evidence id {e!r}")
        by = lambda ph: [s for s in steps if s["phase"] == ph]
        finds, rems, vers = by("FINDING"), by("REMEDIATION"), by("VERIFICATION")
        vset = {(s["system_id"], s["resource_id"], s["operation"]) for s in vers}
        vres = {(s["system_id"], s["resource_id"]) for s in vers}
        if st == "COMPLETE" and (finds or rems): S.e(f"{c} COMPLETE with findings/remediation")
        for s in rems:                               # every remediation is verified afterwards
            if (s["system_id"], s["resource_id"], REM_TO_VER[s["operation"]]) not in vset:
                S.e(f"{c} remediation {s['operation']} {s['resource_id']} has no matching verification")
        rset = {(s["system_id"], s["resource_id"]) for s in rems}
        for s in finds:                              # every finding is remediated
            if not any((s["system_id"], s["resource_id"]) == k for k in rset):
                S.e(f"{c} finding {s['operation']} {s['resource_id']} has no remediation")
            elif not any(x["resource_id"] == s["resource_id"] and x["operation"] in FIND_TO_REM[s["operation"]] for x in rems):
                S.e(f"{c} finding {s['operation']} {s['resource_id']} remediated with an inconsistent operation")
        for s in rems:
            if not any(f["resource_id"] == s["resource_id"] and s["operation"] in FIND_TO_REM[f["operation"]] for f in finds):
                S.e(f"{c} remediation {s['operation']} {s['resource_id']} without a supporting finding")
        for s in vers:                               # verification matches the policy for that category
            cat = PREFIX_CAT.get(prefix(s["resource_id"]))
            act = rules.get(cat)
            if act is None and cat != "DATA_FLOW": S.w(f"{c} no policy category for {s['resource_id']}"); continue
            if cat in ("DATA_FLOW",):
                if s["operation"] != "VERIFY_DATA_FLOW_DISABLED": S.e(f"{c} data flow verified with {s['operation']}")
            elif cat == "BACKUP_ARCHIVE":
                if s["operation"] != "VERIFY_RESTORE_BLOCKED": S.e(f"{c} backup verified with {s['operation']}")
            elif s["operation"] not in ACTION_VERIFY.get(act, set()):
                S.e(f"{c} {s['resource_id']} policy {act} but verified with {s['operation']}")
        # one account verification expected for every deletion subject
        if not any(prefix(s["resource_id"]) in ("account",) for s in vers): S.w(f"{c} no account verification")
        for s in vers:
            if (s["system_id"], s["resource_id"]) not in vres: pass
    return R, S, stats


def main(argv=None):
    a = [x for x in (argv if argv is not None else sys.argv[1:]) if not x.startswith("--")]
    flags = [x for x in (argv if argv is not None else sys.argv[1:]) if x.startswith("--")]
    path = a[0] if a else os.path.join(ROOT, "output.csv")
    ds = a[1] if len(a) > 1 else os.path.join(ROOT, "dataset")
    R, S, stats = validate(path, ds, "--structural-only" in flags, "--no-ocr" not in flags)
    print(f"STRUCTURAL: {'OK' if not R.err else str(len(R.err)) + ' errors'}")
    for m in R.err[:30]: print("  ", m)
    if "--structural-only" not in flags:
        print(f"SEMANTIC:   {'OK' if not S.err else str(len(S.err)) + ' errors'}; {len(S.warn)} warnings; stats {stats}")
        for m in S.err[:30]: print("  ERR ", m)
        for m in S.warn[:10]: print("  warn", m)
    return 1 if (R.err or S.err) else 0

if __name__ == "__main__":
    sys.exit(main())
