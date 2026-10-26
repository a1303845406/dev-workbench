<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { get, post } from './api'

type RepositoryStatus = { exists: boolean; is_git: boolean; branch: string; commit_sha: string; changed_files: string[]; state: string }
type Repository = { id: string; name: string; path: string; branch: string; commit_sha: string; status: RepositoryStatus }
type Project = { id: string; name: string; status: string; workspace_path: string; task_counts: Record<string, number>; artifact_count: number; open_decisions: number }
type Module = { id: string; name: string; description: string; parent_id?: string | null }
type Feature = { id: string; module_id: string; name: string; description: string; status: string }
type Execution = { id: string; status: string; started_at?: string; ended_at?: string; mcp_token?: string; receipts?: Array<{ report_id: string; summary: string; submitted_at: string; workspace_verification_json?: string }>; tests?: Array<{ id: string; status: string; command: string; exit_code?: number }>; conclusions?: Array<{ kind: string; status: string; summary: string; actor: string; created_at: string }> }
type Task = { id: string; feature_id: string; title: string; description: string; acceptance: string; plan_status: string; ai_review_status: string; executions: Execution[] }
type ProjectDetail = Project & { repositories: Repository[]; artifacts: Array<{ kind: string; title: string; version: string; version_status: string }>; modules: Module[]; features: Feature[]; tasks: Task[]; events: Array<{ event_type: string; entity_type: string; created_at: string }> }

const projects = ref<Project[]>([])
const selectedId = ref('')
const detail = ref<ProjectDetail | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const mcpTokens = ref<Record<string, string>>({})
const selectedTask = ref<Task | null>(null)
const projectName = ref('')
const moduleName = ref('')
const moduleDescription = ref('')
const featureModuleId = ref('')
const featureName = ref('')
const featureDescription = ref('')
const taskFeatureId = ref('')
const taskTitle = ref('')
const taskDescription = ref('')
const taskAcceptance = ref('')
const repositoryName = ref('')
const repositoryPath = ref('')
const selected = computed(() => detail.value)
const activeModuleId = computed(() => featureModuleId.value || detail.value?.modules[0]?.id || '')
const activeFeatureId = computed(() => taskFeatureId.value || detail.value?.features[0]?.id || '')

function clearFeedback() { error.value = ''; notice.value = '' }
async function loadProjects() {
  projects.value = (await get<{ projects: Project[] }>('/api/v2/projects')).projects
  if (!selectedId.value && projects.value[0]) selectedId.value = projects.value[0].id
  if (selectedId.value) await loadDetail()
}
async function loadDetail() {
  if (!selectedId.value) return
  detail.value = await get<ProjectDetail>('/api/v2/projects/' + selectedId.value)
  if (!featureModuleId.value && detail.value.modules[0]) featureModuleId.value = detail.value.modules[0].id
  if (!taskFeatureId.value && detail.value.features[0]) taskFeatureId.value = detail.value.features[0].id
}
async function runAction(action: () => Promise<void>, success: string) {
  busy.value = true
  clearFeedback()
  try { await action(); notice.value = success; await loadProjects() }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { busy.value = false }
}
async function createProject() {
  const name = projectName.value.trim()
  if (!name) return
  await runAction(async () => {
    const created = await post<ProjectDetail>('/api/v2/projects', { name, requirement: '仓库管理控制台：登记仓库并展示分支与提交状态' })
    projectName.value = ''
    selectedId.value = created.id
    detail.value = created
  }, '项目已创建')
}
async function registerRepository() {
  if (!selectedId.value || !repositoryPath.value.trim()) return
  await runAction(async () => {
    await post('/api/v2/projects/' + selectedId.value + '/repositories', { name: repositoryName.value.trim() || '主仓库', path: repositoryPath.value.trim() })
    repositoryName.value = ''; repositoryPath.value = ''
  }, '仓库已登记')
}
async function createModule() {
  if (!selectedId.value || !moduleName.value.trim()) return
  await runAction(async () => {
    await post('/api/v2/projects/' + selectedId.value + '/modules', { name: moduleName.value.trim(), description: moduleDescription.value.trim() })
    moduleName.value = ''; moduleDescription.value = ''; featureModuleId.value = ''
  }, '模块已创建')
}
async function createFeature() {
  if (!selectedId.value || !activeModuleId.value || !featureName.value.trim()) return
  await runAction(async () => {
    await post('/api/v2/projects/' + selectedId.value + '/features', { module_id: activeModuleId.value, name: featureName.value.trim(), description: featureDescription.value.trim() })
    featureName.value = ''; featureDescription.value = ''; taskFeatureId.value = ''
  }, '特性已创建')
}
async function createTask() {
  if (!selectedId.value || !activeFeatureId.value || !taskTitle.value.trim()) return
  await runAction(async () => {
    await post('/api/v2/projects/' + selectedId.value + '/tasks', { feature_id: activeFeatureId.value, title: taskTitle.value.trim(), description: taskDescription.value.trim(), acceptance: taskAcceptance.value.trim() })
    taskTitle.value = ''; taskDescription.value = ''; taskAcceptance.value = ''
  }, '任务卡已创建')
}
async function reviewAndApprove(task: Task) {
  await runAction(async () => {
    const review = await post<{ ai_review_status: string }>('/api/v2/tasks/' + task.id + '/review', {})
    if (review.ai_review_status !== 'passed') throw new Error('AI 一审未通过：请查看任务详情后人工强制批准')
    await post('/api/v2/tasks/' + task.id + '/approve')
  }, '任务卡已通过一审')
}
async function prepare(task: Task) {
  await runAction(async () => {
    await post('/api/v2/tasks/' + task.id + '/prompt')
    const started = await post<{ execution: Execution; mcp_token: string }>('/api/v2/tasks/' + task.id + '/execute')
    mcpTokens.value = { ...mcpTokens.value, [started.execution.id]: started.mcp_token }
    notice.value = '执行已启动；MCP token 仅在本次页面会话显示，请立即复制'
  }, '执行已启动，请使用 MCP 回执')
}
function latestExecution(task: Task) { return task.executions[0]?.status || 'not_started' }
function openTask(task: Task) { selectedTask.value = task }
function closeTask() { selectedTask.value = null }
async function copyToken(token: string) {
  try { await navigator.clipboard.writeText(token); notice.value = 'MCP token 已复制到剪贴板' }
  catch { error.value = '浏览器未允许复制，请手动选择 token' }
}
onMounted(() => { loadProjects().catch(e => { error.value = String(e) }) })
</script>

<template>
  <div class="shell">
    <header>
      <div><span class="mark">⌘</span><strong>开发工作台</strong><small> Dev Workbench · v2</small></div>
      <div class="header-actions"><span class="status-dot">●</span>本地控制室 <a class="docs-link" href="/api/docs" target="_blank">API 文档</a><form class="project-create" @submit.prevent="createProject"><input v-model="projectName" aria-label="项目名称" placeholder="输入项目名称" required :disabled="busy"><button type="submit" :disabled="busy">+ 新建项目</button></form></div>
    </header>
    <main>
      <aside>
        <div class="aside-title">项目 <span>{{ projects.length }}</span></div>
        <button v-for="project in projects" :key="project.id" class="project" :class="{ active: selectedId === project.id }" @click="selectedId = project.id; loadDetail()">
          <strong>{{ project.name }}</strong><small>{{ project.status }} · 产物 {{ project.artifact_count }}</small>
        </button>
        <p v-if="!projects.length" class="empty">还没有项目</p>
      </aside>
      <section class="content">
        <div v-if="error" class="error">{{ error }}</div>
        <div v-if="notice" class="notice">{{ notice }}</div>
        <template v-if="selected">
          <div class="hero"><div><p class="eyebrow">PROJECT CONTROL ROOM</p><h1>{{ selected.name }}</h1><p>一个项目内的需求、产物、任务、执行与验收证据。</p></div><button class="secondary" @click="loadDetail">↻ 刷新状态</button></div>
          <div class="stats"><div><span>产物</span><b>{{ selected.artifact_count }}</b></div><div><span>模块</span><b>{{ selected.modules.length }}</b></div><div><span>任务卡</span><b>{{ selected.tasks.length }}</b></div><div><span>决策待办</span><b>{{ selected.open_decisions }}</b></div></div>
          <article class="panel repository-panel"><div class="panel-heading"><div><h2>仓库登记与状态</h2><p class="panel-help">项目内登记仓库后，工作台读取分支、提交与工作区变化。</p></div><span>{{ selected.repositories.length }} 个仓库</span></div><div class="repository-list"><div v-for="repository in selected.repositories" :key="repository.id" class="repository-row"><div><strong>{{ repository.name }}</strong><small>{{ repository.path }}</small></div><div class="repository-state"><b>{{ repository.status.state }}</b><span>{{ repository.status.branch || "无分支" }} · {{ repository.status.commit_sha ? repository.status.commit_sha.slice(0, 8) : "无提交" }}</span><em v-if="repository.status.changed_files.length">变更 {{ repository.status.changed_files.length }} 个</em></div></div></div><form class="repository-form" @submit.prevent="registerRepository"><input v-model="repositoryName" placeholder="仓库名称" :disabled="busy"><input v-model="repositoryPath" placeholder="本地仓库路径" required :disabled="busy"><button :disabled="busy || !repositoryPath.trim()">登记仓库</button></form></article>
          <div class="grid">
            <article class="panel"><div class="panel-heading"><h2>六个项目节点</h2><span>ArtifactVersion</span></div><div class="artifacts"><div v-for="artifact in selected.artifacts" :key="artifact.kind" class="artifact"><span class="artifact-icon">◆</span><div><strong>{{ artifact.title }}</strong><small>{{ artifact.version }} · {{ artifact.version_status }}</small></div></div></div></article>
            <article class="panel"><div class="panel-heading"><h2>最近事件</h2><span>{{ selected.events.length }}</span></div><div class="events"><div v-for="event in selected.events.slice(0, 8)" :key="event.created_at + event.entity_type + event.event_type" class="event"><span class="event-dot">●</span><div><strong>{{ event.event_type }}</strong><small>{{ event.entity_type }} · {{ event.created_at }}</small></div></div></div></article>
          </div>
          <article class="panel builder-panel">
            <div class="panel-heading"><div><h2>主线建卡</h2><p class="panel-help">直接建立 Module → Feature → TaskCard，不必先切换到 API 页面。</p></div><span>Phase 1</span></div>
            <div class="builder-grid">
              <form class="builder-form" @submit.prevent="createModule"><h3>1 · 模块</h3><input v-model="moduleName" placeholder="模块名称" required :disabled="busy"><textarea v-model="moduleDescription" placeholder="模块职责（可选）" rows="2" :disabled="busy"></textarea><button :disabled="busy || !moduleName.trim()">创建模块</button></form>
              <form class="builder-form" @submit.prevent="createFeature"><h3>2 · 特性</h3><select v-model="featureModuleId" required :disabled="busy || !selected.modules.length"><option value="" disabled>选择所属模块</option><option v-for="module in selected.modules" :key="module.id" :value="module.id">{{ module.name }}</option></select><input v-model="featureName" placeholder="特性名称" required :disabled="busy"><textarea v-model="featureDescription" placeholder="特性描述（可选）" rows="2" :disabled="busy"></textarea><button :disabled="busy || !activeModuleId || !featureName.trim()">创建特性</button></form>
              <form class="builder-form" @submit.prevent="createTask"><h3>3 · 任务卡</h3><select v-model="taskFeatureId" required :disabled="busy || !selected.features.length"><option value="" disabled>选择所属特性</option><option v-for="feature in selected.features" :key="feature.id" :value="feature.id">{{ feature.name }}</option></select><input v-model="taskTitle" placeholder="任务标题" required :disabled="busy"><textarea v-model="taskDescription" placeholder="任务描述" rows="2" :disabled="busy"></textarea><textarea v-model="taskAcceptance" placeholder="验收标准" rows="2" :disabled="busy"></textarea><button :disabled="busy || !activeFeatureId || !taskTitle.trim()">创建任务卡</button></form>
            </div>
          </article>
          <article class="panel task-panel"><div class="panel-heading"><h2>任务与执行</h2><span>{{ selected.tasks.length }} 张任务卡</span></div><div v-if="!selected.tasks.length" class="empty-panel">先在上方创建一张任务卡。</div><div v-for="task in selected.tasks" :key="task.id" class="task-row" @click="openTask(task)"><div class="task-main"><strong>{{ task.title }}</strong><small>{{ task.plan_status }} · 一审 {{ task.ai_review_status }} · 执行 {{ latestExecution(task) }}</small><p v-if="task.description">{{ task.description }}</p></div><div class="task-actions"><button v-if="task.plan_status !== 'approved' && task.plan_status !== 'superseded'" class="secondary" @click.stop="reviewAndApprove(task)" :disabled="busy">一审并批准</button><button v-if="task.plan_status === 'approved' && !['executing','machine_verifying','awaiting_acceptance'].includes(latestExecution(task))" @click.stop="prepare(task)" :disabled="busy">生成提示词并执行</button><div v-if="task.executions[0] && mcpTokens[task.executions[0].id]" class="token-box"><span>MCP token（仅本页）</span><code>{{ mcpTokens[task.executions[0].id] }}</code><button class="token-copy" @click.stop="copyToken(mcpTokens[task.executions[0].id])">复制</button></div><span class="details-hint">查看详情 →</span><a class="docs-link" href="/api/docs" target="_blank">回执 / CI</a></div></div></article>
           <aside v-if="selectedTask" class="drawer-backdrop" @click.self="closeTask"><section class="task-drawer"><div class="drawer-heading"><div><p class="eyebrow">TASK DETAIL</p><h2>{{ selectedTask.title }}</h2></div><button class="secondary" @click="closeTask">关闭</button></div><p>{{ selectedTask.description }}</p><h3>验收标准</h3><p>{{ selectedTask.acceptance || '未填写' }}</p><h3>当前状态</h3><p class="drawer-status">{{ selectedTask.plan_status }} · 一审 {{ selectedTask.ai_review_status }} · 执行 {{ latestExecution(selectedTask) }}</p><div v-for="execution in selectedTask.executions" :key="execution.id" class="execution-card"><div class="execution-heading"><strong>{{ execution.id }}</strong><span>{{ execution.status }}</span></div><div v-if="mcpTokens[execution.id]" class="token-box"><span>MCP token（仅本页）</span><code>{{ mcpTokens[execution.id] }}</code><button class="token-copy" @click="copyToken(mcpTokens[execution.id])">复制</button></div><div v-if="execution.receipts?.length"><h4>回执</h4><p v-for="receipt in execution.receipts" :key="receipt.report_id">{{ receipt.summary }} · {{ receipt.submitted_at }}</p></div><div v-if="execution.tests?.length"><h4>TestRun / CI</h4><p v-for="test in execution.tests" :key="test.id">{{ test.status }} · {{ test.command }}</p></div><div v-if="execution.conclusions?.length"><h4>结论</h4><p v-for="conclusion in execution.conclusions" :key="conclusion.created_at">{{ conclusion.kind }} · {{ conclusion.status }} · {{ conclusion.summary }}</p></div></div></section></aside>
         </template>
        <div v-else class="empty-state"><h2>选择或新建项目</h2><p>第一阶段控制闭环从一个项目开始。</p></div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.docs-link { color: #79e2bc; text-decoration: none; font-size: 12px; margin-right: 12px; }
.docs-link:hover { text-decoration: underline; }
.project-create { display: inline-flex; gap: 7px; align-items: center; margin-left: 6px; }
.project-create input { width: 150px; box-sizing: border-box; border: 1px solid #30455d; border-radius: 6px; background: #0d1725; color: #dce7f4; padding: 8px 9px; font: inherit; font-size: 12px; }
.notice { color: #79e2bc; background: #122d2a; border: 1px solid #246357; padding: 11px 14px; border-radius: 8px; margin-bottom: 18px; }
.repository-panel { margin-top: 18px; }
.repository-list { display: grid; gap: 9px; }
.repository-row { display: flex; justify-content: space-between; gap: 16px; padding: 11px 0; border-top: 1px solid #243248; }
.repository-row:first-child { border-top: 0; }
.repository-row small, .repository-state span, .repository-state em { display: block; color: #8194aa; font-size: 11px; margin-top: 4px; }
.repository-state { text-align: right; }
.repository-state b { color: #79e2bc; }
.repository-state em { color: #f2c879; font-style: normal; }
.repository-form { display: grid; grid-template-columns: 1fr 2fr auto; gap: 8px; margin-top: 14px; }
.builder-panel { margin-top: 18px; }
.panel-help { color: #8194aa; font-size: 12px; margin: 6px 0 0; }
.builder-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.builder-form { display: flex; flex-direction: column; gap: 9px; padding: 14px; border: 1px solid #263b52; border-radius: 10px; background: #111c2b; }
.builder-form h3 { margin: 0 0 3px; font-size: 14px; color: #dce7f4; }
.builder-form input, .builder-form textarea, .builder-form select { width: 100%; box-sizing: border-box; border: 1px solid #30455d; border-radius: 6px; background: #0d1725; color: #dce7f4; padding: 9px 10px; font: inherit; font-size: 12px; }
.builder-form textarea { resize: vertical; }
.builder-form button { align-self: flex-start; }
.task-row { display: flex; gap: 18px; align-items: center; justify-content: space-between; padding: 15px 0; border-top: 1px solid #243248; }
.task-row:first-of-type { border-top: 0; }
.task-main { min-width: 0; }
.task-main strong, .task-main small, .task-main p { display: block; }
.task-main small { color: #79e2bc; margin-top: 5px; font-size: 11px; }
.task-main p { color: #8194aa; font-size: 12px; margin: 7px 0 0; }
.task-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.token-box { display: grid; gap: 2px; max-width: 230px; color: #f2c879; font-size: 10px; }
.token-box code { overflow-wrap: anywhere; color: #dce7f4; font-size: 10px; }
.empty-panel { color: #8194aa; padding: 14px 0; }
.details-hint { color: #79e2bc; font-size: 11px; white-space: nowrap; }
.drawer-backdrop { position: fixed; inset: 0; z-index: 20; display: flex; justify-content: flex-end; background: rgba(3, 9, 16, .72); }
.task-drawer { width: min(560px, 92vw); height: 100%; overflow: auto; box-sizing: border-box; padding: 26px; background: #101c2c; border-left: 1px solid #30455d; box-shadow: -12px 0 40px rgba(0,0,0,.3); }
.drawer-heading, .execution-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.task-drawer h2 { margin: 0 0 10px; }
.task-drawer h3 { margin: 22px 0 5px; color: #dce7f4; font-size: 13px; }
.task-drawer h4 { margin: 12px 0 4px; color: #79e2bc; font-size: 12px; }
.task-drawer p { color: #aebed0; line-height: 1.55; }
.drawer-status { color: #f2c879 !important; }
.execution-card { margin-top: 14px; padding: 13px; border: 1px solid #2b4058; border-radius: 9px; background: #0d1725; }
.execution-heading span { color: #79e2bc; font-size: 11px; }
.token-copy { border: 1px solid #41627e; border-radius: 5px; background: #182b40; color: #dce7f4; padding: 3px 7px; font-size: 10px; cursor: pointer; }
.task-row { cursor: pointer; }
.task-row:hover { background: rgba(43, 64, 88, .24); }
@media (max-width: 900px) { .builder-grid { grid-template-columns: 1fr; } .repository-form { grid-template-columns: 1fr; } .repository-row { align-items: flex-start; flex-direction: column; } .repository-state { text-align: left; } .task-row { align-items: flex-start; flex-direction: column; } .task-actions { width: 100%; } }
</style>
