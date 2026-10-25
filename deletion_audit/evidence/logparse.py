"""Dialect-agnostic log line parser.

The evidence logs come in eight different dialects (logfmt, bracketed, pipe
separated, syslog-RFC5424-like, JSON lines, cloud-runtime wrapper with an escaped
``message="..."``, and ledger-style ``record_lookup`` lines).  They all reduce to
``timestamp + (level, service, trace id, event) + key/value pairs``; this module
extracts that common shape so the semantic layer can be written once.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|[^\s"]+)')
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*(.*)$")
_BRACKET = re.compile(r"^\[(?P<level>[A-Z]+)\]\s+\[(?P<service>[^\]]+)\]\s+\[(?P<trace>[^\]]+)\]\s+(?P<event>\w+):\s*(?P<rest>.*)$")
_SYSLOG = re.compile(r"^<\d+>1\s+(?P<node>\S+)\s+(?P<service>\S+)\s+(?P<trace>\S+)\s+-\s+-\s+(?P<rest>.*)$")


@dataclass
class LogRec:
    ts: str
    level: str = ""
    service: str = ""
    trace: str = ""
    event: str = ""
    kv: Dict[str, str] = field(default_factory=dict)
    raw: str = ""
    lineno: int = 0


def _unq(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v.replace('\\"', '"').replace("\\\\", "\\")


def parse_kv(text: str) -> Dict[str, str]:
    return {m.group(1): _unq(m.group(2)) for m in _KV.finditer(text)}


def parse_line(line: str, lineno: int = 0) -> Optional[LogRec]:
    line = line.rstrip("\n")
    m = _TS.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2).strip()
    rec = LogRec(ts=ts, raw=line, lineno=lineno)

    if rest.startswith("{"):                                   # JSON lines
        try:
            d = json.loads(rest)
        except Exception:
            return rec
        rec.level = str(d.get("severity", d.get("level", "")))
        rec.service = str(d.get("service", d.get("logger", "")))
        rec.trace = str(d.get("trace_id", d.get("requestId", "")))
        rec.event = str(d.get("event", ""))
        rec.kv = {k: ("null" if v is None else str(v)) for k, v in d.items()}
        return rec

    mb = _BRACKET.match(rest)                                   # [LEVEL] [service] [trace] event: k=v
    if mb:
        rec.level, rec.service, rec.trace, rec.event = (mb.group("level"), mb.group("service"),
                                                         mb.group("trace"), mb.group("event"))
        rec.kv = parse_kv(mb.group("rest"))
        return rec

    if rest.startswith("|"):                                    # | LEVEL | service | trace | event | k=v
        parts = [p.strip() for p in rest.lstrip("|").split(" | ", 4)]
        if len(parts) >= 5 and parts[0] in ("DEBUG", "INFO", "WARN", "ERROR"):
            rec.level, rec.service, rec.trace, rec.event = parts[0], parts[1], parts[2], parts[3]
            rec.kv = parse_kv(parts[4])
            return rec

    ms = _SYSLOG.match(rest)                                    # <14>1 node svc trace - - level=.. k=v
    if ms:
        rec.service, rec.trace = ms.group("service"), ms.group("trace")
        rec.kv = parse_kv(ms.group("rest"))
        rec.level = rec.kv.get("level", "")
        rec.event = rec.kv.get("event", "")
        return rec

    # logfmt (incl. requestId=... function=... message="...") ------------------------------
    kv = parse_kv(rest)
    lvl = rest.split(" ", 1)[0]
    if lvl in ("DEBUG", "INFO", "WARN", "ERROR") and "level" not in kv:
        kv["level"] = lvl
    if "message" in kv and "event" not in kv:
        inner = parse_kv(kv["message"])
        if inner:
            kv.update(inner)
    rec.kv = kv
    rec.level = kv.get("level", "")
    rec.service = kv.get("service", kv.get("function", ""))
    rec.trace = kv.get("trace_id", kv.get("requestId", ""))
    rec.event = kv.get("event", "")
    return rec


def read_log(path: str) -> List[LogRec]:
    out: List[LogRec] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            r = parse_line(line, i)
            if r is not None:
                out.append(r)
    return out
