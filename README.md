# 开发工作台 · Dev Workbench

> 一个部署在 localhost、文件系统即数据库的 **AI 辅助开发 Web 工作台**。它不写代码——它产出**规范文档 + 可直接投喂外部 IDE（Trae / Codex CLI / Cursor 等）的提示词**。

[English](README.en.md) | 中文

---

## 这是什么

开发工作台把「需求 → 设计 → 开发 → 测试 → 验收」全流程拆成 **10 个可动态组合的模块**，依据任务复杂度自动选择组合、按外部 IDE 上下文窗口上限拆分任务粒度，逐环生成带完整上下文包的提示词，并对复制/导出链路做脱敏门禁。全部数据以 Markdown / JSON 文本落盘，Git 友好，无数据库依赖。

**核心定位**：本工具是"大脑"，外部 IDE 是"手脚"。

## 功能一览（对标 SRS V1.2.2）

| 模块 | 能力 | 需求追溯 |
|---|---|---|
| 🧭 需求探索（FR-01） | 七阶段流水线：引导对话 → 碎片提炼(B/F/N/C) → 澄清追问 → 摘要确认 → 范围冻结 → 文档编写 → 评审修订；每阶段确认门 + 滚动底稿六区落盘 + 断点续跑 | AC-01/06/15 |
| 🗂 项目规整（FR-03） | 同机目录只读扫描：路径白名单、深度/条数上限、语言构成统计、目录健康度、分级规整建议（自动归位/需人工决策） | AC-03 |
| ⚙️ 提示词工程（FR-04） | 六步工作流：勾选上下文 → 复杂度声明(简单/中等/复杂) → 模块组合(硬约束：需求→任一开发→测试→验收) → 按预算任务拆分 → 编排计划(拓扑序+并行组) → 环执行(四层输入包/预算闸门/交接摘要/失败挂起恢复) | AC-04/14/16 |
| 🧪 测试用例（FR-05） | 功能/业务用例必出，性能用例可选开关；覆盖编号标注 | AC-05 |
| 📋 任务看板（G5） | 待投喂/已投喂/已完成/失败四列流转，失败必填备注 | — |
| 📚 资产库（FR-07/08） | 提示词版本链、两版对比、回滚、执行反馈；Spec Kit / Taskmaster / OpenSpec / 通用 Markdown 适配导出 | AC-07/13 |
| 🔧 分层编码规范（2.9） | 通用硬约束包（不可放宽）+ 项目 rules.md，保存时关键词级冲突告警 | AC-17 |
| 🔐 安全机制 | 外发知情确认（G-10）、脱敏扫描（密钥/密码/内网地址）、Provider Key 仅走环境变量、强制绑定 127.0.0.1 | NFR-03/04/12 |
| 💾 持久化与备份（FR-06/NFR-08） | 原子写（临时文件+replace）、状态索引、.bak 滚动副本、每日快照 + Key 预扫描 | AC-06/09 |

## 技术栈

- **后端**：Python 3.10+ · FastAPI · Uvicorn · httpx（OpenAI 兼容 Provider 抽象，内置离线 mock Provider，无需 API Key 即可完整体验）
- **前端**：Vue 3 · TypeScript · Vite · Element Plus · Pinia · markdown-it + DOMPurify
- **存储**：纯文件系统（Markdown / JSON / .drawio），LF 统一换行，schemaVersion 校验
- **推送**：SSE 全局事件流（环执行进度、配置热加载、备份完成等）

## 快速开始

### 方式一：一键启动（Windows）

```bat
启动工作台.bat
```

脚本自动创建 venv、安装依赖、（如需要）构建前端并启动服务。

### 方式二：手动

```bash
# 1. 后端
python -m venv .venv
.venv\Scripts\pip install -e .          # 或 pip install fastapi uvicorn httpx pydantic python-multipart
.venv\Scripts\python -m uvicorn server.app:app --host 127.0.0.1 --port 8642

# 2. 前端（开发模式，可选；仓库已含构建产物时可跳过）
cd frontend && npm install && npm run build

# 3. 打开浏览器
#    http://127.0.0.1:8642/
```

> 内置默认 Provider 为 `mock-demo`（离线演示）：无需任何 API Key 即可跑通全流程。接真实模型时编辑 `configs/providers.json`，将 `base_url` 指向任意 OpenAI 兼容服务，并设置对应环境变量（如 `DEVWB_PROVIDER__OFFICIAL__API_KEY`）。**Key 只从环境变量读取，绝不落盘。**

### 运行测试

```bash
.venv\Scripts\python -m pytest tests -W ignore
```

## 目录结构

```
devworkbench/
├─ server/                  # FastAPI 服务端
│  ├─ app.py                # 应用装配（127.0.0.1 强制绑定）
│  ├─ api/                  # REST 路由（projects/pipelines/engineering/system…）
│  └─ core/                 # 领域服务
│     ├─ pipeline.py        #   FR-01 七阶段状态机 + 滚动底稿
│     ├─ orchestrator.py    #   FR-04 编排器（DAG/输入包/预算/交接摘要）
│     ├─ scanner.py         #   FR-03 只读扫描
│     ├─ persist.py         #   原子写/.bak/前置 front matter/轻量锁
│     ├─ provider.py        #   Provider 抽象（OpenAI 兼容 + mock）
│     ├─ sanitize.py        #   脱敏规则引擎
│     ├─ constraints.py     #   硬约束包注入与冲突检测
│     ├─ exporters.py       #   FR-08 适配导出
│     └─ backup.py          #   每日快照调度
├─ frontend/                # Vue 3 SPA（构建产物 dist/ 随服务端分发）
├─ configs/                 # 产品级配置（热加载：改动 5s 内生效）
│  ├─ providers.json        #   Provider 注册（脱敏）
│  ├─ complexity-map.json   #   复杂度→模块组合映射
│  ├─ constraints-package.md#   通用硬约束包（版本化）
│  ├─ sanitize-rules.json   #   脱敏正则
│  ├─ templates/            #   目录模板
│  └─ modules/              #   10 模块 + FR-01 七阶段角色提示词
├─ workspace/               # 项目数据根（可经 DEVWB_HOME 重定向）
├─ docs/                    # 需求规格说明书 / 设计文档 / 交互原型 / drawio 图
└─ tests/                   # pytest 冒烟（覆盖 FR-01/03/04/05/08 主链路）
```

## 项目数据布局（每个项目）

```
workspace/projects/<项目名>/
├─ project.json / rules.md / tools.json
├─ docs/需求/          滚动底稿.md · 阶段记录/S1~S7-*.md
├─ analysis/           结构画像.md · 规整建议.md · 扫描摘要.json
├─ pipeline/           state/index.json · tasks.json · orchestration/<环>/（input/output/handoff/meta 全量落盘）
├─ prompts/            <模块>/<任务>/v<N>.md（front matter 版本链）
├─ testcases/          <任务>/用例集.md
└─ exports/            spec-kit/ · taskmaster/ · …（含 meta.json 溯源）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEVWB_HOME` | `./workspace` | 项目数据根 |
| `DEVWB_CONFIG_DIR` | `./configs` | 产品级配置目录 |
| `DEVWB_BACKUP_DIR` | `./backups` | 备份快照目录 |
| `DEVWB_PORT` | `8642` | `python -m server.app` 启动端口 |
| `DEVWB_PROVIDER__<NAME>__API_KEY` | — | 各 Provider 的 Key（仅环境变量，G-03） |

## 安全须知（务必阅读）

1. **仅限本机使用**：服务强制绑定 `127.0.0.1`，无鉴权。局域网/公网暴露前必须先实施门禁鉴权与 HTTPS（SRS FU-02/FU-05 前置）。
2. **外发知情**：任何模型调用前界面会强制弹出"Provider + 文档清单 + 估算字符量"确认。
3. **脱敏门禁**：复制/导出强制过正则规则（block 级阻断，confirm 级逐条裁决），宁可阻断不放行。
4. **备份不含 Key**：Key 不在数据树内，快照前还会再做一次 Key 扫描。

## 设计与需求文档

完整追溯链见 [`docs/`](docs/)：

- 需求：`docs/需求/开发工作台需求规格说明书-V1.2.md`（V1.2.2 定稿）
- 设计：`docs/设计/01~07`（总体/数据/接口/核心机制/POC/界面/复核）
- 原型：`docs/原型/开发工作台-交互原型-V1.0.html`（单文件可离线打开）
- 图示：`docs/assets/*.drawio`（CON-12 标准产物格式）

## 路线图

- [x] **V1**（本仓库）：localhost 单机全功能闭环
- [ ] V1.x：批次 0 POC（AOCI-CODE / tree-sitter 结构画像、预算系数校准）、drawio 只读预览
- [ ] V2：局域网共享（HTTPS + 账号门禁）、代码切片注入、Git 深度集成、Token 统计

## License

[MIT](LICENSE)
