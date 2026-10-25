#!/usr/bin/env python3
"""Held-out check of the key-less account linker (no gold labels needed).

For every request whose account is linked by *identity keys* (ground truth we can verify), hide that link and ask the
timing-based linker to recover it, with ALL requests competing for the same worker events.  Reports accuracy by the
number of eligible candidates, i.e. how trustworthy an inferred (unverifiable) link is.

    python code/evaluation/linkage_holdout.py
"""
import collections, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
ROOT = CODE if os.path.isdir(os.path.join(CODE, "dataset")) else os.path.dirname(CODE)
sys.path.insert(0, CODE)
import main as entry
from deletion_audit.actlink import ACCOUNT_BAND, RESULT_FOR_STATUS, _m, link_accounts
from deletion_audit.casemodel import load_requests
from deletion_audit.timeutil import T


def main():
    ds = os.path.join(ROOT, "dataset")
    a = entry.Audit(ds)
    reqs = load_requests(os.path.join(ds, "sample_deletion_requests.csv")) + load_requests(os.path.join(ds, "deletion_requests.csv"))
    views = a.link(reqs)
    truth = {}
    for i, v in enumerate(views):
        accs = [r for (s, r), lr in v.resources.items() if s == "account_service" and lr.link.startswith("content")]
        if len(accs) == 1 and not v.na:
            truth[i] = accs[0]
    needs = sorted(truth)
    def explicit(i, rid):
        q = views[i].req
        return any(q.requested_at <= T(o.t) <= q.audit_at for o in a.state.by_res.get(("account_service", rid), ()))
    files_of = {i: {o.file_id for lr in v.resources.values() for o in lr.obs} for i, v in enumerate(views)}
    def affinity(i, rid):
        q = views[i].req
        return any(q.requested_at <= T(o.t) <= q.audit_at and o.file_id in files_of[i] for o in a.state.by_res.get(("account_service", rid), ()))
    compliant = {i for i in needs if not a._violations(views[i])}
    got = link_accounts(views, needs, a.stream, set(), compliant, explicit, affinity)
    events = [e for e in a.stream.worker if e.event_type == "DELETE_RECORD" and e.target == "account_service"]
    tally = collections.defaultdict(lambda: [0, 0])
    for i in needs:
        q = views[i].req
        k = sum(1 for e in events if ACCOUNT_BAND[0] <= _m(e, q.reported_at) <= ACCOUNT_BAND[1]
                and e.result == RESULT_FOR_STATUS[q.reported_status] and e.t <= q.audit_at)
        bucket = "1" if k <= 1 else ("2-3" if k <= 3 else "4+")
        tally[bucket][1] += 1
        tally[bucket][0] += bool(got.get(i) and got[i].resource_id == truth[i])
    print("candidates | correct / total | accuracy")
    tot = [0, 0]
    for b in ("1", "2-3", "4+"):
        c, n = tally[b]; tot[0] += c; tot[1] += n
        print(f"{b:>10} | {c:>4} / {n:<4}     | {c / n:.3f}" if n else f"{b:>10} | -")
    print(f"{'all':>10} | {tot[0]:>4} / {tot[1]:<4}     | {tot[0] / tot[1]:.3f}")

if __name__ == "__main__":
    main()
