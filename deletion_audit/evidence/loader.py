"""Evidence registry: manifest -> parsed observations for every supplied file."""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Tuple

from ..model import EvidenceFile, Obs
from .logsem import log_observations
from .structured import json_observations, json_snaps, sql_observations, sql_scope, txt_observations


def load_manifest(dataset_dir: str) -> Dict[str, EvidenceFile]:
    out: Dict[str, EvidenceFile] = {}
    with open(os.path.join(dataset_dir, "evidence_files.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ext = r["file_path"].rsplit(".", 1)[-1].lower()
            out[r["file_id"]] = EvidenceFile(r["file_id"], r["file_path"], r["origin"], r["captured_at"], ext)
    return out


def load_observations(dataset_dir: str, manifest: Dict[str, EvidenceFile], artifacts=None) -> List[Obs]:
    """Parse every evidence file.  ``artifacts`` is an optional callable handling pdf/png."""
    obs: List[Obs] = []
    for fid, ef in manifest.items():
        path = os.path.join(dataset_dir, ef.path)
        if not os.path.exists(path):
            continue
        if ef.kind == "log":
            obs += log_observations(path, fid, ef.captured_at)
        elif ef.kind == "sql":
            obs += sql_observations(path, fid, ef.captured_at)
            ef.scope = sql_scope(path, ef.captured_at)
        elif ef.kind == "json":
            obs += json_observations(path, fid, ef.captured_at)
            ef.snaps = json_snaps(path, ef.captured_at)
        elif ef.kind == "txt":
            obs += txt_observations(path, fid, ef.captured_at)
        elif ef.kind in ("pdf", "png") and artifacts is not None:
            obs += artifacts(path, fid, ef)
    return obs
