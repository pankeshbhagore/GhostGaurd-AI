"""Policy engine: ``deletion_rules.csv`` -> required end state per data category.

The rules file is read at start-up; the mapping below only translates its *required_action*
vocabulary into the normalised end states used by the planner, so a changed rules file changes
the behaviour (nothing is hard-coded per category).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict

from .model import (ABSENT, ANONYMIZED, BLOCKED, DISABLED, PRESENT, REDACTED)

ACTION_END_STATE = {"DELETE": ABSENT, "REDACT": REDACTED, "ANONYMIZE": ANONYMIZED, "RETAIN": PRESENT}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    condition: str
    action: str
    end_state: str


class Policy:
    def __init__(self, rules_csv: str):
        self.rules: Dict[str, Rule] = {}
        with open(rules_csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                act = r["required_action"].strip().upper()
                self.rules[r["data_category"]] = Rule(r["rule_id"], r["data_category"], r["condition"], act,
                                                      ACTION_END_STATE.get(act, PRESENT))
        # BACKUP_ARCHIVE is "RETAIN while restore access can be blocked": the archive stays, the
        # restore path is what must be closed.  DATA_FLOW has no row: sync/import paths must be off.
        self.rules.setdefault("DATA_FLOW", Rule("derived", "DATA_FLOW", "No active sync/import may recreate data",
                                                 "DISABLE", DISABLED))
        b = self.rules.get("BACKUP_ARCHIVE")
        if b:
            self.rules["BACKUP_ARCHIVE"] = Rule(b.rule_id, b.category, b.condition, "BLOCK_RESTORE", BLOCKED)

    def required(self, category: str) -> Rule:
        return self.rules[category]
