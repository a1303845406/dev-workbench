"""Provider abstraction layer (总体设计 6.3 / 接口设计 §5).

- OpenAI-compatible chat completions via httpx (stream supported).
- Built-in deterministic "mock" provider (base_url `mock://local`) so the whole
  product is runnable/demoable without any API key.
- API keys only read from env at request assembly time (G-03), never persisted.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

from .errors import ApiError


@dataclass
class LLMResult:
    text: str
    finish_reason: str = "stop"
    provider: str = ""
    model: str = ""
    usage: dict | None = None


@dataclass
class Message:
    role: str  # system | user
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class ProviderError(ApiError):
    def __init__(self, code: str, status: int, message: str, retryable: bool):
        super().__init__(code, status, message)
        self.retryable = retryable


def get_provider(project: str | None = None) -> BaseProvider:
    """Resolve provider by project.json → providers.json; fallback to mock."""
    from . import paths
    from .persist import load_json
    from .config import config_center

    wanted = None
    if project:
        pj = paths.project_dir(project) / "project.json"
        if pj.exists():
            wanted = load_json(pj, default={}).get("provider")
    cfg = config_center.providers() or {}
    entries = cfg.get("providers") or []
    entry = next((e for e in entries if e.get("name") == wanted), None) \
        if wanted else None
    if entry is None:
        default_name = cfg.get("default")
        entry = next((e for e in entries if e.get("name") == default_name), None)
    if entry is None:
        entry = entries[0] if entries else None
    if entry is None or str(entry.get("base_url", "")).startswith("mock://"):
        return MockProvider()
    return OpenAICompatProvider(
        name=entry.get("name", "provider"),
        base_url=entry["base_url"], model=entry.get("model", "gpt-unknown"),
        key_env=entry.get("key_env", ""), stream=bool(entry.get("stream", True)),
        timeout_s=int(entry.get("timeout_s", 120)),
        temperature=float(entry.get("temperature", 0.3)))


def resolve_outbound(provider_name: str, context_files: list[str], estimated_chars: int,
                     confirmed_at: str | None = None) -> str:
    """G-10 outbound knowledge check. Callers must have validated the request
    carries outbound_confirmation; this records it for audit."""
    from .audit import audit
    audit("outbound.confirm", target=provider_name,
          detail={"context_files": context_files, "estimated_chars": estimated_chars,
                  "confirmed_at": confirmed_at})
    return confirmed_at or "unrecorded"


def timeout_error(why: str) -> ProviderError:
    return ProviderError("PROVIDER_TIMEOUT", 502, f"Provider 超时：{why}", retryable=True)


def rate_limit_error(why: str = "") -> ProviderError:
    return ProviderError("PROVIDER_RATE_LIMIT", 429, f"Provider 限流 {why}", retryable=True)


def auth_error(why: str = "") -> ProviderError:
    return ProviderError("PROVIDER_AUTH", 401, f"Provider 鉴权失败 {why}", retryable=False)


class BaseProvider:
    name: str = ""
    model: str = ""

    async def chat(self, messages: list[Message], *, stream_cb: Callable[[str], Awaitable[None]] | None = None,
                   timeout_s: float = 120) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------- mock provider

_MOCK_DELAY_S = 0.15


class MockProvider(BaseProvider):
    """Deterministic offline provider: stage/module-aware canned outputs."""

    name = "mock-demo"
    model = "mock-1"

    async def chat(self, messages, *, stream_cb=None, timeout_s: float = 30) -> LLMResult:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        stage = _extract_marker(system, "stage:") or _extract_marker(user, "stage:")
        module = _extract_marker(system, "module:") or _extract_marker(user, "module:")
        mode = _extract_marker(system, "mock-mode:") or _extract_marker(user, "mock-mode:") or "chat"
        text = _mock_content(mode, stage, module, user)
        if stream_cb is not None:
            for i in range(0, len(text), 80):
                await stream_cb(text[i:i + 80])
                await asyncio.sleep(_MOCK_DELAY_S / 8)
        else:
            await asyncio.sleep(_MOCK_DELAY_S)
        return LLMResult(text=text, provider=self.name, model=self.model,
                         usage={"prompt_tokens": len(user) // 4, "completion_tokens": len(text) // 4})


def _extract_marker(text: str, key: str) -> str | None:
    m = re.search(re.escape(key) + r"\s*([^\n]+)", text)
    if not m:
        return None
    return m.group(1).strip().rstrip("]").strip()


def _handoff(what: str) -> str:
    return f"\n===HANDOFF===\n{what}；关键决策：按提示词结构产出；遗留风险：无。"


def _mock_content(mode: str, stage: str | None, module: str | None, user: str) -> str:
    if mode == "fr01":
        return _mock_fr01(stage or "S1")
    if mode == "compose":
        return _mock_compose(user)
    if mode == "testcase":
        return _mock_testcase(user)
    if mode == "handoff":
        return "补充要点：任务输出已定稿，摘要与正文一致。" + _handoff("摘要重生成完成")
    # ring execution (module prompt)
    return _mock_ring(module or "requirement")


def _mock_fr01(stage: str) -> str:
    reply = {
        "S1": ("## 引导（5W1H）\n为把诉求澄清到位，请先回答以下问题（可只答有把握的）：\n"
               "1. **Why**：要解决的痛点是什么？[模糊: 收益未量化]\n"
               "2. **Who**：使用者是谁、规模多大？[模糊: 用户规模]\n"
               "3. **What**：核心场景 3 个以内？\n"
               "4. **Where/When**：部署与使用时机？\n"
               "5. **How**：成功标准（可验收）是什么？[模糊: 验收口径]\n"),
        "S2": ("## 需求摘要（B/F/N/C）\n\n| 编号 | 类别 | 描述 | 状态 |\n|---|---|---|---|\n"
               "| B1 | 业务 | 提供辅助开发的工作台 | 开放 |\n| F1 | 功能 | 需求探索流水线 | 澄清中 |\n"
               "| N1 | 非功能 | localhost 单机 | 已确认 |\n| C1 | 约束 | 不直接写代码 | 已确认 |\n\n"
               "**缺口清单**：P0-验收口径未定；P1-用户规模。\n"),
        "S3": ("## 澄清追问（本轮 2 问，请选择）\n1. 使用规模：A) 单人 B) ≤10 人小队 C) 更大？\n"
               "2. 第一版是否需要权限区分：A) 不需要（推荐） B) 需要？\n"),
        "S4": ("## 三角度自审\n- **有门无路**：每个功能均有入口与产出路径 ✅\n"
               "- **迭代对称性**：修改/回滚路径已覆盖 ✅\n- **旅程断点**：断点续跑已覆盖 ✅\n\n"
               "## 摘要确认请求\n请确认以上需求摘要，确认后进入范围冻结。\n"),
        "S5": ("## 范围冻结（两段）\n1. 业务冻结：B1 满足业务诉求；2. 清单冻结：F/N/C 清单以摘要为准。\n"
               "确认后如需变更须走变更流程。\n"),
        "S6": ("## 需求规格说明书（骨架）\n1. 引言 2. 总体描述 3. 功能需求 FR-01…（含编号追溯链 B→F→FR）\n"
               "4. 非功能 NFR-01… 5. 约束 CON-01… 6. 验收标准 AC-01…\n配图：![业务流程图](assets/业务流程图.drawio)\n"),
        "S7": ("## 评审问题清单\n- **Q-S-01（严重）**：FR-04 验收口径需量化\n"
               "- **Q-G-01（一般）**：术语需统一\n- **Q-B-01（建议）**：补充示例\n"),
    }
    body = reply.get(stage, reply["S1"])
    section = {"S1": "二", "S2": "三", "S3": "四", "S4": "四", "S5": "五", "S6": "三", "S7": "五"}.get(stage, "四")
    patch = (f"\n\n```draft-patch\n[{{\"section\": \"{section}\", \"action\": \"append\", "
             f"\"content\": \"\\n### 阶段 {stage} 产出（自动摘要）\\n- 已生成阶段产出，详见阶段记录。\\n\"}}]\n```\n")
    return body + _handoff(f"FR-01 阶段 {stage} 产出完成") + patch


def _mock_compose(user: str) -> str:
    proposal = {
        "modules_selected": ["requirement", "development-backend", "development-frontend", "test", "acceptance"],
        "tasks_proposal": [
            {"title": "核心领域模型与数据层", "module": "development-backend",
             "description": "定义核心实体与持久化接口，实现数据访问层。",
             "acceptance": "实体/仓库接口可通过单元测试验证", "req_refs": ["F1"], "est_chars": 18000},
            {"title": "业务服务与接口层", "module": "development-backend",
             "description": "实现业务服务与 REST 接口，串联数据层。",
             "acceptance": "接口按契约返回，错误码符合设计", "req_refs": ["F1", "F3"], "est_chars": 22000},
            {"title": "前端页面与状态管理", "module": "development-frontend",
             "description": "实现主要视图、路由与状态管理，对接后端接口。",
             "acceptance": "主流程可走通，交互符合界面设计", "req_refs": ["F2"], "est_chars": 26000},
        ],
    }
    return json.dumps(proposal, ensure_ascii=False)


def _mock_testcase(user: str = "") -> str:
    body = ("## TC-F-001（功能）主流程创建\n- 前置条件：服务已启动\n- 步骤：创建项目→打开工作台\n"
            "- 预期结果：项目出现在列表\n- 覆盖：F1\n\n"
            "## TC-B-001（业务）提示词交付闭环\n- 前置条件：已生成提示词\n- 步骤：复制→投喂外部 IDE\n"
            "- 预期结果：提示词可直接执行\n- 覆盖：F3\n")
    if "性能用例：需要" in user:
        body += ("## TC-P-001（性能）百文件扫描耗时\n- 前置条件：目标目录含 ≥100 文件\n"
                 "- 步骤：执行扫描并计时\n- 预期结果：P95 耗时 ≤ 5s\n- 覆盖：N1\n")
    return body


def _mock_ring(module: str) -> str:
    titles = {
        "requirement": "需求条目化产出（含 EARS 条目与验收口径）",
        "design-outline": "概要设计：模块划分与关键流程",
        "prototype": "原型要点：页面与交互清单",
        "detailed-design": "详细设计：类/接口/时序要点",
        "database": "数据库设计：实体、字段与索引",
        "development-backend": "后端实现指引：目录、接口、错误码与测试要点",
        "development-frontend": "前端实现指引：视图、状态、组件拆分",
        "test": "测试指引：功能/业务用例设计要点",
        "regression": "回归范围与用例选择建议",
        "acceptance": "验收清单：演示脚本与通过标准",
    }
    body = (f"## {module} 环产出\n**产出**：{titles.get(module, module)}。\n"
            "**要求**：外部 IDE 按本提示词执行，产出符合项目工程规则与硬约束包。\n"
            "**执行说明**：请阅读任务层列出的项目内文件后开始；完成后汇报改动清单。\n")
    return body + _handoff(f"{module} 环完成：产出就绪")


# ---------------------------------------------------------- OpenAI-compatible

class OpenAICompatProvider(BaseProvider):
    def __init__(self, name: str, base_url: str, model: str, key_env: str,
                 *, stream: bool = True, timeout_s: int = 120, temperature: float = 0.3):
        self.name, self.base_url, self.model = name, base_url, model
        self.key_env, self.stream, self.timeout_s, self.temperature = key_env, stream, timeout_s, temperature

    def _headers(self) -> dict:
        import os
        key = os.environ.get(self.key_env, "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def chat(self, messages, *, stream_cb=None, timeout_s: float | None = None) -> LLMResult:
        timeout = timeout_s or self.timeout_s
        payload = {"model": self.model, "messages": [m.as_dict() for m in messages],
                   "temperature": self.temperature, "stream": bool(stream_cb and self.stream)}
        url = self.base_url.rstrip("/") + "/chat/completions"
        try:
            if payload["stream"] and stream_cb is not None:
                return await self._chat_stream(url, payload, stream_cb, timeout)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 429:
                    raise rate_limit_error()
                if resp.status_code in (401, 403):
                    raise auth_error()
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return LLMResult(text=text, provider=self.name, model=self.model, usage=data.get("usage"))
        except httpx.TimeoutException as e:
            raise timeout_error(str(e))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise rate_limit_error()
            if e.response.status_code in (401, 403):
                raise auth_error()
            raise ProviderError("PROVIDER_REMOTE_ERROR", 502, f"Provider 返回 {e.response.status_code}", True)
        except httpx.HTTPError as e:
            raise timeout_error(str(e))

    async def _chat_stream(self, url, payload, stream_cb, timeout) -> LLMResult:
        parts: list[str] = []
        usage = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as resp:
                if resp.status_code == 429:
                    raise rate_limit_error()
                if resp.status_code in (401, 403):
                    raise auth_error()
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        parts.append(delta)
                        await stream_cb(delta)
        return LLMResult(text="".join(parts), provider=self.name, model=self.model, usage=usage)
