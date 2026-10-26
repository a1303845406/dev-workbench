"""FR-05 test case generation (functional/business mandatory, performance opt)."""
from __future__ import annotations

import re
from pathlib import Path

from . import paths
from .audit import audit
from .errors import ApiError
from .persist import atomic_write_text, now_iso, parse_front_matter, read_text, with_front_matter
from . import orchestrator as orch


def _pdir(project: str) -> Path:
    return paths.project_dir(project)


def list_testcases(project: str) -> list[dict]:
    root = _pdir(project) / "testcases"
    out = []
    if not root.exists():
        return out
    for f in sorted(root.rglob("*.md")):
        meta, body = parse_front_matter(read_text(f))
        out.append({**meta, "task_id": f.parent.name, "path": str(f.relative_to(_pdir(project))),
                    "body": body, "chars": len(body)})
    return out


async def generate(project: str, task_ids: list[str], *, performance: bool = False) -> list[dict]:
    from .provider import Message, get_provider, resolve_outbound
    data = orch.get_tasks(project)
    provider = get_provider(project)
    results = []
    for tid in task_ids:
        task = next((t for t in data["tasks"] if t["id"] == tid), None)
        if task is None:
            raise ApiError("TASK_NOT_FOUND", 404, "任务不存在", {"task": tid})
        user = (f"为任务生成测试用例集。\n## 任务 {tid}：{task['title']}\n- 描述：{task.get('description')}\n"
                f"- 验收：{task.get('acceptance')}\n- 关联需求：{', '.join(task.get('req_refs', []))}\n"
                f"- 性能用例：{'需要（TC-P-*）' if performance else '不需要（禁止 TC-P-*）'}\n"
                "[mock-mode: testcase]")
        resolve_outbound(provider.name, [f"pipeline/tasks.json#{tid}"], len(user))
        result = await provider.chat([Message("system", "你是测试设计模块。[module: test]"),
                                      Message("user", user)])
        body = result.text
        warns = []
        if not performance and "TC-P-" in body:
            body = re.sub(r"^## TC-P-.*?(?=^## TC-|\Z)", "", body, flags=re.MULTILINE | re.DOTALL)
            warns.append("检测到 TC-P 用例，因性能开关关闭已被过滤（AC-05）")
        coverage = task.get("req_refs", [])
        out_dir = _pdir(project) / "testcases" / tid
        existing = list(out_dir.glob("用例集*.md"))
        fname = "用例集.md" if not existing else f"用例集-v{len(existing) + 1}.md"
        meta = {"schemaVersion": 1, "task_id": tid, "coverage": coverage,
                "includes": {"functional": True, "business": True, "performance": performance},
                "generated_at": now_iso()}
        atomic_write_text(out_dir / fname, with_front_matter(meta, body + "\n"))
        audit("testcase.generate", project=project, target=tid,
              detail={"performance": performance, "file": fname})
        results.append({"task_id": tid, "file": fname, "warnings": warns, "body": body})
    return results
