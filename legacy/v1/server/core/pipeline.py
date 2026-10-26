"""FR-01 requirement-exploration pipeline engine.

- 7-stage state machine with confirm gates (AC-01); only S5 skippable.
- State fully on disk: pipeline/state/index.json + <id>.state.json + rolling
  draft doc (六区底稿) — browser close never loses progress (FR-06).
- Stateless per-round calls: role layer + draft snapshot + user input (规则 8).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

from . import events, paths
from .audit import audit
from .config import config_center
from .errors import ApiError, stage_not_confirmed
from .persist import (atomic_write_json, atomic_write_text, load_json, now_iso,
                      parse_front_matter, read_text, with_front_matter)

SCHEMA = 1

STAGES = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
STAGE_NAMES = {"S1": "引导对话", "S2": "碎片提炼", "S3": "澄清追问", "S4": "摘要确认",
               "S5": "范围冻结", "S6": "文档编写", "S7": "评审修订"}
SECTION_TITLES = ["一、项目语境", "二、原始表述记录", "三、需求摘要滚动区",
                  "四、轮次记录", "五、范围冻结区", "六、实现形态细化"]

_DRAFT_PATCH_RE = re.compile(r"```draft-patch\s*\n(.*?)\n```", re.DOTALL)


# ------------------------------------------------------------------ layout

def _pdir(project: str) -> Path:
    return paths.project_dir(project)


def _state_index_path(project: str) -> Path:
    return _pdir(project) / "pipeline" / "state" / "index.json"


def _state_path(project: str, plid: str) -> Path:
    return _pdir(project) / "pipeline" / "state" / f"{plid}.state.json"


def _draft_path(project: str) -> Path:
    return _pdir(project) / "docs" / "需求" / "滚动底稿.md"


def _stage_dir(project: str) -> Path:
    return _pdir(project) / "docs" / "需求" / "阶段记录"


# ------------------------------------------------------------------ state io

def _load_index(project: str) -> dict:
    return load_json(_state_index_path(project), default={"project": project, "pipelines": []})


def _save_index(project: str, index: dict) -> None:
    atomic_write_json(_state_index_path(project), index)


def load_pipeline(project: str, plid: str) -> dict:
    path = _state_path(project, plid)
    if not path.exists():
        raise ApiError("PIPELINE_NOT_FOUND", 404, "流水线不存在", {"plid": plid})
    return load_json(path)


def list_pipelines(project: str) -> list[dict]:
    return _load_index(project).get("pipelines", [])


def find_project_of_pipeline(plid: str) -> str | None:
    root = paths.projects_root()
    if not root.exists():
        return None
    for d in root.iterdir():
        if d.is_dir() and (_state_path(d.name, plid)).exists():
            return d.name
    return None


# ------------------------------------------------------------------ creation

_DRAFT_TEMPLATE = """# 滚动工作底稿 · {project}
> schemaVersion: 1 · 流水线id: {plid} · 当前阶段: S1 · 更新时间: {ts}
> 维护: FR-01 流水线引擎（人工可读可改，改后以文件为准）

## 一、项目语境
<!-- 项目名/目录/干系人/部署形态等 5W1H 沉淀 -->

## 二、原始表述记录
<!-- R1/R2/...：逐条原文引用 + 模糊点标注（[模糊: …]） -->

## 三、需求摘要滚动区
<!-- B/F/N/C 四类清单表格；每行含 编号|描述|来源Rxx|状态(开放/澄清中/已确认) -->

## 四、轮次记录
<!-- 第 N 轮：提问 / 回答 / 结论 -->

## 五、范围冻结区
<!-- S5 产出：业务冻结记录 / 功能·非功能清单冻结记录 / 变更流程记入 -->

## 六、实现形态细化
<!-- 阶段性技术讨论沉淀，不进入 SRS 正文 -->
"""


def create_pipeline(project: str, title: str = "需求探索") -> dict:
    plid = f"fr01-{time.strftime('%Y%m%d-%H%M%S')}"
    pdir = _pdir(project)
    atomic_write_text(_draft_path(project), _DRAFT_TEMPLATE.format(project=project, plid=plid, ts=now_iso()))
    state = {"schemaVersion": SCHEMA, "id": plid, "type": "fr01", "title": title,
             "stage": "S1", "status": "running", "rounds": [], "stage_seq": {s: 0 for s in STAGES},
             "freeze": {}, "updated_at": now_iso()}
    atomic_write_json(_state_path(project, plid), state)
    index = _load_index(project)
    index["pipelines"] = [p for p in index.get("pipelines", []) if p.get("id") != plid]
    index["pipelines"].insert(0, {"id": plid, "type": "fr01", "title": title, "status": "running",
                                  "stage": "S1", "updated_at": now_iso()})
    _save_index(project, index)
    audit("pipeline.create", project=project, target=plid)
    return state


# ------------------------------------------------------------------ draft doc

def read_draft(project: str) -> str:
    path = _draft_path(project)
    if not path.exists():
        return ""
    return read_text(path)


def _split_sections(draft: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    positions = []
    for title in SECTION_TITLES:
        m = re.search(rf"^## {re.escape(title)}\s*$", draft, re.MULTILINE)
        positions.append((title, m.start() if m else None, m.end() if m else None))
    for i, (title, s, e) in enumerate(positions):
        if s is None:
            sections[title] = ""
            continue
        next_s = positions[i + 1][1] if i + 1 < len(positions) else len(draft)
        sections[title] = draft[e:next_s].strip("\n") if e is not None else ""
    return sections


def _update_header(draft: str, plid: str, stage: str) -> str:
    return re.sub(r"^> schemaVersion: .* · 流水线id: .* · 当前阶段: .* · 更新时间: .*$",
                  f"> schemaVersion: 1 · 流水线id: {plid} · 当前阶段: {stage} · 更新时间: {now_iso()}",
                  draft, count=1, flags=re.MULTILINE)


def _apply_patches(draft: str, patches: list[dict]) -> tuple[str, int]:
    sections = _split_sections(draft)
    title_by_no = {s.split("、")[0]: s for s in SECTION_TITLES}
    applied = 0
    for p in patches:
        sec_name = str(p.get("section", "")).strip()
        title = sec_name if sec_name in SECTION_TITLES else title_by_no.get(sec_name)
        if title is None:
            continue
        content = str(p.get("content", ""))
        action = p.get("action", "append")
        cur = sections.get(title, "")
        sections[title] = (cur + "\n\n" + content).strip("\n") if action == "append" else content
        applied += 1
    head = draft.split("## 一、")[0]
    body = head
    for title in SECTION_TITLES:
        body += f"## {title}\n\n{sections.get(title, '')}\n\n"
    return body.rstrip() + "\n", applied


# ------------------------------------------------------------------- chat

def _role_layer(project: str, stage: str) -> str:
    meta, body = config_center.module_prompt(f"modules/fr01-{stage.lower()}.md")
    if not body:
        body = f"你是软件需求分析团队的需求{STAGE_NAMES.get(stage, '')}师。"
    return (f"<!-- layer:role -->\n{body.strip()}\n\n"
            f"[stage: {stage}]\n[mock-mode: fr01]\n[project: {project}]")


def _stage_file_seq(state: dict, stage: str) -> int:
    return int(state.get("stage_seq", {}).get(stage, 0))


def _stage_file(project: str, stage: str, seq: int, *, status: str, round_no: int,
                output: str, comment: str = "") -> Path:
    _stage_dir(project).mkdir(parents=True, exist_ok=True)
    path = _stage_dir(project) / f"{stage}-{STAGE_NAMES[stage]}-{seq}.md"
    meta = {"stage": stage, "seq": seq, "round": round_no, "status": status}
    if status == "confirmed":
        meta["confirmed_at"] = now_iso()
    body = f"## 产出\n{output}\n"
    if comment:
        body += f"\n## 确认\n- 用户意见：{comment}\n- 确认时间：{now_iso()}\n"
    atomic_write_text(path, with_front_matter(meta, body))
    return path


async def chat(project: str, plid: str, stage: str, user_input: str, *,
               stream: bool = False) -> dict:
    """One stateless round: role layer + draft snapshot + user input → provider."""
    from .provider import Message, get_provider, resolve_outbound
    state = load_pipeline(project, plid)
    if state["stage"] != stage:
        raise ApiError("STAGE_MISMATCH", 409, "阶段与流水线当前阶段不一致",
                       {"current": state["stage"], "got": stage})
    round_no = len(state.get("rounds", [])) + 1
    draft = read_draft(project)
    system = _role_layer(project, stage)
    user = f"<!-- 状态层：滚动底稿全文快照 -->\n{draft}\n\n---\n本轮用户输入：\n{user_input}"
    digest = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]

    provider = get_provider(project)
    resolve_outbound(provider.name, [f"projects/{project}/docs/需求/滚动底稿.md"], len(user))
    chunks: list[str] = []

    async def cb(delta: str) -> None:
        chunks.append(delta)
        if stream:
            await events.publish("pipeline.round_delta", {"plid": plid, "stage": stage, "delta": delta})

    result = await provider.chat([Message("system", system), Message("user", user)],
                                 stream_cb=cb if stream else None)

    reply = result.text
    # parse draft-patch block
    patches: list[dict] = []
    m = _DRAFT_PATCH_RE.search(reply)
    if m:
        try:
            parsed = json.loads(m.group(1))
            patches = parsed if isinstance(parsed, list) else [parsed]
            reply = (reply[:m.start()] + reply[m.end():]).strip()
        except json.JSONDecodeError:
            patches = []

    if patches:
        draft, patches_applied = _apply_patches(draft, patches)
    else:
        draft, patches_applied = _apply_patches(
            draft, [{"section": "四", "action": "append",
                     "content": f"### 第 {round_no} 轮（{stage}）\n- 输入摘要：{user_input[:80]}…\n- 产出见阶段记录"}])
    draft = _update_header(draft, plid, stage)
    atomic_write_text(_draft_path(project), draft)

    seq = _stage_file_seq(state, stage) + (1 if state.get("last_stage") != stage else 0)
    state["last_stage"] = stage
    state["stage_seq"][stage] = max(seq, 1)
    draft_file = _stage_file(project, stage, max(seq, 1), status="draft", round_no=round_no,
                             output=reply.strip())
    state["rounds"].append({"round": round_no, "stage": stage, "input_digest": digest,
                            "output_ref": str(draft_file.relative_to(_pdir(project))), "ts": now_iso()})
    state["updated_at"] = now_iso()
    atomic_write_json(_state_path(project, plid), state)
    await events.publish("pipeline.round_done", {"plid": plid, "stage": stage, "round": round_no})
    return {"round": round_no, "stage": stage, "reply_markdown": reply.strip(),
            "patches_applied": patches_applied, "stage_file": draft_file.name,
            "usage": result.usage}


# ------------------------------------------------------------------ confirm

def _next_stage(stage: str) -> str | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def confirm(project: str, plid: str, stage: str, comment: str = "",
            expected_status: str = "draft", part: str | None = None) -> dict:
    """Confirm gate transaction: write confirmed record → update draft header →
    update index → audit. Any failure raises before state changes (AC-01)."""
    state = load_pipeline(project, plid)
    if state["stage"] != stage:
        raise stage_not_confirmed({"current": state["stage"], "got": stage})
    seq = int(state.get("stage_seq", {}).get(stage, 0))
    if seq == 0:
        raise stage_not_confirmed({"why": "该阶段尚无产出草稿"})

    if stage == "S5" and part:
        state.setdefault("freeze", {})[f"frozen_{part}"] = now_iso()
        if part == "business":
            _stage_file(project, stage, seq, status="draft", round_no=len(state.get("rounds", [])),
                        output="业务冻结已确认，待清单冻结。", comment=comment)
            atomic_write_json(_state_path(project, plid), {**state, "updated_at": now_iso()})
            return {"stage": stage, "status": "draft", "freeze": state["freeze"],
                    "message": "业务冻结完成，请确认清单冻结"}
        state["freeze"]["frozen_scope"] = state["freeze"].get("frozen_scope") or now_iso()

    # 1) flip the latest draft stage file to confirmed
    stage_dir = _stage_dir(project)
    candidates = sorted(stage_dir.glob(f"{stage}-*.md")) if stage_dir.exists() else []
    latest = None
    for c in reversed(candidates):
        meta, _ = parse_front_matter(read_text(c))
        if meta.get("status") == "draft":
            latest = c
            break
    if latest is None and not candidates:
        raise stage_not_confirmed({"why": "未找到阶段产出文件"})
    if latest is not None:
        meta, body = parse_front_matter(read_text(latest))
        meta["status"] = "confirmed"
        meta["confirmed_at"] = now_iso()
        if comment:
            body += f"\n## 确认\n- 用户意见：{comment}\n- 确认时间：{now_iso()}\n"
        atomic_write_text(latest, with_front_matter(meta, body))

    # 2) update draft header + 3) index
    nxt = _next_stage(stage)
    state["stage"] = nxt or state["stage"]
    if nxt is None:
        state["status"] = "done"
    state["updated_at"] = now_iso()
    atomic_write_json(_state_path(project, plid), state)
    draft = read_draft(project)
    atomic_write_text(_draft_path(project), _update_header(draft, plid, state["stage"]))
    index = _load_index(project)
    for p in index.get("pipelines", []):
        if p.get("id") == plid:
            p["stage"] = state["stage"]
            p["status"] = state["status"]
            p["updated_at"] = now_iso()
    _save_index(project, index)
    audit("pipeline.confirm", project=project, target=plid, detail={"stage": stage})
    return {"stage": state["stage"], "status": state["status"], "next": nxt,
            "freeze": state.get("freeze", {})}


def skip_s5(project: str, plid: str) -> dict:
    state = load_pipeline(project, plid)
    if state["stage"] != "S5":
        raise ApiError("STAGE_MISMATCH", 409, "仅当流水线处于 S5 时可跳过", {"current": state["stage"]})
    seq = int(state.get("stage_seq", {}).get("S5", 0)) or 1
    _stage_file(project, "S5", seq, status="confirmed", round_no=len(state.get("rounds", [])),
                output="范围冻结被用户显式跳过（FR-01 规则 1）。")
    state["stage"] = "S6"
    state.setdefault("stage_seq", {})["S5"] = seq
    state["updated_at"] = now_iso()
    atomic_write_json(_state_path(project, plid), state)
    index = _load_index(project)
    for p in index.get("pipelines", []):
        if p.get("id") == plid:
            p["stage"] = "S6"
    _save_index(project, index)
    audit("pipeline.skip_s5", project=project, target=plid)
    return {"stage": "S6", "skipped": "S5"}


def pipeline_detail(project: str, plid: str) -> dict:
    state = load_pipeline(project, plid)
    stages = []
    for s in STAGES:
        seq = int(state.get("stage_seq", {}).get(s, 0))
        status = "not_started"
        if seq:
            stage_dir = _stage_dir(project)
            cands = sorted(stage_dir.glob(f"{s}-*.md")) if stage_dir.exists() else []
            st = "draft"
            for c in reversed(cands):
                meta, _ = parse_front_matter(read_text(c))
                if meta.get("status") in ("draft", "confirmed", "skipped"):
                    st = meta["status"]
                    break
            status = st
        current = state["stage"] == s
        stages.append({"stage": s, "name": STAGE_NAMES[s], "status": status, "current": current,
                       "skippable": s == "S5"})
    return {**{k: v for k, v in state.items() if k != "rounds"},
            "rounds_count": len(state.get("rounds", [])),
            "stages": stages, "draft": read_draft(project)}
