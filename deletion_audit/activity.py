"""Global operational activity stream (``system_activity.csv``)."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from .model import parse_ts, prefix_of


@dataclass
class Activity:
    activity_id: str
    t: datetime
    event_type: str
    source: str
    target: str
    resource_id: str
    result: str
    details: str

    @property
    def kind(self) -> str:
        return prefix_of(self.resource_id) or ""


class ActivityStream:
    def __init__(self, path: str):
        self.events: List[Activity] = []
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.events.append(Activity(r["activity_id"], parse_ts(r["event_time"]), r["event_type"],
                                            r["source_system"], r["target_system"], r["resource_id"],
                                            r["reported_result"], r["details"]))
        self.events.sort(key=lambda e: (e.t, e.activity_id))
        self.by_resource: Dict[str, List[Activity]] = defaultdict(list)
        for e in self.events:
            self.by_resource[e.resource_id].append(e)
        self.worker = [e for e in self.events if e.source == "deletion_worker"]
