"""GhostGuard AI - Local Frontend Web Server.

Serves an interactive audit dashboard and provides local REST APIs to inspect cases,
view evidence citations, generate live OpenAI explanations, validate output, and run tests.
All data is 100% dynamically loaded from the engine, dataset, and output artifacts.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Set up project root
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(FRONTEND_DIR)
sys.path.insert(0, ROOT)

from deletion_audit.casemodel import load_requests
from deletion_audit.openai_client import AI_CLIENT
from deletion_audit.usage import METER

START_TIME = time.time()
_CACHED_REQ_MAP = None
_CACHED_OUTPUT_MTIME = 0
_CACHED_CASES = []


def get_requests_map():
    global _CACHED_REQ_MAP
    if _CACHED_REQ_MAP is not None:
        return _CACHED_REQ_MAP

    ds_path = os.path.join(ROOT, "dataset", "deletion_requests.csv")
    sample_path = os.path.join(ROOT, "dataset", "sample_deletion_requests.csv")
    req_map = {}
    if os.path.exists(ds_path):
        for r in load_requests(ds_path):
            req_map[r.case_id] = r
    if os.path.exists(sample_path):
        for r in load_requests(sample_path):
            if r.case_id not in req_map:
                req_map[r.case_id] = r
    _CACHED_REQ_MAP = req_map
    return req_map


def load_dataset_and_output():
    global _CACHED_OUTPUT_MTIME, _CACHED_CASES
    out_path = os.path.join(ROOT, "output.csv")
    if not os.path.exists(out_path):
        return []

    current_mtime = os.path.getmtime(out_path)
    if _CACHED_CASES and current_mtime == _CACHED_OUTPUT_MTIME:
        return _CACHED_CASES

    req_map = get_requests_map()
    cases = []
    with open(out_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cid = row["case_id"]
            req = req_map.get(cid)
            steps = []
            try:
                steps = json.loads(row.get("steps_json", "[]"))
            except Exception:
                pass
            cases.append({
                "case_id": cid,
                "deletion_status": row.get("deletion_status", "UNKNOWN"),
                "steps": steps,
                "step_count": len(steps),
                "email": req.email if req else "N/A",
                "requested_at": req.requested_at.isoformat() if req and hasattr(req.requested_at, "isoformat") else str(getattr(req, "requested_at", "N/A")),
                "reported_status": req.reported_status if req else "N/A",
                "audit_at": req.audit_at.isoformat() if req and hasattr(req.audit_at, "isoformat") else str(getattr(req, "audit_at", "N/A")),
                "request_text": req.text if req else ""
            })

    _CACHED_OUTPUT_MTIME = current_mtime
    _CACHED_CASES = cases
    return cases


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index_path = os.path.join(FRONTEND_DIR, "index.html")
            if os.path.exists(index_path):
                content = open(index_path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "index.html not found")
            return

        if path == "/api/health":
            self._send_json({
                "status": "online",
                "uptime_seconds": round(time.time() - START_TIME, 1),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "port": 8000,
                "openai_active": AI_CLIENT.is_available()
            })
            return

        if path == "/api/stats":
            cases = load_dataset_and_output()
            status_counts = {"COMPLETE": 0, "INCOMPLETE": 0, "NOT_APPLICABLE": 0}
            for c in cases:
                st = c["deletion_status"]
                status_counts[st] = status_counts.get(st, 0) + 1

            total = len(cases)
            percentages = {
                "INCOMPLETE": round(status_counts["INCOMPLETE"] / max(total, 1) * 100, 1),
                "COMPLETE": round(status_counts["COMPLETE"] / max(total, 1) * 100, 1),
                "NOT_APPLICABLE": round(status_counts["NOT_APPLICABLE"] / max(total, 1) * 100, 1),
            }

            usage_path = os.path.join(ROOT, "evaluation", "usage_run.json")
            usage_data = {}
            if os.path.exists(usage_path):
                try:
                    usage_data = json.load(open(usage_path, encoding="utf-8"))
                except Exception:
                    pass

            # Inject live meter totals into usage
            live_meter = METER.totals()
            usage_data["live_meter"] = live_meter
            usage_data["live_models"] = dict(METER.models)

            metrics_path = os.path.join(ROOT, "evaluation", "sample_metrics.json")
            metrics_data = {}
            if os.path.exists(metrics_path):
                try:
                    metrics_data = json.load(open(metrics_path, encoding="utf-8"))
                except Exception:
                    pass

            self._send_json({
                "total_cases": total,
                "status_counts": status_counts,
                "percentages": percentages,
                "usage": usage_data,
                "metrics": metrics_data,
                "openai_enabled": AI_CLIENT.is_available(),
                "openai_model": AI_CLIENT.model,
                "server_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            })
            return

        if path == "/api/cases":
            cases = load_dataset_and_output()
            st_filter = qs.get("status", [None])[0]
            search = qs.get("search", [""])[0].lower()

            if st_filter and st_filter != "ALL":
                cases = [c for c in cases if c["deletion_status"] == st_filter]

            if search:
                cases = [
                    c for c in cases
                    if search in c["case_id"].lower()
                    or search in c["email"].lower()
                    or any(search in s.get("resource_id", "").lower() or search in s.get("system_id", "").lower() for s in c["steps"])
                ]

            self._send_json({"count": len(cases), "cases": cases})
            return

        if path.startswith("/api/case/"):
            case_id = path.replace("/api/case/", "").strip()
            cases = load_dataset_and_output()
            target = next((c for c in cases if c["case_id"] == case_id), None)
            if target:
                self._send_json(target)
            else:
                self._send_json({"error": f"Case '{case_id}' not found"}, status=404)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/explain/"):
            case_id = path.replace("/api/explain/", "").strip()
            cases = load_dataset_and_output()
            target = next((c for c in cases if c["case_id"] == case_id), None)
            if not target:
                self._send_json({"error": "Case not found"}, status=404)
                return

            narrative = AI_CLIENT.explain_investigation_and_remediation(
                target["case_id"],
                target["deletion_status"],
                target["steps"],
                target["request_text"]
            )
            self._send_json(narrative)
            return

        if path == "/api/action/audit":
            p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--evaluate"], capture_output=True, text=True, cwd=ROOT)
            global _CACHED_OUTPUT_MTIME
            _CACHED_OUTPUT_MTIME = 0  # invalidate cache so fresh output is read
            self._send_json({"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
            return

        if path == "/api/action/validate":
            p = subprocess.run([sys.executable, os.path.join(ROOT, "validate.py")], capture_output=True, text=True, cwd=ROOT)
            self._send_json({"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
            return

        if path == "/api/action/test":
            p = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "discover", "-s", "tests", "-v"], capture_output=True, text=True, cwd=ROOT)
            self._send_json({"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
            return

        self.send_error(404, "Not Found")


def run(port=8000):
    server_address = ("", port)
    httpd = HTTPServer(server_address, DashboardHandler)
    print(f"GhostGuard AI Web Dashboard running at http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    run(port)
