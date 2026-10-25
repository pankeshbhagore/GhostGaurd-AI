#!/usr/bin/env python3
"""One command that reproduces every artifact and records the real logs/metrics.

    python run_all.py

Steps: unit/integration tests -> official sample evaluation -> full run (twice) -> byte comparison ->
independent validation -> diagnostics.  Everything is written to evaluation/logs/.
"""
import hashlib, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(HERE, "dataset")):
    ROOT = HERE
    CODE_DIR = ""
else:
    ROOT = os.path.dirname(HERE)
    CODE_DIR = "code"

LOGS = os.path.join(HERE, "evaluation", "logs")
os.makedirs(LOGS, exist_ok=True)
PY = sys.executable


def target_path(*parts):
    if CODE_DIR:
        return os.path.join(CODE_DIR, *parts).replace("\\", "/")
    return os.path.join(*parts).replace("\\", "/")


def sh(name, cmd, **kw):
    t = time.time()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)
    out = f"$ {' '.join(cmd)}\n[exit {p.returncode}, {time.time()-t:.1f}s]\n{p.stdout}{p.stderr}"
    with open(os.path.join(LOGS, name + ".log"), "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"[{'ok' if p.returncode == 0 else 'FAIL'}] {name}")
    return p


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    fail = 0
    env_nocache = dict(os.environ, GHOSTGUARD_NO_CACHE="1")
    tests_dir = target_path("tests")
    custom_cases = target_path("evaluation", "custom_cases.py")
    main_py = target_path("main.py")
    validate_py = target_path("validate.py")
    diagnostics_py = target_path("diagnostics.py")
    linkage_py = target_path("evaluation", "linkage_holdout.py")
    output_csv = "output.csv"

    fail += sh("1_tests", [PY, "-W", "ignore", "-m", "unittest", "discover", "-s", tests_dir, "-v"]).returncode != 0
    fail += sh("2_custom_cases", [PY, custom_cases]).returncode != 0
    r = sh("3_sample_eval_and_full_run", [PY, main_py, "--evaluate", "--output", output_csv], env=env_nocache)   # cold: no OCR cache
    fail += r.returncode != 0
    a = sha(os.path.join(ROOT, output_csv))
    fail += sh("4_second_run", [PY, main_py, "--no-usage", "--output", os.path.join(LOGS, "output_run2.csv")]).returncode != 0
    b = sha(os.path.join(LOGS, "output_run2.csv"))
    if os.path.exists(os.path.join(LOGS, "output_run2.csv")):
        os.remove(os.path.join(LOGS, "output_run2.csv"))
    rep = {"run1_sha256": a, "run2_sha256": b, "reproducible": a == b}
    json.dump(rep, open(os.path.join(LOGS, "5_reproducibility.json"), "w"), indent=2)
    print("[ok] reproducibility" if a == b else "[FAIL] reproducibility", rep)
    fail += a != b
    fail += sh("6_validate", [PY, validate_py]).returncode != 0
    fail += sh("7_diagnostics", [PY, diagnostics_py]).returncode != 0
    fail += sh("8_linkage_holdout", [PY, linkage_py]).returncode != 0
    print("ALL OK" if not fail else f"{fail} step(s) failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
