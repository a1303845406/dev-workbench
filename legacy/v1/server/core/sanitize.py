"""Sanitize engine (FR-04 规则 7 / 04 文档 §5): block/confirm regex rules."""
from __future__ import annotations

import re

from .config import config_center


def scan(text: str) -> list[dict]:
    """Return hits: [{rule_id, severity, excerpt(masked), position}]."""
    rules = (config_center.sanitize_rules() or {}).get("rules", [])
    hits: list[dict] = []
    for rule in rules:
        try:
            pat = re.compile(rule["pattern"])
        except re.error:
            continue
        for m in pat.finditer(text):
            start, end = m.start(), m.end()
            before = text[max(0, start - 12):start]
            after = text[end:end + 12]
            masked = len(m.group(0))
            excerpt = f"{before}[{'*' * min(masked, 8)}]{after}"
            hits.append({"rule_id": rule.get("id", "unknown"),
                         "severity": rule.get("severity", "confirm"),
                         "excerpt": excerpt, "position": start})
    hits.sort(key=lambda h: h["position"])
    return hits


def classify(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    blocked = [h for h in hits if h["severity"] == "block"]
    confirm = [h for h in hits if h["severity"] != "block"]
    return blocked, confirm
