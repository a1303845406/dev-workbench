"""Full end-to-end functional check against a LIVE server (http://127.0.0.1:8642).

Usage:  .venv/Scripts/python scripts/e2e_check.py
Prints one PASS/FAIL line per feature, exits non-zero on any failure.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import httpx

BASE = "http://127.0.0.1:8642/api"
CONF = {"provider": "mock-demo", "context_files": [], "estimated_chars": 100,
        "confirmed_at": "2026-01-01T00:00:00+08:00"}

results: list[tuple[bool, str, str]] = []


def check(name: str, cond, detail: str = ""):
    results.append((bool(cond), name, detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)

    # ---------- system ----------
    r = c.get("/health"); check("health", r.status_code == 200 and r.json()["status"] == "ok")
    r = c.get("/version"); check("version", r.json().get("version") == "1.0.0")
    r = c.get("/settings"); s = r.json()
    check("settings masked key", s["providers"][0]["key"] == "***")
    check("settings templates", len(s["templates"]) >= 1)
    r = c.get("/templates"); check("templates list", len(r.json()["templates"]) >= 1)

    # ---------- project lifecycle ----------
    proj = "E2E全量验证"
    c.request("DELETE", f"/projects/{proj}", json={"confirm_name": proj})  # cleanup
    r = c.post("/projects", json={"name": proj, "template": "default-template"})
    check("project create 201", r.status_code == 201)
    check("scaffold files", "README.md" in r.json()["scaffold_report"]["files"])
    r = c.post("/projects", json={"name": proj})
    check("duplicate 409", r.status_code == 409 and r.json()["error"]["code"] == "PROJECT_NAME_CONFLICT")
    r = c.post("/projects", json={"name": "bad name!"})
    check("invalid name 400", r.status_code == 400)
    r = c.get("/projects")
    check("project listed", any(p["name"] == proj for p in r.json()["projects"]))
    r = c.request("DELETE", f"/projects/{proj}", json={})
    check("delete w/o confirm 400", r.status_code == 400)

    # ---------- docs ----------
    r = c.get(f"/projects/{proj}/docs")
    ids = [f["id"] for f in r.json()["files"]]
    check("docs list", "rules.md" in ids and "README.md" in ids)
    r = c.get(f"/projects/{proj}/docs/content", params={"path": "rules.md"})
    check("docs content", "工程规则" in r.json()["content"])
    r = c.patch(f"/projects/{proj}/docs/raw", json={"path": "../escape.md", "content": "x"})
    check("docs raw path guard 403", r.status_code == 403)

    # ---------- rules & constraints ----------
    r = c.patch(f"/projects/{proj}/rules", json={"content": "# 规则\n## 补充约束\n允许硬编码演示值"})
    check("rules conflict warning", r.json()["conflict_warning"]["hits"][0]["keyword"] == "允许硬编码")
    r = c.patch(f"/projects/{proj}/rules", json={"content": "# 规则\n遵守安全约束"})
    check("rules save clean", r.json()["conflict_warning"] is None)

    # ---------- tools ----------
    r = c.get(f"/projects/{proj}/tools")
    tools = r.json(); check("tools default", len(tools["tools"]) >= 5)
    tools["tools"][0]["enabled"] = False
    r = c.put(f"/projects/{proj}/tools", json=tools)
    check("tools save", r.json()["tools"][0]["enabled"] is False)

    # ---------- FR-01 pipeline (skip-S5 branch on pipeline B) ----------
    r = c.post(f"/projects/{proj}/pipelines", json={}); pl_a = r.json()["id"]
    r = c.post(f"/pipelines/{pl_a}/chat", json={"stage": "S1", "user_input": "x"})
    check("outbound 428", r.status_code == 428 and r.json()["error"]["code"] == "OUTBOUND_CONFIRM_REQUIRED")
    r = c.post(f"/pipelines/{pl_a}/chat", json={"stage": "S2", "user_input": "x", "outbound_confirmation": CONF})
    check("stage mismatch 409", r.status_code == 409)
    for stage in ["S1", "S2", "S3", "S4"]:
        r = c.post(f"/pipelines/{pl_a}/chat", json={"stage": stage, "user_input": "继续", "outbound_confirmation": CONF})
        ok = r.status_code == 200
        if ok:
            r2 = c.post(f"/pipelines/{pl_a}/confirm", json={"stage": stage, "comment": "ok"})
            ok = r2.status_code == 200 and r2.json()["next"] == f"S{int(stage[1])+1}"
        check(f"FR-01 {stage} chat+confirm", ok)
    # S5 two-phase freeze
    r = c.post(f"/pipelines/{pl_a}/chat", json={"stage": "S5", "user_input": "冻结", "outbound_confirmation": CONF})
    r = c.post(f"/pipelines/{pl_a}/confirm", json={"stage": "S5", "part": "business", "comment": "确认业务"})
    check("S5 business freeze", r.status_code == 200 and "清单冻结" in r.json().get("message", ""))
    d = c.get(f"/pipelines/{pl_a}").json()
    check("S5 still draft after business", d["stage"] == "S5")
    r = c.post(f"/pipelines/{pl_a}/confirm", json={"stage": "S5", "part": "scope"})
    check("S5 scope freeze -> S6", r.json()["next"] == "S6")
    for stage in ["S6", "S7"]:
        c.post(f"/pipelines/{pl_a}/chat", json={"stage": stage, "user_input": "继续", "outbound_confirmation": CONF})
        c.post(f"/pipelines/{pl_a}/confirm", json={"stage": stage})
    d = c.get(f"/pipelines/{pl_a}").json()
    check("FR-01 done", d["status"] == "done")
    check("draft sections intact", all(f"## {t}" in d["draft"] for t in
          ["一、项目语境", "二、原始表述记录", "三、需求摘要滚动区", "四、轮次记录", "五、范围冻结区", "六、实现形态细化"]))
    check("S7 confirmed in stage list", any(x["stage"] == "S7" and x["status"] == "confirmed" for x in d["stages"]))
    # skip-S5 branch
    r = c.post(f"/projects/{proj}/pipelines", json={}); pl_b = r.json()["id"]
    for stage in ["S1", "S2", "S3", "S4"]:
        c.post(f"/pipelines/{pl_b}/chat", json={"stage": stage, "user_input": "x", "outbound_confirmation": CONF})
        c.post(f"/pipelines/{pl_b}/confirm", json={"stage": stage})
    r = c.post(f"/pipelines/{pl_b}/chat", json={"stage": "S5", "user_input": "x", "outbound_confirmation": CONF})
    r = c.post(f"/pipelines/{pl_b}/skip-s5")
    check("skip S5 -> S6", r.status_code == 200 and r.json()["stage"] == "S6")

    # ---------- FR-03 scan ----------
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="devwb-e2e-"))
    legacy = tmp / "legacy"; (legacy / "src").mkdir(parents=True)
    (legacy / "说明.md").write_text("# legacy", encoding="utf-8")
    (legacy / "src" / "app.py").write_text("print(1)", encoding="utf-8")
    (legacy / "test_app.py").write_text("def test_x(): pass", encoding="utf-8")
    r = c.post(f"/projects/{proj}/scan", json={"target": "D:/definitely/not/allowed"})
    check("scan nonexistent 400", r.status_code == 400 and r.json()["error"]["code"] == "PATH_NOT_FOUND")
    r = c.post(f"/projects/{proj}/scan", json={"target": str(legacy)})
    check("scan whitelist 403", r.status_code == 403 and r.json()["error"]["code"] == "PATH_OUT_OF_WHITELIST")
    r = c.patch(f"/projects/{proj}", json={"scan_roots": [str(tmp)]})
    check("scan_roots update", str(tmp) in r.json()["scan_roots"])
    r = c.post(f"/projects/{proj}/scan", json={"target": str(legacy)})
    ok = r.status_code == 200 and r.json()["files_done"] == 3
    check("scan run", ok)
    r = c.get(f"/projects/{proj}/analysis")
    a = r.json()
    check("analysis files", "结构画像.md" in a and "规整建议.md" in a and "扫描摘要.json" in a)
    check("suggestions present", "需人工决策" in a["规整建议.md"] or "自动归位" in a["规整建议.md"])

    # ---------- FR-04 engineering ----------
    r = c.post(f"/projects/{proj}/compose", json={"complexity": "简单", "context_file_ids": ["rules.md"]})
    check("compose 428", r.status_code == 428)
    r = c.post(f"/projects/{proj}/compose", json={"complexity": "简单", "context_file_ids": ["rules.md"],
                                                  "outbound_confirmation": CONF})
    check("compose ok", r.status_code == 200 and "requirement" in r.json()["modules_selected"])
    comp = r.json()
    r = c.post(f"/projects/{proj}/compose", json={"complexity": "简单", "context_file_ids": [],
                                                  "user_modules_override": ["requirement"],
                                                  "outbound_confirmation": CONF})
    check("hard constraint 422", r.status_code == 422 and r.json()["error"]["code"] == "HARD_CONSTRAINT_VIOLATION")
    r = c.post(f"/projects/{proj}/compose", json={"complexity": "不存在档", "context_file_ids": [],
                                                  "outbound_confirmation": CONF})
    check("complexity unknown 400", r.status_code == 400)

    # over-budget task -> split into children
    proposal = {"tasks_proposal": [
        {"title": "大任务", "module": "development-backend", "description": "实现一个较大的模块",
         "acceptance": "可验收", "req_refs": ["F1"], "est_chars": 150000},
        {"title": "小任务", "module": "development-frontend", "description": "页面", "acceptance": "可验收",
         "req_refs": ["F2"], "est_chars": 8000},
    ]}
    r = c.post(f"/projects/{proj}/split", json={"proposal": proposal, "complexity": "中等",
                                                "modules_selected": comp["modules_selected"],
                                                "context_limit_chars": 128000, "coefficient": 0.6})
    sd = r.json()
    check("split multi-level", any(t["parent_id"] == "T1" for t in sd["tasks"]))
    check("split no overbudget", sd["overbudget_leaves"] == [])
    tasks = c.get(f"/projects/{proj}/tasks").json()
    check("tasks persisted", len(tasks["tasks"]) >= 3)

    r = c.post(f"/projects/{proj}/plan/generate")
    plan = r.json()
    check("plan generated", len(plan["rings"]) >= 4)
    # reorder invalid (move dependent after dependent)
    rings = plan["rings"]
    if len(rings) >= 2:
        bad = [dict(r) for r in rings]
        bad[0], bad[1] = bad[1], bad[0]
        r = c.put(f"/projects/{proj}/plan", json={"rings": [
            {"ring_id": b["ring_id"], "seq": i + 1, "parallel_group": b["parallel_group"]}
            for i, b in enumerate(bad)]})
        check("bad reorder 409", r.status_code == 409)
    # valid reorder: adjacent swap where the later ring does not depend on the former
    swap_at = next((i for i in range(len(rings) - 1)
                    if rings[i]["ring_id"] not in rings[i + 1]["depends_rings"]), None)
    if swap_at is not None:
        swapped = [dict(x) for x in rings]
        swapped[swap_at], swapped[swap_at + 1] = swapped[swap_at + 1], swapped[swap_at]
        r = c.put(f"/projects/{proj}/plan", json={"rings": [
            {"ring_id": b["ring_id"], "seq": i + 1, "parallel_group": b["parallel_group"]}
            for i, b in enumerate(swapped)]})
        check("valid reorder ok", r.status_code == 200 and r.json()["user_adjusted_order"] is True)
    else:
        check("valid reorder ok", False, "no swappable adjacent pair")

    r = c.post(f"/projects/{proj}/plan/confirm", json={})
    check("plan confirm 428", r.status_code == 428)
    r = c.post(f"/projects/{proj}/plan/confirm", json={"outbound_confirmation": CONF})
    check("plan confirm starts", r.status_code == 200 and r.json()["status"] == "running")
    for _ in range(120):
        p = c.get(f"/projects/{proj}/plan").json()
        if p["status"] in ("done", "suspended"):
            break
        time.sleep(0.3)
    check("plan executed to done", p["status"] == "done", p.get("resume_hint", ""))
    check("all rings done", all(r["status"] == "done" for r in p["rings"]))
    rid = p["rings"][0]["ring_id"]
    r = c.get(f"/rings/{rid}", params={"pid": proj})
    check("ring artifacts", all(r.json()["files"][f] for f in ("input.md", "output.md", "handoff.md", "meta.json")))

    # ---------- FR-07 prompts ----------
    prompts = c.get(f"/projects/{proj}/prompts").json()["prompts"]
    check("prompts generated", len(prompts) == len(p["rings"]))
    pid0 = prompts[0]["prompt_id"]
    cur = c.get(f"/prompts/{pid0}", params={"pid": proj}).json()
    check("prompt draft status", cur["status"] == "draft")
    r = c.put(f"/prompts/{pid0}", params={"pid": proj}, json={"body": cur["body"] + "\n\n<!-- 人工微调 -->"})
    v2 = r.json(); check("edit -> v2 draft", v2["version"] == 2 and v2["status"] == "draft")
    r = c.get(f"/prompts/{pid0}/diff", params={"pid": proj, "v1": 1, "v2": 2})
    check("diff v1v2", r.status_code == 200 and r.json()["changed"] > 0)
    r = c.post(f"/prompts/{pid0}/finalize", params={"pid": proj})
    check("finalize v2", r.status_code == 200)
    r = c.post(f"/prompts/{pid0}/rollback", params={"pid": proj}, json={"to_version": 1})
    check("rollback -> v3", r.json()["version"] == 3)
    r = c.post(f"/prompts/{pid0}/finalize", params={"pid": proj})
    r = c.post(f"/prompts/{pid0}/regen-handoff", params={"pid": proj})
    check("regen handoff", r.status_code == 200 and r.json().get("handoff"))
    r = c.post(f"/prompts/{pid0}/feedback", params={"pid": proj}, json={"result": "成功", "note": "一次通过"})
    check("feedback ok", r.status_code == 200)
    r = c.get(f"/projects/{proj}/feedback")
    check("feedback recorded", pid0 in r.json()["content"])

    # ---------- FR-05 testcases ----------
    r = c.post(f"/projects/{proj}/testcases", json={"task_ids": ["T1.1", "T2"], "performance": False,
                                                    "outbound_confirmation": CONF})
    check("testcases gen", r.status_code == 200 and len(r.json()["results"]) == 2)
    check("TC-P filtered", all("TC-P-" not in x["body"] for x in r.json()["results"]))
    r = c.post(f"/projects/{proj}/testcases", json={"task_ids": ["T1.1"], "performance": True,
                                                    "outbound_confirmation": CONF})
    check("performance cases kept", "TC-P-" in r.json()["results"][0]["body"])
    r = c.get(f"/projects/{proj}/testcases")
    check("testcases listed", len(r.json()["testcases"]) >= 2)

    # ---------- kanban ----------
    r = c.patch(f"/projects/{proj}/tasks/T2/status", json={"status": "fed"})
    check("kanban fed", r.json()["status"] == "fed")
    r = c.patch(f"/projects/{proj}/tasks/T2/status", json={"status": "done"})
    r = c.patch(f"/projects/{proj}/tasks/T1.1/status", json={"status": "failed", "note": "接口返回结构不符"})
    check("kanban failed", r.json()["status"] == "failed")
    k = c.get(f"/projects/{proj}/kanban").json()
    check("kanban columns", [col["key"] for col in k["columns"]] == ["pending", "fed", "done", "failed"])
    check("kanban progress", k["progress"]["done"] >= 1)

    # ---------- exports ----------
    for fmt in ["spec-kit", "taskmaster", "openspec", "markdown"]:
        r = c.post(f"/projects/{proj}/exports", json={"format": fmt, "scope": {"tasks": True, "requirements": True}})
        check(f"export {fmt}", r.status_code == 200 and "export_id" in r.json())
    exps = c.get(f"/projects/{proj}/exports").json()["exports"]
    check("exports listed", len(exps) >= 4)
    eid = exps[0]["export_id"]
    r = c.get(f"/exports/{eid}/download", params={"pid": proj})
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    check("export zip download", r.status_code == 200 and len(zf.namelist()) >= 1 and "meta.json" in zf.namelist())

    # ---------- audit / backups / SSE ----------
    r = c.get("/audit"); check("audit tail", len(r.json()["entries"]) > 10)
    r = c.post("/backups"); check("backup run", r.status_code == 202 and r.json()["files"] >= 0)
    r = c.get("/backups"); check("backup listed", len(r.json()["backups"]) >= 1)
    with c.stream("GET", "/events") as es:
        check("SSE reachable", es.status_code == 200 and es.headers["content-type"].startswith("text/event-stream"))

    # ---------- template save & delete project ----------
    tpl_name = f"e2e自定义模板-{int(time.time())}"
    r = c.post("/templates", json={"name": tpl_name, "from_project": proj})
    check("custom template", r.status_code == 201)
    r = c.get("/templates")
    check("custom template listed", any(t["id"] == tpl_name for t in r.json()["templates"]))
    r = c.request("DELETE", f"/projects/{proj}", json={"confirm_name": proj})
    check("project deleted 204", r.status_code == 204)
    r = c.get(f"/projects/{proj}")
    check("project gone 404", r.status_code == 404)

    c.close()
    failed = [x for x in results if not x[0]]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} passed, {len(failed)} failed =====")
    for _, name, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
