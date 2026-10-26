"""Configuration center: product-level configs under configs/ with lazy mtime
hot reload, .bak fallback and degraded flags (总体设计 6.4 / 04 文档 §8)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .errors import config_invalid
from .persist import parse_front_matter, read_text

_CACHE_TTL_S = 5.0


@dataclass
class Loaded:
    data: object
    mtime: float = 0.0
    degraded: bool = False
    why: str = ""


def _read_with_fallback(path: Path, loader):  # loader: (text) -> object
    mtime = path.stat().st_mtime if path.exists() else 0.0
    try:
        return Loaded(data=loader(read_text(path)), mtime=mtime)
    except Exception as e:  # noqa: BLE001 - degrade, never crash on config
        for bak in (path.with_suffix(path.suffix + ".bak"),):
            if bak.exists():
                try:
                    return Loaded(data=loader(read_text(bak)), mtime=mtime, degraded=True, why=str(e))
                except Exception:
                    pass
        return Loaded(data=None, mtime=mtime, degraded=True, why=str(e))


class ConfigCenter:
    def __init__(self) -> None:
        self.root = paths.config_root()
        self._json: dict[str, Loaded] = {}
        self._text: dict[str, Loaded] = {}
        self._last_check = 0.0

    # ---------------------------------------------------------------- loaders
    def _load_json(self, key: str, rel: Path, default: dict) -> dict:
        entry = self._json.get(key)
        path = self.root / rel
        if entry is None or (time.time() - self._last_check > _CACHE_TTL_S):
            mtime = path.stat().st_mtime if path.exists() else 0.0
            if entry is None or entry.mtime != mtime:
                entry = _read_with_fallback(path, json.loads)
                if entry.data is None:
                    entry.data = default
                self._json[key] = entry
            self._last_check = time.time()
        return entry.data

    def _load_text(self, key: str, rel: Path) -> Loaded:
        entry = self._text.get(key)
        path = self.root / rel
        if entry is None or (time.time() - self._last_check > _CACHE_TTL_S):
            mtime = path.stat().st_mtime if path.exists() else 0.0
            if entry is None or entry.mtime != mtime:
                entry = _read_with_fallback(path, lambda t: t) if path.exists() else Loaded(data="")
                self._text[key] = entry
            self._last_check = time.time()
        return entry

    # -------------------------------------------------------------- accessors
    def providers(self) -> dict:
        return self._load_json("providers", Path("providers.json"),
                               {"providers": [{"name": "mock-demo", "base_url": "mock://local",
                                               "model": "mock-1", "key_env": "", "stream": True}],
                                "default": "mock-demo"})

    def complexity_map(self) -> dict:
        return self._load_json("complexity", Path("complexity-map.json"), {"levels": {}})

    def sanitize_rules(self) -> dict:
        return self._load_json("sanitize", Path("sanitize-rules.json"), {"rules": []})

    def tools_default(self) -> dict:
        return self._load_json("tools_default", Path("tools-default.json"), {"tools": []})

    def constraints_package(self) -> tuple[dict, str]:
        entry = self._load_text("constraints", Path("constraints-package.md"))
        meta, body = parse_front_matter(entry.data or "")
        return meta, body

    def module_prompt(self, module_key: str) -> tuple[dict, str]:
        """module_key e.g. `modules/requirement.md` relative path."""
        entry = self._load_text(f"module:{module_key}", Path(module_key))
        meta, body = parse_front_matter(entry.data or "")
        return meta, body

    def template(self, template_dir: str) -> dict:
        safe = template_dir or "default-template"
        return self._load_json(f"template:{safe}", Path("templates") / safe / "template.json", {})

    def template_dirs(self) -> list[str]:
        tdir = self.root / "templates"
        if not tdir.exists():
            return []
        return sorted(p.name for p in tdir.iterdir() if p.is_dir() and (p / "template.json").is_file())

    def degraded_configs(self) -> list[dict]:
        out = []
        for key, entry in {**self._json, **self._text}.items():
            if entry.degraded:
                out.append({"key": key, "why": entry.why})
        return out


config_center = ConfigCenter()
