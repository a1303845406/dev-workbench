# Dev Workbench v2

> 本地 B/S 开发指挥中枢：管理项目、版本化产物、任务卡、执行回执、CI 证据与人工验收。外部 Agent 是执行者，最终决策权保留给人。

## 第一阶段已实现

第一阶段验证一个真实的最小开发控制闭环：

`Project → 六个项目级 ArtifactVersion → Module/Feature/TaskCard → PromptInstance → ExecutionRun → 本地 MCP ExecutionReceipt → changed_files 工作区扫描 → 独立 TestRun/CI → WorkbenchConclusion → AcceptanceConclusion`

已覆盖：

- SQLite WAL 事务库：关系、幂等、事件和重启恢复；文件系统保存 CI 日志等证据。
- 不可变产物版本链：新版本通过 `based_on_version_id` 关联，不覆盖历史。
- TaskCard 计划状态和 ExecutionRun 执行状态分离；需求变更可将旧卡标记 `superseded`。
- AI 一审失败时支持带理由人工强制批准。
- 本地 MCP 回执接口：`POST /api/v2/mcp/receipts`，每次执行令牌隔离，重复 `report_id` 幂等。
- changed_files 由工作台扫描验证，不接受 Agent 自报作为唯一事实；支持 Git 工作区和非 Git 工作区。
- 项目 workspace 绑定唯一，拒绝两个项目共享同一个工作目录。
- 独立 TestRun/CI runner：命令 allowlist、超时、禁止 shell 运算符和路径逃逸。
- Workbench 总结、人工普通验收和带风险验收，所有关键动作写入事件流。
- superseded 任务不能重新审核、批准或生成提示词；重复 `report_id` 只有 payload 完全一致时才视为幂等，否则拒绝冲突。
- WorkbenchConclusion 仅接受 `passed`、`failed`、`rework_required`；CI 可执行入口必须是无路径的 allowlist 命令名。
- 最小项目驾驶舱：项目列表、六节点产物、任务与执行状态、事件历史。
- 仓库登记与状态：按项目登记本地仓库，展示 Git/非 Git、分支、提交和工作区变更；MCP token 只在执行启动后的当前页面会话展示，不写入执行查询结果。
- 空工作区基线是有效事实；扫描不会把父级 Git 仓库的无关变更混入项目回执。

第一阶段**不宣称**自动驱动 Codex/DSH/Cursor，也不伪造最终人工验收；当前人工验收需要由产品负责人在驾驶舱或 API 中完成。

## 技术栈

- 后端：Python 3.10+、FastAPI、Pydantic、Uvicorn、httpx
- 前端：Vue 3、TypeScript、Vite
- 存储：SQLite（控制关系）+ 文件系统（项目工作区和证据）
- 运行方式：单个 FastAPI 进程同时提供 REST、内置本地 MCP 接口和构建后的前端

## 快速开始

Windows 下推荐直接双击或运行仓库根目录的 `start-devworkbench.bat`：脚本会设置 `DEVWB_HOME`、检测前端构建产物、后台轮询 `/api/health`，服务就绪后自动用默认浏览器打开驾驶舱。

### 后端

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
$env:DEVWB_HOME = (Join-Path (Get-Location) "workspace")
python -m uvicorn server.app:app --host 127.0.0.1 --port 8642
```

打开 <http://127.0.0.1:8642/>；API 文档位于 <http://127.0.0.1:8642/api/docs>。

### 前端开发模式

```powershell
cd frontend
npm install --cache .npm-cache
npm run dev
```

Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8642`。生产模式先执行 `npm run build`，后端会从 `frontend/dist` 提供静态驾驶舱。

### Provider

默认使用 deterministic provider，不需要密钥即可运行控制链路；任务审核接口不传 `status` 时会调用内置 provider（deterministic 模式按任务描述/验收字段审核，real 模式调用 OpenAI 兼容 `/chat/completions`；也可启用本机 Codex CLI 适配器。接入 OpenAI 兼容服务时设置：

- `DEVWB_PROVIDER_BASE_URL`
- `DEVWB_PROVIDER_API_KEY`
- `DEVWB_PROVIDER_MODEL`

启用本机 Codex（本次已用 glm-5.3-flash 验证）时设置 `DEVWB_CODEX_ENABLED=true`、`DEVWB_PROVIDER_MODEL=glm-5.3-flash`、`DEVWB_CODEX_CONTEXT_WINDOW=500000`。适配器调用 `codex exec --ephemeral --json`，不会读取或写入项目密钥；需要 Codex CLI 可执行文件在 PATH 中。

密钥只从环境变量读取，不写入项目文件。

任务与执行面板中，点击任务行会打开自定义详情抽屉，展示验收标准、每次 ExecutionRun、MCP 回执、TestRun/CI 和 Workbench/Acceptance 结论；v2 页面不依赖浏览器原生 prompt/alert/confirm。

## 测试与验证

当前自动化回归结果：`22 passed`（deterministic 与 Codex GLM provider 均已回归），但这不等同于产品负责人完成的人工验收；人工验收仍需按下方 HTTP 序列执行。真实页面验收还覆盖仓库登记控制台、项目控制室自定义任务详情抽屉、失败返工与执行历史。

```powershell
python -m pytest tests -q
python -m py_compile server\app.py server\api\v2.py server\core\ci.py server\core\db.py server\core\errors.py server\core\provider.py server\core\service.py server\core\workspace.py
cd frontend
npm run typecheck
npm run build
```

正式端到端测试在 `tests/test_phase1_api.py`，覆盖：真实文件变更验证、非 Git 已存在文件修改、AI 审核强批、MCP 幂等与同 `report_id` 改 payload 冲突、过期执行拒绝、superseded 任务状态保护、artifact_refs 项目隔离、MCP 回执非空字段与 supersede 原因契约、CI 命令安全、workspace 嵌套隔离和项目隔离。测试还会确认未产生 WorkbenchConclusion 或机器检查失败时不能直接完成人工验收，并验证 PromptInstance 保存审核提供方、审核状态和任务上下文。

可用 PowerShell/HTTP 做一次手工验收：先按“快速开始”启动服务，创建一个项目/模块/特性/任务；对任务执行审核、批准、生成提示词和执行；在项目返回的 `workspace_path` 中实际创建或修改文件，再用执行返回的 `mcp_token` 调用 `POST /api/v2/mcp/receipts`；随后运行 `POST /api/v2/projects/{project_id}/tests`、`POST /api/v2/executions/{execution_id}/conclusion`，最后由产品负责人调用 `POST /api/v2/executions/{execution_id}/acceptance`。验收时还应人为制造一次失败总结，并确认再次执行产生新的 ExecutionRun、旧执行保留为 `rework_required`。

## 目录结构

```text
devworkbench/
├─ server/
│  ├─ app.py                 # FastAPI 装配、内置 MCP、静态前端
│  ├─ api/v2.py              # v2 控制室 REST API
│  └─ core/
│     ├─ db.py               # SQLite schema、事务与 JSON 辅助
│     ├─ service.py          # 项目、产物、任务、执行、回执、结论
│     ├─ workspace.py        # changed_files 工作区验证
│     ├─ ci.py               # allowlist CI/TestRun runner
│     └─ provider.py         # deterministic/OpenAI-compatible provider
├─ frontend/                 # v2 Vue 3 驾驶舱
├─ tests/                    # 正式后端集成测试
├─ docs/                     # 历史原型与设计资产
├─ legacy/v1/                # v1 归档实现，只读参考
└─ workspace/                # 本地运行数据（不提交）
```

## 规划与边界

- [v2 重建讨论定稿](../v2-重建规划/20260928-新定位与重建决策-讨论定稿.md)
- [迁移清单与自举模拟](../v2-重建规划/20260928-迁移清单与自举模拟方案.md)
- v1 归档标签：`v1.2.2-archive`

下一步由产品负责人进行人工验收：用一个真实小特性在驾驶舱/API 走完执行、回执、CI、总结和验收，并确认失败打回后的返工记录是否符合预期。