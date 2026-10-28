"""Executable modular-monolith dependency rules (stdlib only, no paid calls)."""
import ast
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps/api/src/devworkbench"
WEB = ROOT / "apps/web/src"


def python_violation(owner: str, dependency: str) -> bool:
    if not dependency.startswith("devworkbench."):
        if owner.endswith(".domain"):
            return dependency.split(".")[0] in {"fastapi", "httpx", "sqlite3", "subprocess", "os", "pathlib"}
        return False
    if dependency == "devworkbench.main":
        return True
    suffix = owner.removeprefix("devworkbench.")
    target = dependency.removeprefix("devworkbench.")
    if suffix.startswith("shared."):
        return not target.startswith("shared.")
    if suffix.startswith("infrastructure."):
        return target.startswith("modules.")
    if suffix.startswith("modules."):
        module = suffix.split(".")[1]
        if target.startswith("modules.") and target.split(".")[1] != module:
            return True
        if suffix.endswith(".domain"):
            return not (target.startswith("shared.") or target == suffix)
        if suffix.endswith((".application", ".prompts")) and target.endswith(".router"):
            return True
    return False


def web_violation(owner: str, target: str) -> bool:
    if owner.startswith("shared/"):
        return not target.startswith("shared/")
    if owner.startswith("features/"):
        feature = owner.split("/")[1]
        return target.startswith("app/") or (target.startswith("features/") and target.split("/")[1] != feature)
    return False


def test_backend_dependencies_follow_layer_direction():
    failures = []
    for path in API.rglob("*.py"):
        owner = "devworkbench." + ".".join(path.relative_to(API).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            dependencies = []
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"Use explicit absolute imports: {path}:{node.lineno}"
                dependencies = [node.module or ""]
            elif isinstance(node, ast.Import):
                dependencies = [item.name for item in node.names]
            for dependency in dependencies:
                if python_violation(owner, dependency):
                    failures.append(f"{owner}:{node.lineno} -> {dependency}")
    assert not failures, "Forbidden dependencies:\n" + "\n".join(failures)


def test_frontend_dependencies_follow_feature_boundaries():
    failures = []
    for path in WEB.rglob("*"):
        if path.suffix not in {".ts", ".vue"}:
            continue
        owner = path.relative_to(WEB).as_posix()
        source = path.read_text(encoding="utf-8")
        imports = re.findall(r"(?:from\s*|import\s*\(?\s*)[\"']([^\"']+)[\"']", source)
        for reference in imports:
            if not reference.startswith("."):
                assert not reference.startswith(("@/", "~/")), "Alias needs explicit boundary resolution"
                continue
            resolved = (path.parent / reference).resolve()
            assert resolved.is_relative_to(WEB), f"Import escapes web source: {owner} -> {reference}"
            target = resolved.relative_to(WEB).as_posix()
            if web_violation(owner, target):
                failures.append(f"{owner} -> {target}")
    assert not failures, "Forbidden dependencies:\n" + "\n".join(failures)


@pytest.mark.parametrize("owner,dependency", [
    ("devworkbench.shared.errors", "devworkbench.infrastructure.models.provider"),
    ("devworkbench.infrastructure.models.provider", "devworkbench.modules.planning.domain"),
    ("devworkbench.modules.planning.application", "devworkbench.modules.workbench.application"),
    ("devworkbench.modules.planning.domain", "httpx"),
    ("devworkbench.modules.planning.application", "devworkbench.modules.planning.router"),
])
def test_backend_rules_detect_forbidden_examples(owner, dependency):
    assert python_violation(owner, dependency)


@pytest.mark.parametrize("owner,target", [
    ("shared/api/http.ts", "features/planning/types.ts"),
    ("features/planning/PlanningWorkspace.vue", "app/App.vue"),
    ("features/planning/PlanningWorkspace.vue", "features/model-settings/ModelSettings.vue"),
])
def test_frontend_rules_detect_forbidden_examples(owner, target):
    assert web_violation(owner, target)


def test_runtime_defaults_do_not_depend_on_current_directory(tmp_path, monkeypatch):
    from devworkbench.shared.paths import data_root, web_dist
    monkeypatch.delenv("DEVWB_HOME", raising=False)
    monkeypatch.delenv("DEVWB_WEB_DIST", raising=False)
    monkeypatch.chdir(tmp_path)
    assert data_root() == ROOT / "workspace"
    assert web_dist() == ROOT / "apps/web/dist"


def test_runtime_path_overrides_are_honored(tmp_path, monkeypatch):
    from devworkbench.shared.paths import data_root, web_dist
    monkeypatch.setenv("DEVWB_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("DEVWB_WEB_DIST", str(tmp_path / "web"))
    assert data_root() == tmp_path / "data"
    assert web_dist() == tmp_path / "web"
