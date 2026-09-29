"""Layered coding constraints (SRS 2.9 / 04 文档 §7):
package loading, fixed injection order (package → project rules), keyword-level
conflict warning for rules.md relaxations."""
from __future__ import annotations

from .config import config_center


def package_version() -> str:
    meta, _ = config_center.constraints_package()
    return str(meta.get("package_version", "0.0.0"))


def conflict_keywords() -> list[str]:
    meta, _ = config_center.constraints_package()
    kws = meta.get("conflict_keywords", [])
    if isinstance(kws, str):
        kws = [k.strip() for k in kws.split(";") if k.strip()]
    return kws


def conflict_check(rules_md: str) -> list[dict]:
    """Keyword-level (case-insensitive) match. Warning only — never blocks save."""
    hits = []
    for kw in conflict_keywords():
        for i, line in enumerate(rules_md.splitlines(), 1):
            if kw.lower() in line.lower():
                hits.append({"keyword": kw, "line": i, "excerpt": line.strip()[:80]})
    return hits


def global_layer(rules_md: str) -> str:
    """Fixed order: constraints package first, then project rules (2.9.3-1)."""
    _, body = config_center.constraints_package()
    return f"<!-- layer:global -->\n{body.strip()}\n\n{rules_md.strip()}"
