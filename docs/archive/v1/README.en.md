# Dev Workbench · 开发工作台

> A localhost-deployed, filesystem-as-database **AI-assisted development web workbench**. It doesn't write code — it produces **well-formed documents + prompts ready to feed external IDE agents** (Trae / Codex CLI / Cursor, etc.).

English | [中文](README.md)

---

## What is it

Dev Workbench splits the whole development lifecycle (requirements → design → development → testing → acceptance) into **10 dynamically composable modules**. It picks a module combination automatically based on task complexity, splits work into task units bounded by the external IDE's context window, and generates ring-by-ring prompts carrying complete context packets — with a sanitize gate on every copy/export path. All data is persisted as plain Markdown/JSON files: Git-friendly, zero database.

**Positioning**: this tool is the "brain"; the external IDE is the "hands and feet".

## Feature overview (per SRS V1.2.2)

| Module | Capability | Traceability |
|---|---|---|
| 🧭 Requirements Exploration (FR-01) | 7-stage pipeline: guided dialogue → fragment extraction (B/F/N/C) → clarification → summary confirmation → scope freeze → document writing → review & revision; per-stage confirm gates + 6-section rolling draft on disk + resumable | AC-01/06/15 |
| 🗂 Project Organization (FR-03) | Read-only scan of local directories: path whitelist, depth/entry limits, language stats, directory health, graded suggestions (auto-sort / needs-human-decision) | AC-03 |
| ⚙️ Prompt Engineering (FR-04) | 6-step workflow: pick context → declare complexity → module combination (hard constraint: requirement → any dev → test → acceptance) → budget-bounded task splitting → orchestration plan (topo order + parallel groups) → ring execution (4-layer input packet / budget gate / handoff notes / suspend & resume) | AC-04/14/16 |
| 🧪 Test Cases (FR-05) | Functional & business cases mandatory; performance cases opt-in; coverage refs annotated | AC-05 |
| 📋 Task Board (G5) | Pending-fed / fed / done / failed kanban; failure requires a note | — |
| 📚 Asset Library (FR-07/08) | Prompt version chain, two-version diff, rollback, execution feedback; Spec Kit / Taskmaster / OpenSpec / plain Markdown adaptive export | AC-07/13 |
| 🔧 Layered Coding Rules (2.9) | Universal hard-constraint package (cannot be relaxed) + project rules.md, keyword-level conflict warnings on save | AC-17 |
| 🔐 Security | Outbound knowledge confirmation (G-10), sanitize scanning (keys/passwords/intranet addresses), provider keys via env vars only, forced 127.0.0.1 binding | NFR-03/04/12 |
| 💾 Persistence & Backup (FR-06/NFR-08) | Atomic writes (tmp + replace), state index, rolling .bak copies, daily snapshots with key pre-scan | AC-06/09 |

## Tech stack

- **Backend**: Python 3.10+ · FastAPI · Uvicorn · httpx (OpenAI-compatible provider abstraction with a built-in offline mock provider — full experience with zero API keys)
- **Frontend**: Vue 3 · TypeScript · Vite · Element Plus · Pinia · markdown-it + DOMPurify
- **Storage**: pure filesystem (Markdown / JSON / .drawio), LF line endings, schemaVersion validation
- **Push**: global SSE event stream (ring progress, config hot-reload, backup done, …)

## Quick start

### Option 1: one-click (Windows)

```bat
启动工作台.bat
```

### Option 2: manual

```bash
# 1. Backend
python -m venv .venv
.venv/Scripts/pip install fastapi uvicorn httpx pydantic python-multipart
.venv/Scripts/python -m uvicorn server.app:app --host 127.0.0.1 --port 8642

# 2. Frontend (optional; dist/ is already bundled)
cd frontend && npm install && npm run build

# 3. Open http://127.0.0.1:8642/
```

> The default provider is `mock-demo` (offline demo): the entire flow runs without any API key. To use a real model, edit `configs/providers.json` to point `base_url` at any OpenAI-compatible service and set the matching env var (e.g. `DEVWB_PROVIDER__OFFICIAL__API_KEY`). **Keys are read from env vars only and never persisted.**

### Run tests

```bash
.venv/Scripts/python -m pytest tests -W ignore
```

## Repository layout

```
devworkbench/
├─ server/                  # FastAPI server
│  ├─ app.py                # app assembly (forced 127.0.0.1 binding)
│  ├─ api/                  # REST routers
│  └─ core/                 # domain services
│     ├─ pipeline.py        #   FR-01 7-stage state machine + rolling draft
│     ├─ orchestrator.py    #   FR-04 orchestrator (DAG / input packet / budget / handoff)
│     ├─ scanner.py         #   FR-03 read-only scanner
│     ├─ persist.py         #   atomic writes / .bak / front matter / light locks
│     ├─ provider.py        #   provider abstraction (OpenAI-compatible + mock)
│     ├─ sanitize.py        #   sanitize rule engine
│     ├─ constraints.py     #   constraint package injection & conflict check
│     ├─ exporters.py       #   FR-08 adaptive exporters
│     └─ backup.py          #   daily snapshot scheduler
├─ frontend/                # Vue 3 SPA (dist/ served by the backend)
├─ configs/                 # product-level config (hot-reloaded within 5s)
├─ workspace/               # project data root (redirect via DEVWB_HOME)
├─ docs/                    # SRS / design docs / interactive prototype / drawio diagrams
└─ tests/                   # pytest smoke suite (FR-01/03/04/05/08 main paths)
```

## Per-project data layout

```
workspace/projects/<project>/
├─ project.json / rules.md / tools.json
├─ docs/需求/          rolling draft · stage records S1~S7
├─ analysis/           structure profile · suggestions · scan summary
├─ pipeline/           state index · tasks.json · orchestration/<ring>/ (input/output/handoff/meta)
├─ prompts/            <module>/<task>/v<N>.md (front-matter version chain)
├─ testcases/          <task>/cases.md
└─ exports/            spec-kit/ · taskmaster/ · … (with meta.json provenance)
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEVWB_HOME` | `./workspace` | project data root |
| `DEVWB_CONFIG_DIR` | `./configs` | product config dir |
| `DEVWB_BACKUP_DIR` | `./backups` | snapshot dir |
| `DEVWB_PORT` | `8642` | port for `python -m server.app` |
| `DEVWB_PROVIDER__<NAME>__API_KEY` | — | provider key (env-only, G-03) |

## Security notes (please read)

1. **Local machine only**: the server binds `127.0.0.1` with no authentication. Before any LAN/public exposure, implement access control and HTTPS first (SRS FU-02/FU-05 prerequisites).
2. **Outbound knowledge**: every model call requires an explicit confirmation dialog listing provider + document list + estimated chars.
3. **Sanitize gate**: copy/export paths pass regex rules (block-level hard-stops; confirm-level per-item adjudication) — prefer blocking over leaking.
4. **Backups never contain keys**: keys live outside the data tree; snapshots run a key scan anyway.

## Requirements & design documents

Full traceability in [`docs/`](docs/) (Chinese):

- Requirements: `docs/需求/开发工作台需求规格说明书-V1.2.md` (V1.2.2 final)
- Design: `docs/设计/01~07` (architecture / data / API / core mechanisms / POC / UI / review)
- Prototype: `docs/原型/开发工作台-交互原型-V1.0.html` (single-file, offline)
- Diagrams: `docs/assets/*.drawio`

## Roadmap

- [x] **V1** (this repo): full localhost single-machine loop
- [ ] V1.x: batch-0 POC (AOCI-CODE / tree-sitter structure profile, budget coefficient calibration), read-only drawio preview
- [ ] V2: LAN sharing (HTTPS + auth gate), code-slice injection, deep Git integration, token accounting

## License

[MIT](LICENSE)
