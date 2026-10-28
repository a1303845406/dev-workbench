<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { get, post } from '../shared/api/http'
import ModelSettings from '../features/model-settings/ModelSettings.vue'
import PlanningWorkspace from '../features/planning/PlanningWorkspace.vue'
type Project = {id:string;name:string;status:string}
type Execution = {id:string;status:string;receipts:{report_id:string;summary:string;submitted_at:string}[];tests:{id:string;status:string;command:string;exit_code:number}[];conclusions:{kind:string;status:string;summary:string;created_at:string}[]}
type Task = {id:string;title:string;description:string;acceptance:string;plan_status:string;executions:Execution[]}
type Detail = Project & {artifacts:{id:string;title:string;version:string;content:string}[];tasks:Task[];events:{id:number;event_type:string;created_at:string}[]}
const projects=ref<Project[]>([]), selectedId=ref(''), detail=ref<Detail|null>(null), error=ref('')
const view=ref<'planning'|'history'>('planning'), modelSettingsOpen=ref(false), modelSettingsTrigger=ref<HTMLButtonElement|null>(null)
const planner=ref<InstanceType<typeof PlanningWorkspace>|null>(null), modal=ref(false), name=ref(''), idea=ref(''), busy=ref(false)
const selectedTaskId=ref(''), task=computed(()=>detail.value?.tasks.find(t=>t.id===selectedTaskId.value)), selected=computed(()=>projects.value.find(p=>p.id===selectedId.value))
let sequence=0
function mayLeave(){return planner.value?.mayLeave()??true}
function closeSettings(){modelSettingsOpen.value=false;modelSettingsTrigger.value?.focus()}
async function loadDetail(){const id=selectedId.value, seq=++sequence;try{const next=await get<Detail>('/api/v2/projects/'+id);if(id===selectedId.value&&seq===sequence)detail.value=next}catch(e){error.value=String(e)}}
async function selectProject(id:string){if(id===selectedId.value||!await mayLeave())return;selectedId.value=id;view.value='planning';detail.value=null;selectedTaskId.value='';error.value='';await loadDetail()}
async function changeView(next:'planning'|'history'){if(next===view.value||!await mayLeave())return;view.value=next;if(next==='history')void loadDetail()}
async function openProject(){if(!await mayLeave())return;name.value='';idea.value='';modal.value=true}
async function createProject(){
  if(!name.value.trim()||busy.value)return
  busy.value=true;error.value=''
  try{const project=await post<Detail>('/api/v2/projects',{name:name.value.trim(),requirement:idea.value.trim()});projects.value.unshift(project);selectedId.value=project.id;detail.value=project;selectedTaskId.value='';view.value='planning';modal.value=false}catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}
}
function escape(e:KeyboardEvent){if(e.key==='Escape'){if(task.value)selectedTaskId.value='';else if(modal.value&&!busy.value)modal.value=false}}
onMounted(async()=>{window.addEventListener('keydown',escape);try{projects.value=(await get<{projects:Project[]}>('/api/v2/projects')).projects;selectedId.value=projects.value[0]?.id??'';if(selectedId.value)await loadDetail()}catch(e){error.value=String(e)}})
onUnmounted(()=>window.removeEventListener('keydown',escape))
</script>
<template>
<div class="shell co-shell">
  <ModelSettings v-if="modelSettingsOpen" @close="closeSettings" />
  <header class="topbar"><div class="brand"><span class="brand-mark">⌘</span><div><strong>开发工作台</strong><small>Dev Workbench · 人与 AI 的设计空间</small></div></div><div class="top-actions"><button ref="modelSettingsTrigger" class="quiet-button" @click="modelSettingsOpen=true">设置 · 模型服务</button><a href="/api/docs" target="_blank" rel="noopener">开发者 API</a><button class="primary" @click="openProject">+ 新建项目</button></div></header>
  <main class="app-layout"><aside class="project-sidebar" aria-label="项目列表"><div class="sidebar-heading"><div><span class="eyebrow">PROJECTS</span><h2>我的项目</h2></div><span class="count-badge">{{projects.length}}</span></div><button v-for="p in projects" :key="p.id" class="project-item" :class="{active:p.id===selectedId}" @click="selectProject(p.id)"><span class="project-icon">{{p.name.slice(0,1)}}</span><span><strong>{{p.name}}</strong><small>共同思考 · 逐层设计</small></span></button><div class="sidebar-empty"><strong>决策始终留给人</strong><p>每个阶段都有明确底稿、版本和人工审查。AI 提案不会自行变成批准结果。</p></div></aside>
    <section class="workspace"><div v-if="error" class="feedback error" role="alert">{{error}}<button @click="error=''" aria-label="关闭错误">×</button></div>
      <template v-if="selected"><div class="workspace-header"><div><span class="eyebrow">PROJECT SPACE</span><div class="title-line"><h1>{{selected.name}}</h1><span class="status-pill">AI 共创项目</span></div></div><span class="plan-help">本地保存 · 版本可追溯 · 人工决策</span></div><nav class="stage-nav" aria-label="项目工作区"><button :class="{active:view==='planning'}" @click="changeView('planning')">共创与设计</button><button :class="{active:view==='history'}" @click="changeView('history')">历史执行证据</button></nav>
        <PlanningWorkspace v-if="view==='planning'" :key="selectedId" :project-id="selectedId" ref="planner" @settings="modelSettingsOpen=true" />
        <section v-else class="legacy-history"><div class="plan-warning">旧版任务、产物和执行证据保留为只读历史；不参与当前共创流程的审批，也不能绕过新门禁继续编码。</div><button class="quiet-button" @click="loadDetail">刷新历史</button><div v-if="detail" class="overview-grid"><article class="card"><h2>历史任务与执行</h2><p v-if="!detail.tasks.length" class="plan-help">没有历史任务。设计审查完成后也不会自动创建编码任务。</p><button v-for="t in detail.tasks" :key="t.id" class="task-row history-task" @click="selectedTaskId=t.id"><strong>{{t.title}}</strong><span>{{t.plan_status}} · {{t.executions.length}} 次执行 ↗</span></button><details v-for="a in detail.artifacts" :key="a.id" class="history-artifact"><summary>{{a.title}} · {{a.version}}（旧版）</summary><pre>{{a.content||'空白历史节点'}}</pre></details></article><article class="card"><h2>项目事件</h2><p v-for="event in detail.events.slice(0,30)" :key="event.id" class="event-line"><strong>{{event.event_type}}</strong><small>{{event.created_at}}</small></p></article></div></section>
      </template><section v-else class="empty-workspace"><div class="empty-illustration">✦</div><span class="eyebrow">FROM AN IDEA TO A SHARED DESIGN</span><h2>带着一个想法，开始共同思考。</h2><p>和 AI 讨论问题，借助知识与资料把模糊想法变成标准需求，再逐层拆分、设计和人工审查。</p><button class="primary" @click="openProject">创建第一个项目 →</button></section>
    </section>
  </main>
  <div v-if="modal" class="modal-backdrop" @click.self="!busy&&(modal=false)"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="new-project-title"><div class="modal-heading"><div><span class="eyebrow">START A CONVERSATION</span><h2 id="new-project-title">创建共创项目</h2></div><button class="icon-button" :disabled="busy" aria-label="关闭" @click="modal=false">×</button></div><p>先给项目一个名字。初始想法将进入对话，不会被当成已批准的需求。</p><form @submit.prevent="createProject"><label for="project-name">项目名称</label><input id="project-name" v-model="name" required maxlength="200" autofocus><label for="project-idea">初始想法（可选）</label><textarea id="project-idea" v-model="idea" rows="5" maxlength="20000" placeholder="想为谁解决什么问题？"></textarea><p v-if="error" class="plan-run-error" role="alert">{{error}}</p><div class="modal-actions"><button type="button" class="quiet-button" :disabled="busy" @click="modal=false">取消</button><button class="primary" :disabled="busy||!name.trim()">创建并开始共创</button></div></form></section></div>
  <div v-if="task" class="drawer-backdrop" @click.self="selectedTaskId=''"><section class="task-drawer" role="dialog" aria-modal="true" aria-labelledby="history-task-title"><div class="drawer-heading"><div><span class="eyebrow">READ-ONLY EVIDENCE</span><h2 id="history-task-title">{{task.title}}</h2></div><button class="icon-button" aria-label="关闭详情" @click="selectedTaskId=''">×</button></div><p>{{task.description}}</p><h3>历史验收标准</h3><p>{{task.acceptance}}</p><section v-for="run in task.executions" :key="run.id" class="execution-card"><h3>{{run.status}}</h3><code>{{run.id}}</code><h4>执行回执</h4><p v-for="receipt in run.receipts" :key="receipt.report_id">{{receipt.summary}}<br><small>{{receipt.report_id}} · {{receipt.submitted_at}}</small></p><h4>独立测试</h4><p v-for="test in run.tests" :key="test.id">{{test.command}} · {{test.status}} · exit {{test.exit_code}}</p><h4>结论与验收</h4><p v-for="(c,i) in run.conclusions" :key="i">{{c.kind}} · {{c.status}}<br>{{c.summary}}<br><small>{{c.created_at}}</small></p></section></section></div>
</div>
</template>
