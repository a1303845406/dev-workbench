"""Strict proposal contracts and independently computed review checks."""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SECTIONS = ["背景与问题", "目标与成功指标", "用户与场景", "范围与非目标", "流程与业务规则", "功能需求", "数据与接口", "非功能需求", "约束与假设", "验收与追踪", "风险与待确认", "决策与参考"]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Findings(StrictModel):
    unresolved: list[str] = Field(default_factory=list, max_length=100)
    checks: list[str] = Field(default_factory=list, max_length=100)

class Requirement(Findings):
    document: str = Field(min_length=1, max_length=100000)

class Node(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    parent_id: str | None = None
    type: Literal["module", "feature"]
    title: str = Field(min_length=1, max_length=200)
    responsibility: str = Field(min_length=1, max_length=5000)
    exclusions: str = Field(min_length=1, max_length=5000)
    requirement_ids: list[str] = Field(min_length=1, max_length=100)
    acceptance: str = Field(min_length=1, max_length=5000)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    rationale: str = Field(min_length=1, max_length=5000)

class Decomposition(Findings):
    overview: str = Field(min_length=1, max_length=30000)
    nodes: list[Node] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_graph(self):
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("节点 ID 重复")
        for n in self.nodes:
            if n.parent_id is not None and (n.parent_id not in nodes or nodes[n.parent_id].type != "module"):
                raise ValueError("父节点必须是存在的模块")
            if n.type == "feature" and n.parent_id is None:
                raise ValueError("特性必须归属于模块")
            if any(d not in nodes or d == n.id for d in n.dependencies):
                raise ValueError("依赖不存在或引用自身")
        # Ownership and dependency graphs are separate; neither may contain cycles.
        for field in ("parent_id", "dependencies"):
            done, visiting = set(), set()
            def visit(key):
                if key in visiting:
                    raise ValueError("模块归属或依赖存在循环")
                if key in done:
                    return
                visiting.add(key)
                value = getattr(nodes[key], field)
                for target in ([value] if field == "parent_id" and value else value if field == "dependencies" else []):
                    visit(target)
                visiting.remove(key)
                done.add(key)
            for key in nodes:
                visit(key)
        return self

class Design(Findings):
    detailed_design: str = Field(min_length=1, max_length=100000)
    prototype_html: str = Field(min_length=1, max_length=150000)
    data_design: str = Field(min_length=1, max_length=100000)

MODELS = {"requirement": Requirement, "decomposition": Decomposition, "design": Design}

def normalize(kind: str, data: dict) -> dict:
    return MODELS[kind].model_validate(data).model_dump()

def requirement_ids(document: str) -> set[str]:
    return set(re.findall(r"\bFR-\d+\b", document))

def review_checks(kind: str, data: dict, requirement: dict | None = None, decomposition: dict | None = None) -> list[str]:
    checks = ["待确认：" + item for item in data.get("unresolved", []) if item.strip()]
    if kind == "requirement":
        checks += ["缺少需求章节：" + title for title in SECTIONS if title not in data["document"]]
        if not requirement_ids(data["document"]):
            checks.append("缺少可追踪的 FR-001 格式功能需求编号")
    elif kind == "decomposition":
        expected = requirement_ids((requirement or {}).get("document", ""))
        covered = {r for n in data["nodes"] if n["type"] == "feature" for r in n["requirement_ids"]}
        mentioned = {r for n in data["nodes"] for r in n["requirement_ids"]}
        if expected - covered:
            checks.append("缺少特性覆盖：" + ", ".join(sorted(expected - covered)))
        if mentioned - expected:
            checks.append("引用了不存在的需求：" + ", ".join(sorted(mentioned - expected)))
        for n in data["nodes"]:
            if n["type"] == "module" and not any(c["parent_id"] == n["id"] for c in data["nodes"]):
                checks.append("模块未拆至特性：" + n["id"])
    else:
        for n in (decomposition or {}).get("nodes", []):
            if n["type"] != "feature":
                continue
            for key, label in [("detailed_design", "详细设计"), ("prototype_html", "原型"), ("data_design", "数据设计")]:
                if not re.search(r"(?<![\w-])" + re.escape(n["id"]) + r"(?![\w-])", data[key]):
                    checks.append(label + "缺少特性追踪：" + n["id"])
    return checks
