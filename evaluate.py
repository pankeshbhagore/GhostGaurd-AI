#!/usr/bin/env python3
"""Reproducible evaluator.  Gold files are used ONLY here (never by the predictor).

    python code/evaluate.py PRED.csv GOLD.csv [--json out.json]

Metrics
  status accuracy; per-status precision / recall / F1 with micro and macro averages
  sequence accuracy      : whole steps_json equal, array order significant (step_no ignored: it follows from order)
  step-object P/R/F1     : multiset match on the full (phase, system, resource, operation, state, evidence) tuple,
                           duplicates counted once per occurrence; micro (pooled) and macro (mean over cases)
  resource accuracy      : recall / precision of the set of resource ids touched
  operation / state / evidence accuracy: among steps whose (system, resource, phase) matches a gold step
  field accuracy         : per field among steps matched on (system, resource, operation)
  phase-order accuracy   : share of cases whose phases are non-decreasing FINDING<REMEDIATION<VERIFICATION
  schema validity        : share of cases whose steps have exactly the seven contract fields with allowed values
"""
import csv, json, sys
from collections import Counter

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
ALL = FIELDS + ["step_no"]
STATUSES = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]
PH = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
KEYS = set(ALL)


def load(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def sk(s):
    return tuple(s[f] for f in FIELDS)


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def evaluate(pred, gold):
    cases = sorted(gold)
    n = len(cases)
    out = {"cases": n}
    # ---- status
    out["status_acc"] = sum(pred.get(c, ("", []))[0] == gold[c][0] for c in cases) / n
    per = {}
    for s in STATUSES:
        tp = sum(pred.get(c, ("", []))[0] == s and gold[c][0] == s for c in cases)
        fp = sum(pred.get(c, ("", []))[0] == s and gold[c][0] != s for c in cases)
        fn = sum(pred.get(c, ("", []))[0] != s and gold[c][0] == s for c in cases)
        p, r, f = _prf(tp, fp, fn)
        per[s] = {"precision": p, "recall": r, "f1": f, "support": tp + fn}
    out["status_by_class"] = per
    present = [s for s in STATUSES if per[s]["support"]]
    out["status_macro_f1"] = sum(per[s]["f1"] for s in present) / len(present) if present else 0.0
    out["status_micro_f1"] = out["status_acc"]            # single-label: micro-F1 equals accuracy
    # ---- sequence
    out["seq_acc"] = sum([sk(s) for s in pred.get(c, ("", []))[1]] == [sk(s) for s in gold[c][1]] for c in cases) / n
    # ---- step objects
    tp = fp = fn = 0; case_f1 = []
    res_tp = res_fp = res_fn = 0
    fld = Counter(); fld_n = 0
    opc = stc = evc = trip_n = 0
    for c in cases:
        p = pred.get(c, ("", []))[1]; g = gold[c][1]
        gs = Counter(sk(s) for s in g); ps = Counter(sk(s) for s in p)
        i = sum((gs & ps).values()); a, b = sum(ps.values()) - i, sum(gs.values()) - i
        tp += i; fp += a; fn += b
        case_f1.append(_prf(i, a, b)[2] if (i + a + b) else 1.0)
        gr = {s["resource_id"] for s in g}; pr = {s["resource_id"] for s in p}
        res_tp += len(gr & pr); res_fp += len(pr - gr); res_fn += len(gr - pr)
        gm = {(s["system_id"], s["resource_id"], s["operation"]): s for s in g}
        for s in p:
            m = gm.get((s["system_id"], s["resource_id"], s["operation"]))
            if m:
                fld_n += 1
                for f in ALL:
                    fld[f] += s.get(f) == m[f]
        gp = {(s["system_id"], s["resource_id"], s["phase"]): s for s in g}
        for s in p:
            m = gp.get((s["system_id"], s["resource_id"], s["phase"]))
            if m:
                trip_n += 1; opc += s["operation"] == m["operation"]; stc += s["state"] == m["state"]; evc += s["evidence_id"] == m["evidence_id"]
    P, Rr, F = _prf(tp, fp, fn)
    out.update(step_precision=P, step_recall=Rr, step_f1=F, step_f1_macro=sum(case_f1) / n)
    rp, rr, rf = _prf(res_tp, res_fp, res_fn)
    out.update(resource_precision=rp, resource_recall=rr, resource_f1=rf)
    out["operation_acc"] = opc / trip_n if trip_n else 0.0
    out["state_acc"] = stc / trip_n if trip_n else 0.0
    out["evidence_acc"] = evc / trip_n if trip_n else 0.0
    out["field_acc"] = {f: fld[f] / fld_n for f in ALL} if fld_n else {}
    # ---- ordering / schema
    def ordered(steps):
        ph = [PH.get(s.get("phase"), 9) for s in steps]
        return ph == sorted(ph)
    def schema(steps):
        return all(isinstance(s, dict) and set(s) == KEYS for s in steps) and [s["step_no"] for s in steps] == list(range(1, len(steps) + 1))
    out["phase_order_acc"] = sum(ordered(pred.get(c, ("", []))[1]) for c in cases) / n
    out["schema_valid"] = sum(schema(pred.get(c, ("", []))[1]) for c in cases) / n
    return out


def to_markdown(m, title="Metrics"):
    L = [f"### {title}", "", f"Cases: {m['cases']}", "", "| Metric | Value |", "|---|---|"]
    for k in ("status_acc", "status_macro_f1", "seq_acc", "step_precision", "step_recall", "step_f1", "step_f1_macro",
              "resource_precision", "resource_recall", "resource_f1", "operation_acc", "state_acc", "evidence_acc",
              "phase_order_acc", "schema_valid"):
        L.append(f"| {k} | {m[k]:.4f} |")
    L += ["", "| Status | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"]
    for s, d in m["status_by_class"].items():
        L.append(f"| {s} | {d['precision']:.3f} | {d['recall']:.3f} | {d['f1']:.3f} | {d['support']} |")
    L += ["", "| Field (matched steps) | Accuracy |", "|---|---|"] + [f"| {f} | {v:.4f} |" for f, v in m["field_acc"].items()]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    res = evaluate(load(a[0]), load(a[1]))
    print(json.dumps(res, indent=2))
    if "--json" in sys.argv:
        open(sys.argv[sys.argv.index("--json") + 1], "w").write(json.dumps(res, indent=2))
