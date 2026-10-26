from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


class WorkspaceScanner:
    """Read-only workspace scanner used to verify agent-declared file changes."""

    def __init__(self, workspace: str):
        self.root = Path(workspace).resolve()

    def snapshot(self) -> dict[str, str]:
        """Return content hashes for files outside the workspace's .git directory."""
        if not self.root.is_dir():
            return {}
        snapshot: dict[str, str] = {}
        for path in self.root.rglob("*"):
            if not path.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} for part in path.parts):
                continue
            try:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                snapshot[path.relative_to(self.root).as_posix()] = digest.hexdigest()
            except OSError:
                continue
        return snapshot

    def _git_changed(self) -> set[str] | None:
        if not self.root.is_dir():
            return set()
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        changed: set[str] = set()
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changed.add(path.replace(chr(92), "/").strip('"'))
        return changed

    def actual_changed(self, baseline_hashes: dict[str, str] | None = None) -> set[str]:
        """Use git status and content hashes to identify post-baseline changes."""
        # A captured manifest is authoritative, including an empty {} manifest.
        # Do not consult a parent Git repository for an empty workspace.
        git_changed = None if baseline_hashes is not None else self._git_changed()
        current = self.snapshot()
        if baseline_hashes is not None:
            changed = {
                path
                for path in set(current) | set(baseline_hashes)
                if current.get(path) != baseline_hashes.get(path)
            }
        else:
            changed = set(git_changed) if git_changed is not None else set(current)
        return changed

    def verify(self, declared: list[str], baseline: set[str] | dict[str, Any] | None = None) -> dict[str, Any]:
        baseline_hashes: dict[str, str] | None = None
        if isinstance(baseline, dict):
            baseline_files = set(baseline.get("files", []))
            # An empty hash map is a valid captured baseline for an empty workspace.
            # Keep it as {} so verification never falls back to git status in a
            # parent repository and accidentally reports unrelated files.
            baseline_hashes = dict(baseline.get("hashes", {}))
        else:
            baseline_files = set(baseline or set())
        normalized = sorted({str(x).replace(chr(92), "/") for x in declared})
        unsafe = [
            x for x in normalized
            if not x or x.startswith("/") or x.startswith("../") or x == ".."
            or any(part == ".." for part in x.split("/"))
            or (len(x) >= 3 and x[1] == ":" and x[2] == "/")
        ]
        actual = self.actual_changed(baseline_hashes)
        delta = actual if baseline_hashes is not None else actual - baseline_files
        missing = sorted(set(normalized) - delta)
        undeclared = sorted(delta - set(normalized))
        return {
            "verified": not unsafe and not missing and not undeclared,
            "declared_files": normalized,
            "baseline_files": sorted(baseline_files),
            "actual_files": sorted(actual),
            "delta_files": sorted(delta),
            "missing_files": missing,
            "undeclared_files": undeclared,
            "unsafe_files": unsafe,
            "workspace": self.root.as_posix(),
        }
