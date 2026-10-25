#!/usr/bin/env python3
"""Four team-made evaluation cases (run: python code/evaluation/custom_cases.py).

Expectations hold by construction (invariants of the task), so no hidden labels are used."""
import csv, json, os, sys, dataclasses
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
ROOT = PARENT if os.path.isdir(os.path.join(PARENT, "dataset")) else os.path.dirname(PARENT)
CODE = PARENT if os.path.isdir(os.path.join(PARENT, "deletion_audit")) else os.path.join(ROOT, "code")
sys.path.insert(0, CODE)
from deletion_audit.casemodel import load_requests
from deletion_audit.engine import Audit
from deletion_audit.model import parse_ts
from deletion_audit.planner import dumps

DS = os.path.join(ROOT, "dataset")
audit = Audit(DS)
base = {r.case_id: r for r in load_requests(os.path.join(DS, "sample_deletion_requests.csv"))}
pool = list(base.values()) + list(load_requests(os.path.join(DS, "deletion_requests.csv")))

def run(req):
    others = [r for r in pool if r.case_id != req.case_id]
    views = audit.link([req] + others)
    return audit.audit(views[0])

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail)); print(("PASS " if cond else "FAIL ") + name, detail)

b = base["case_01"]; st0, s0 = run(b)
# 1. non-deletion request -> NOT_APPLICABLE, []
na = dataclasses.replace(b, case_id="custom_na", text="Please keep my account active. I only want to update my newsletter preferences.")
st, s = run(na); check("C1 preservation request -> NOT_APPLICABLE + []", st == "NOT_APPLICABLE" and s == [], st)
# 2. reported_status is a claim, never evidence
for rs in ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"):
    st, s = run(dataclasses.replace(b, case_id="custom_rs", reported_status=rs))
    check(f"C2 reported_status={rs} does not change the verdict", (st, s) == (st0, s0), st)
# 3. time travel: nothing captured after audit_at may be cited
man = {r["file_id"]: r["captured_at"] for r in csv.DictReader(open(os.path.join(DS, "evidence_files.csv")))}
early = dataclasses.replace(b, case_id="custom_tt", audit_at=b.requested_at.replace(hour=b.requested_at.hour) )
for k in (0, 1):
    cut = parse_ts("2026-08-25T00:00:00Z") if k == 0 else b.audit_at
    st, s = run(dataclasses.replace(b, case_id="custom_tt", audit_at=cut))
    bad = [x for x in s if x["evidence_id"].startswith("evidence_") and man[x["evidence_id"]] > cut.strftime("%Y-%m-%dT%H:%M:%SZ")]
    check(f"C3 cutoff {cut:%F}: no citation captured after audit_at", not bad, f"{st}, {len(s)} steps")
# 4. historical identifier resolves to the same resources
ual = list(csv.DictReader(open(os.path.join(DS, "user_audit_log.csv"))))
ident = list(csv.DictReader(open(os.path.join(DS, "user_identities.csv"))))
em = [r for r in ual if r["field_name"] == "email"]
done = 0
for r in em:
    cur = next((i for i in ident if i["user_id"] == r["user_id"]), None)
    if not cur: continue
    cmail = next((v for k, v in cur.items() if "email" in k and "hash" not in k), None)
    if not cmail or cmail != r["new_value"]: continue
    a = dataclasses.replace(b, case_id="custom_cur", email=cmail)
    h = dataclasses.replace(b, case_id="custom_hist", email=r["old_value"])
    sa, ta = run(a); sh, th = run(h)
    key = lambda t: sorted((x["system_id"], x["resource_id"], x["operation"]) for x in t)
    check(f"C4 historical e-mail of {r['user_id']} resolves like the current one", sa == sh and key(ta) == key(th) and ta != [], f"{sa} {len(ta)} steps")
    done += 1
    if done == 2: break
if not done: check("C4 historical e-mail", False, "no usable user_audit_log email change found")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
