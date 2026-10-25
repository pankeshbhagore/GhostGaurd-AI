"""Synthetic scenarios + metamorphic properties (kept apart from the official samples).
Run: python -m unittest discover -s code/tests -v"""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
ROOT = PARENT if os.path.isdir(os.path.join(PARENT, "dataset")) else os.path.dirname(PARENT)
sys.path.insert(0, PARENT)
sys.path.insert(0, HERE)
from synth import Builder
import main as entry
import validate

R = Builder.rec
REQ, AUD = "2026-02-20T00:00:00Z", "2026-03-02T00:00:00Z"
ACC = ("account_service", "account_9001", "USER_PROFILE")


def run(b):
    ds = b.build()
    _, _, out = entry.run(ds, "deletion_requests.csv", extra=())
    return ds, out


def ops(steps):
    return [(s["operation"], s["resource_id"]) for s in steps]


def base(email="ana@example.org"):
    b = Builder(); b.user("user_s1", "Ana Test", "ana@example.org"); return b


def acc(t, result, subj="ana@example.org", seq=1):
    return R(t, ACC[0], ACC[1], ACC[2], result, subj, seq)


class Scenarios(unittest.TestCase):
    def check_valid(self, ds, b):
        out = os.path.join(ds, "out.csv"); _, reqs, o = entry.run(ds, "deletion_requests.csv", extra=())
        entry.write_output(out, reqs, o)
        rep, sem, _ = validate.validate(out, ds, ocr=False)
        # synthetic data has only 1-3 cases, so the 518-row rule is not applicable
        errs = [e for e in rep.err if "518" not in e] + sem.err
        self.assertEqual(errs, [])

    def test_01_historical_email(self):
        b = base(); b.rename_email("user_s1", "2026-02-01T00:00:00Z", "ana.old@example.org", "ana@example.org")
        b.ledger("2026-03-01T12:00:00Z", [acc("2026-03-01T10:00:00Z", "FOUND", "ana.old@example.org")])
        b.request("c_old", "ana.old@example.org", REQ, AUD); b.request("c_new", "ana@example.org", REQ, AUD)
        ds, out = run(b)
        self.assertEqual(out["c_old"], out["c_new"])
        self.assertEqual(out["c_old"][0], "INCOMPLETE")
        self.assertIn(("REPORT_REMAINING_RECORD", "account_9001"), ops(out["c_old"][1]))
        self.check_valid(ds, b)

    def test_02_recreated_by_sync(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [
            acc("2026-03-01T10:00:00Z", "NOT_FOUND", seq=1),
            R("2026-03-01T11:00:00Z", "integration_service", "data_flow_9003", "DATA_FLOW", "ACTIVE", "ana@example.org", 2),
            acc("2026-03-01T12:00:00Z", "FOUND", seq=3)])
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]
        self.assertEqual(st, "INCOMPLETE")
        o = ops(steps)
        self.assertIn(("REPORT_ACTIVE_DATA_FLOW", "data_flow_9003"), o)
        self.assertIn(("REPORT_REMAINING_RECORD", "account_9001"), o)
        self.assertLess(o.index(("DISABLE_DATA_FLOW", "data_flow_9003")), o.index(("DELETE_RECORD", "account_9001")))
        self.assertIn(("VERIFY_DATA_FLOW_DISABLED", "data_flow_9003"), o)
        self.check_valid(ds, b)

    def test_03_required_redaction(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND"),
            R("2026-03-01T10:00:00Z", "support_platform", "support_9004", "SUPPORT_MESSAGE", "IDENTIFIABLE", "ana@example.org", 2)])
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]
        self.assertEqual(st, "INCOMPLETE")
        o = ops(steps)
        self.assertIn(("REPORT_EXPOSED_PII", "support_9004"), o); self.assertIn(("REDACT_RECORD", "support_9004"), o)
        self.assertNotIn(("DELETE_RECORD", "support_9004"), o)           # policy says redact, never delete
        self.assertIn(("VERIFY_RECORD_REDACTED", "support_9004"), o)
        self.check_valid(ds, b)

    def test_04_backup_restore_risk(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND"),
            R("2026-03-01T10:00:00Z", "backup_service", "backup_9005", "BACKUP_ARCHIVE", "ACTIVE", "ana@example.org", 2)])
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]; o = ops(steps)
        self.assertEqual(st, "INCOMPLETE")
        self.assertIn(("REPORT_RESTORE_RISK", "backup_9005"), o); self.assertIn(("BLOCK_RESTORE", "backup_9005"), o)
        self.assertNotIn(("DELETE_RECORD", "backup_9005"), o)            # backups are blocked, not deleted
        self.assertIn(("VERIFY_RESTORE_BLOCKED", "backup_9005"), o)
        self.check_valid(ds, b)

    def test_05_legal_hold_retained(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND"),
            R("2026-03-01T10:00:00Z", "legal_service", "legal_hold_9006", "LEGAL_HOLD_RECORD", "FOUND", "ana@example.org", 2)])
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]; o = ops(steps)
        self.assertEqual(st, "COMPLETE")
        self.assertIn(("VERIFY_RECORD_PRESENT", "legal_hold_9006"), o)
        self.assertTrue(all(s["phase"] == "VERIFICATION" for s in steps))
        self.check_valid(ds, b)

    def test_06_stale_evidence_after_audit(self):
        b = base()
        e1 = b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND")])
        b.ledger("2026-04-01T13:00:00Z", [acc("2026-04-01T10:00:00Z", "FOUND")])        # after audit_at
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]
        self.assertEqual(st, "COMPLETE")
        self.assertEqual([s["evidence_id"] for s in steps], [e1])
        self.check_valid(ds, b)

    def test_07_conflicting_copies_worst_wins(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND")])
        b.ledger("2026-03-01T13:30:00Z", [acc("2026-03-01T10:00:00Z", "FOUND")])        # replica still has it
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b)
        self.assertEqual(out["c"][0], "INCOMPLETE")
        self.assertIn(("REPORT_REMAINING_RECORD", "account_9001"), ops(out["c"][1]))
        self.check_valid(ds, b)

    def test_08_already_anonymized_analytics(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "NOT_FOUND"),
            R("2026-03-01T10:00:00Z", "analytics_platform", "analytics_9007", "ANALYTICS_PROFILE", "ANONYMIZED", "ana@example.org", 2)])
        b.request("c", "ana@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]
        self.assertEqual(st, "COMPLETE")
        self.assertIn(("VERIFY_RECORD_ANONYMIZED", "analytics_9007"), ops(steps))
        self.assertFalse([s for s in steps if s["phase"] != "VERIFICATION"])
        self.check_valid(ds, b)

    def test_09_ambiguous_identity_same_name(self):
        b = Builder(); b.user("user_s1", "Sam Lee", "sam.a@example.org"); b.user("user_s2", "Sam Lee", "sam.b@example.net")
        b.ledger("2026-03-01T13:00:00Z", [
            R("2026-03-01T10:00:00Z", "account_service", "account_9101", "USER_PROFILE", "FOUND", "sam.b@example.net", 1),
            R("2026-03-01T10:00:00Z", "account_service", "account_9100", "USER_PROFILE", "NOT_FOUND", "sam.a@example.org", 2)])
        b.request("c", "sam.a@example.org", REQ, AUD)
        ds, out = run(b); st, steps = out["c"]
        self.assertNotIn("account_9101", [s["resource_id"] for s in steps])             # never attach by shared name
        self.assertEqual(st, "COMPLETE")
        self.check_valid(ds, b)

    def test_10_not_a_deletion_request(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "FOUND")])
        b.request("c", "ana@example.org", REQ, AUD, text="Please send me a copy of the personal data you hold about me.")
        ds, out = run(b)
        self.assertEqual(out["c"], ("NOT_APPLICABLE", []))
        self.check_valid(ds, b)


class Metamorphic(unittest.TestCase):
    def _base(self):
        b = base()
        b.ledger("2026-03-01T13:00:00Z", [acc("2026-03-01T10:00:00Z", "FOUND"),
            R("2026-03-01T10:00:00Z", "search_service", "search_9002", "SEARCH_INDEX_ENTRY", "FOUND", "ana@example.org", 2)])
        b.request("c", "ana@example.org", REQ, AUD)
        return b

    def test_future_observation_does_not_change_history(self):
        a = run(self._base())[1]["c"]
        b = self._base()
        b.ledger("2026-05-01T13:00:00Z", [acc("2026-05-01T10:00:00Z", "NOT_FOUND"),
            R("2026-05-01T10:00:00Z", "search_service", "search_9002", "SEARCH_INDEX_ENTRY", "NOT_FOUND", "ana@example.org", 2)])
        self.assertEqual(run(b)[1]["c"], a)

    def test_reported_status_is_not_evidence(self):
        res = set()
        for rs in ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED"):
            b = self._base(); b.requests[0] = b.requests[0][:3] + (rs,) + b.requests[0][4:]
            res.add(repr(run(b)[1]["c"]))
        self.assertEqual(len(res), 1)

    def test_unrelated_user_records_are_ignored(self):
        a = run(self._base())[1]["c"]
        b = self._base(); b.user("user_s9", "Zed Other", "zed@example.org")
        b.ledger("2026-03-01T13:00:00Z", [R("2026-03-01T10:00:00Z", "account_service", "account_9999", "USER_PROFILE", "FOUND", "zed@example.org")])
        self.assertEqual(run(b)[1]["c"], a)

    def test_evidence_file_order_is_irrelevant(self):
        b = self._base(); b.ledger("2026-03-01T14:00:00Z", [R("2026-03-01T10:30:00Z", "support_platform", "support_9004", "SUPPORT_MESSAGE", "IDENTIFIABLE", "ana@example.org")])
        a = run(b)[1]["c"]
        b.files.reverse()
        self.assertEqual([ (s["phase"], s["operation"], s["resource_id"]) for s in run(b)[1]["c"][1]],
                         [(s["phase"], s["operation"], s["resource_id"]) for s in a[1]])

    def test_deterministic(self):
        self.assertEqual(run(self._base())[1], run(self._base())[1])


class ValidatorMutations(unittest.TestCase):
    """The independent validator must reject corrupted outputs."""
    @classmethod
    def setUpClass(cls):
        import csv, json
        cls.csv, cls.json = csv, json
        cls.ds = os.path.join(ROOT, "dataset")
        with open(os.path.join(ROOT, "output.csv"), newline="", encoding="utf-8") as fh:
            cls.rows = list(csv.DictReader(fh))

    def write(self, rows, name):
        import tempfile, csv
        p = os.path.join(tempfile.mkdtemp(), name)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, ["case_id", "deletion_status", "steps_json"], lineterminator="\n"); w.writeheader(); w.writerows(rows)
        return p

    def errs(self, rows):
        r, s, _ = validate.validate(self.write(rows, "o.csv"), self.ds, ocr=False)
        return r.err + s.err

    def pick(self, status, n=1):
        i = next(i for i, r in enumerate(self.rows) if r["deletion_status"] == status and len(self.json.loads(r["steps_json"])) >= 2)
        return i

    def mut(self, i, fn):
        rows = [dict(r) for r in self.rows]
        st = self.json.loads(rows[i]["steps_json"]); res = fn(st, rows[i])
        rows[i]["steps_json"] = self.json.dumps(st if res is None else res); return rows

    def test_baseline_clean(self):
        self.assertEqual(self.errs(self.rows), [])

    def test_missing_row(self):       self.assertTrue(self.errs(self.rows[1:]))
    def test_duplicate_row(self):     self.assertTrue(self.errs(self.rows + [self.rows[0]]))
    def test_unsorted(self):          self.assertTrue(self.errs(self.rows[::-1]))
    def test_bad_json(self):
        rows = [dict(r) for r in self.rows]; rows[0]["steps_json"] = "{oops"; self.assertTrue(self.errs(rows))
    def test_non_array(self):
        rows = [dict(r) for r in self.rows]; rows[0]["steps_json"] = "{}"; self.assertTrue(self.errs(rows))
    def test_extra_field(self):
        i = self.pick("INCOMPLETE"); self.assertTrue(self.errs(self.mut(i, lambda st, r: st[0].update(extra=1))))
    def test_step_numbers(self):
        i = self.pick("INCOMPLETE"); self.assertTrue(self.errs(self.mut(i, lambda st, r: st[1].update(step_no=9))))
    def test_bad_state_pair(self):
        i = self.pick("INCOMPLETE"); self.assertTrue(self.errs(self.mut(i, lambda st, r: st[0].update(state="ACTIVE"))))
    def test_wrong_order(self):
        i = self.pick("INCOMPLETE")
        def f(st, r):
            st.reverse()
            for n, s in enumerate(st, 1): s["step_no"] = n
        self.assertTrue(self.errs(self.mut(i, f)))
    def test_nonempty_evidence_on_remediation(self):
        i = self.pick("INCOMPLETE")
        def f(st, r):
            next(s for s in st if s["phase"] == "REMEDIATION").update(evidence_id="evidence_01")
        self.assertTrue(self.errs(self.mut(i, f)))
    def test_unknown_citation(self):
        i = self.pick("COMPLETE"); self.assertTrue(self.errs(self.mut(i, lambda st, r: st[0].update(evidence_id="evidence_999"))))
    def test_future_citation(self):
        i = self.pick("COMPLETE")
        with open(os.path.join(self.ds, "evidence_files.csv"), newline="", encoding="utf-8") as fh:
            late = max(self.csv.DictReader(fh), key=lambda r: r["captured_at"])["file_id"]
        self.assertTrue(self.errs(self.mut(i, lambda st, r: st[0].update(evidence_id=late))))
    def test_complete_with_finding(self):
        i = self.pick("COMPLETE")
        def f(st, r):
            st.insert(0, dict(st[0], phase="FINDING", operation="REPORT_REMAINING_RECORD", state="PRESENT"))
            for n, s in enumerate(st, 1): s["step_no"] = n
        self.assertTrue(self.errs(self.mut(i, f)))
    def test_na_with_steps(self):
        i = next(i for i, r in enumerate(self.rows) if r["deletion_status"] == "NOT_APPLICABLE")
        rows = [dict(r) for r in self.rows]; rows[i]["steps_json"] = self.rows[self.pick("COMPLETE")]["steps_json"]; self.assertTrue(self.errs(rows))
    def test_missing_verification(self):
        i = self.pick("INCOMPLETE")
        def f(st, r):
            st[:] = [s for s in st if s["phase"] != "VERIFICATION"]
        self.assertTrue(self.errs(self.mut(i, f)))
    def test_wrong_policy_treatment(self):
        i = next(i for i, r in enumerate(self.rows) if r["deletion_status"] == "INCOMPLETE" and "search_" in r["steps_json"])
        def f(st, r):
            for s in st:
                if s["resource_id"].startswith("search_") and s["phase"] == "VERIFICATION":
                    s.update(operation="VERIFY_RECORD_PRESENT", state="PRESENT")
        self.assertTrue(self.errs(self.mut(i, f)))


if __name__ == "__main__":
    unittest.main()


class PlannerGuards(unittest.TestCase):
    def _res(self, cat, state, cite="evidence_01"):
        from deletion_audit.state import Resolved
        return Resolved("sys", "x_1", cat, state, "2026-01-01T00:00:00Z", cite, "obs", [])

    def test_missing_retained_record_is_not_verified_as_present(self):
        from deletion_audit.planner import assess, build_steps
        from deletion_audit.policy import Policy
        pol = Policy(os.path.join(ROOT, "dataset", "deletion_rules.csv"))
        st, steps = build_steps([assess(self._res("SECURITY_AUDIT_LOG", "ABSENT"), pol)])
        self.assertEqual(steps, [])
        st, steps = build_steps([assess(self._res("SECURITY_AUDIT_LOG", "PRESENT"), pol)])
        self.assertEqual([s["operation"] for s in steps], ["VERIFY_RECORD_PRESENT"])

    def test_removed_backup_is_not_a_restore_risk(self):
        from deletion_audit.planner import assess
        from deletion_audit.policy import Policy
        pol = Policy(os.path.join(ROOT, "dataset", "deletion_rules.csv"))
        self.assertTrue(assess(self._res("BACKUP_ARCHIVE", "ABSENT"), pol).compliant)
        self.assertFalse(assess(self._res("BACKUP_ARCHIVE", "RESTORABLE"), pol).compliant)
