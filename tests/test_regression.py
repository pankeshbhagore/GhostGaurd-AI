"""Regression tests: python -m unittest discover -s code/tests -v   (run from the repository root)"""
import csv, json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
ROOT = CODE if os.path.isdir(os.path.join(CODE, "dataset")) else os.path.dirname(CODE)
sys.path.insert(0, CODE)
import main as entry
from evaluate import evaluate, load
import validate

DS = os.path.join(ROOT, "dataset")

class Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out1 = os.path.join(cls.tmp, "o1.csv"); cls.out2 = os.path.join(cls.tmp, "o2.csv")
        for p in (cls.out1, cls.out2):
            _, reqs, out = entry.run(DS, "deletion_requests.csv"); entry.write_output(p, reqs, out)

    def test_deterministic(self):
        self.assertEqual(open(self.out1, "rb").read(), open(self.out2, "rb").read())

    def test_valid_format(self):
        rep, sem, _ = validate.validate(self.out1, DS)
        self.assertEqual((rep.err, sem.err), ([], []))

    def test_row_count(self):
        self.assertEqual(len(list(csv.DictReader(open(self.out1, encoding="utf-8")))), 518)

    def test_samples(self):
        _, _, out = entry.run(DS, "sample_deletion_requests.csv", extra=("deletion_requests.csv",))
        m = evaluate(out, load(os.path.join(DS, "sample_output.csv")))
        self.assertEqual(m["status_acc"], 1.0)
        self.assertGreaterEqual(m["step_f1"], 0.98)
        self.assertEqual(m["step_precision"], 1.0)

    def test_not_applicable_empty(self):
        for r in csv.DictReader(open(self.out1, encoding="utf-8")):
            if r["deletion_status"] == "NOT_APPLICABLE":
                self.assertEqual(json.loads(r["steps_json"]), [])

if __name__ == "__main__":
    unittest.main()
