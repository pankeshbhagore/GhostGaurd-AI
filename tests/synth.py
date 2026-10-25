"""Builds tiny self-contained synthetic datasets (kept apart from the official samples)."""
import csv, hashlib, os, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
ROOT = PARENT if os.path.isdir(os.path.join(PARENT, "dataset")) else os.path.dirname(PARENT)
REAL = os.path.join(ROOT, "dataset")


def w(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        c = csv.writer(fh, lineterminator="\n"); c.writerow(header); c.writerows(rows)


class Builder:
    """users: [(user_id, name, email)], audit: [(user, time, field, old, new)], ledger: {file: (captured_at, [lines])}"""
    def __init__(self):
        self.users, self.audit, self.requests, self.activity = [], [], [], []
        self.files = []                      # (file_id, rel_path, captured_at, [ledger lines])

    def user(self, uid, name, email):
        self.users.append((uid, name, email, "+1 555 000 %04d" % len(self.users),
                           hashlib.sha256(email.encode()).hexdigest(), "2026-01-01T00:00:00Z"))

    def rename_email(self, uid, t, old, new):
        self.audit.append((f"audit_s{len(self.audit)+1}", uid, t, "email", old, new))

    def ledger(self, captured_at, lines):
        fid = f"evidence_{len(self.files)+1:02d}"
        self.files.append((fid, f"evidence/logs/synthetic_{fid}.log", captured_at, lines))
        return fid

    @staticmethod
    def rec(t, system, rid, rtype, result, subject, seq=1):
        return (f'{t} INFO service=synthetic event=record_lookup seq={seq} system="{system}" resource_id="{rid}" '
                f'record_type="{rtype}" result="{result}" subject_reference="{subject}"')

    def request(self, cid, email, req_at, aud_at, text="Please delete my account and all personal data.", rs="COMPLETE"):
        self.requests.append((cid, email, req_at, rs, req_at, aud_at, text))

    def build(self, root=None):
        root = root or tempfile.mkdtemp(prefix="synth_")
        ds = os.path.join(root, "dataset"); os.makedirs(ds, exist_ok=True)
        shutil.copy(os.path.join(REAL, "deletion_rules.csv"), ds)
        w(os.path.join(ds, "user_identities.csv"), ["user_id", "name", "email", "phone", "email_hash", "updated_at"], self.users)
        w(os.path.join(ds, "user_audit_log.csv"), ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], self.audit)
        w(os.path.join(ds, "system_activity.csv"), ["activity_id", "event_time", "event_type", "source_system", "target_system",
                                                    "resource_id", "reported_result", "details"], self.activity)
        hdr = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
        for n in ("deletion_requests.csv", "sample_deletion_requests.csv"):
            w(os.path.join(ds, n), hdr, self.requests if n == "deletion_requests.csv" else [])
        man = []
        for fid, rel, cap, lines in self.files:
            p = os.path.join(ds, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            man.append((fid, rel, "synthetic_capture", cap))
        w(os.path.join(ds, "evidence_files.csv"), ["file_id", "file_path", "origin", "captured_at"], man)
        return ds
