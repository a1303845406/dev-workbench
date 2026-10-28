<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { get, post } from '../../shared/api/http'
import type { Data, Kind, PlanningNode, State, Version } from './types'
import './planning.css'
import ConfirmDialog from '../../shared/ui/ConfirmDialog.vue'

const props = defineProps<{projectId:string}>()
const emit = defineEmits<{settings:[];changed:[]}>()
const state = ref<State|null>(null), error = ref(''), notice = ref(''), busy = ref(false)
const tab = ref<Kind>('requirement'), selectedId = ref(''), message = ref(''), comment = ref('')
const consent = ref(false), search = ref(false), query = ref(''), evidenceId = ref('')
const nodeId = ref(''), localSplit = ref(false), designTab = ref<'detailed_design'|'prototype_html'|'data_design'>('detailed_design')
const showDiff = ref(false), editing = ref(false), editText = ref(''), unresolvedText = ref(''), editBase = ref<string|null>(null)
const sourceTitle = ref(''), sourceContent = ref(''), sourceKind = ref('project'), showSource = ref(false)
const expandedSources = ref<string[]>([])
const confirmation = ref<InstanceType<typeof ConfirmDialog>|null>(null)
const confirmAction = (text:string) => confirmation.value?.ask(text)??Promise.resolve(false)
let timer: ReturnType<typeof setTimeout>|undefined, disposed = false, fetching = false
const kinds:Kind[] = ['requirement','decomposition','design']
const titles:Record<Kind,string> = {requirement:'需求共创',decomposition:'整体设计与拆分',design:'详细设计联合审查'}
const statusNames:Record<string,string> = {proposed:'AI 候选 · 未采纳',draft:'底稿 · 待人工审查',approved:'人工已批准',changes_requested:'已退回修改',stale:'上游变化 · 已失效',rejected:'未采纳',running:'生成中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'}
const currentVersion = (kind:Kind) => state.value?.versions.find(v=>v.id===state.value?.current[kind])
const versions = computed(()=>state.value?.versions.filter(v=>v.kind===tab.value).slice().reverse()??[])
const selected = computed(()=>versions.value.find(v=>v.id===selectedId.value))
const running = computed(()=>state.value?.runs.find(r=>r.status==='running'))
const selectedRun = computed(()=>state.value?.runs.find(r=>r.id===evidenceId.value)??state.value?.runs.at(-1))
const baseVersion = computed(()=>state.value?.versions.find(v=>v.id===selected.value?.based_on))
const isCurrent = computed(()=>selected.value?.id===state.value?.current[tab.value])
const canReview = computed(()=>isCurrent.value && ['draft','changes_requested','approved'].includes(selected.value?.status??''))
const canGenerate = computed(()=>tab.value==='requirement'||(currentVersion('requirement')?.status==='approved'&&(tab.value!=='design'||currentVersion('decomposition')?.status==='approved')))
const dirty = computed(()=>editing.value || !!message.value.trim() || !!comment.value.trim() || !!sourceContent.value.trim())
const tree = computed(()=>{
  const nodes=selected.value?.data.nodes??[], result:{node:PlanningNode;depth:number}[]=[]
  const visit=(parent:string|null,depth:number)=>{for(const node of nodes.filter(n=>n.parent_id===parent)){result.push({node,depth});visit(node.id,depth+1)}}
  visit(null,0); return result
})
const activeNode = computed(()=>selected.value?.data.nodes?.find(n=>n.id===nodeId.value))
const coverage = computed(()=>{
  const requirement=currentVersion('requirement')?.data.document??''
  const ids=[...new Set(requirement.match(/\bFR-\d+\b/g)??[])]
  return ids.map(id=>({id,nodes:(selected.value?.data.nodes??[]).filter(n=>n.type==='feature'&&n.requirement_ids.includes(id))}))
})
const rendered = (v?:Version)=>!v?'尚无底稿':v.kind==='requirement'?v.data.document??'':v.kind==='decomposition'?JSON.stringify(v.data,null,2):v.data[designTab.value]??''
const prototype = computed(()=>{
  const doc=new DOMParser().parseFromString(selected.value?.data.prototype_html??'','text/html')
  doc.querySelectorAll('script,iframe,object,embed,form,base,meta,link,svg,math,audio,video').forEach(n=>n.remove())
  doc.querySelectorAll('*').forEach(el=>{for(const a of [...el.attributes])if(/^on/i.test(a.name)||['src','srcset','href','action','formaction','ping','target','xlink:href'].includes(a.name))el.removeAttribute(a.name)})
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none';"><meta name="referrer" content="no-referrer">${doc.head.innerHTML}</head><body>${doc.body.innerHTML}</body></html>`
})
function update(next:State){
  if(state.value && next.revision<state.value.revision)return
  const previousRun=state.value?.runs.at(-1)
  state.value=next
  if(!editing.value){
    const latest=next.runs.at(-1)
    if(latest?.result_version_id && latest.result_version_id!==previousRun?.result_version_id){
      const v=next.versions.find(v=>v.id===latest.result_version_id)
      if(v){tab.value=v.kind;selectedId.value=v.id;showDiff.value=!!v.based_on;evidenceId.value=latest.id}
    }
    if(!versions.value.some(v=>v.id===selectedId.value))selectedId.value=next.current[tab.value]??versions.value[0]?.id??''
  }
}
async function refresh(){
  if(fetching || disposed || busy.value)return
  fetching=true
  try{const next=await get<State>(base());if(!disposed){update(next);error.value=''}}catch(e){if(!disposed)error.value=String(e)}finally{fetching=false}
}
function base(){return '/api/v2/projects/'+props.projectId+'/planning'}
async function mutate(path:string,body:Record<string,unknown>={}){
  if(!state.value||busy.value)return false
  busy.value=true;error.value='';notice.value=''
  try{const next=await post<State>(base()+path,{...body,expected_revision:state.value.revision});if(!disposed){update(next);emit('changed')};return true}
  catch(e){error.value=e instanceof Error?e.message:String(e);return false}
  finally{busy.value=false}
}
async function chooseTab(kind:Kind){
  if(editing.value && !await confirmAction('放弃尚未保存的手工调整？'))return
  editing.value=false;tab.value=kind;selectedId.value=state.value?.current[kind]??versions.value[0]?.id??'';nodeId.value='';localSplit.value=false;showDiff.value=false
}
async function chooseVersion(event:Event){
  const id=(event.target as HTMLSelectElement).value
  if(editing.value && !await confirmAction('放弃尚未保存的手工调整？')){(event.target as HTMLSelectElement).value=selectedId.value;return}
  editing.value=false;selectedId.value=id;nodeId.value='';showDiff.value=false
}
async function generate(action:string){
  if(editing.value){error.value='请先保存或放弃手工调整，避免 AI 使用旧版本。';return}
  if(!canGenerate.value){error.value='请先人工批准当前上游底稿。';return}
  if(!consent.value){error.value='请先确认发送给模型的资料范围。';return}
  const ok=await mutate('/generate',{action,message:message.value,allow_external:consent.value,allow_search:search.value,search_query:query.value,target_node_id:localSplit.value?nodeId.value:null})
  if(ok){message.value='';evidenceId.value=state.value?.runs.at(-1)?.id??'';notice.value='已发起生成。可以离开页面，返回后继续查看结果；生成不会自动采纳。'}
}
async function adopt(){
  const v=selected.value
  if(!v||!await confirmAction('将候选 V'+v.number+' 设为当前底稿？下游设计与审批将失效，但历史保留。'))return
  if(await mutate('/versions/'+v.id+'/adopt')){showDiff.value=false;notice.value='候选稿已采纳为底稿，尚未批准。'}
}
async function review(decision:string){
  const v=selected.value;if(!v)return
  if(decision==='approve'&&!await confirmAction('确认人工批准 '+titles[v.kind]+' V'+v.number+'？审批只绑定这个版本。设计包包含详细设计、原型与数据设计三项。'))return
  if(await mutate('/versions/'+v.id+'/review',{decision,comment:comment.value})){comment.value='';notice.value=decision==='approve'?'已记录人工审批；编码执行仍锁定。':'已记录返工意见。'}
}
function startEdit(){
  const v=selected.value;if(!v)return
  editText.value=v.kind==='requirement'?v.data.document??'':JSON.stringify(v.data,null,2)
  unresolvedText.value=(v.data.unresolved??[]).join('\n');editBase.value=state.value?.current[v.kind]??null;editing.value=true
}
async function saveEdit(){
  if(!selected.value||!state.value)return
  if(editBase.value!==state.value.current[tab.value]){error.value='底稿已被其他操作修改，请复制当前编辑内容并刷新后重新调整。';return}
  try{
    const data:Data=tab.value==='requirement'?{document:editText.value,unresolved:unresolvedText.value.split('\n').map(s=>s.trim()).filter(Boolean),checks:[]}:JSON.parse(editText.value)
    if(await mutate('/versions',{kind:tab.value,data})){editing.value=false;selectedId.value=state.value.current[tab.value]??'';notice.value='已保存新的不可变版本；旧版审批不继承。'}
  }catch{error.value='请输入有效 JSON；不能清除节点必填字段。'}
}
async function addSource(){
  if(await mutate('/sources',{title:sourceTitle.value,kind:sourceKind.value,content:sourceContent.value})){sourceTitle.value='';sourceContent.value='';showSource.value=false;notice.value='资料仅存入当前项目，不会自动晋升为全局经验。'}
}
function toggleSource(id:string){expandedSources.value=expandedSources.value.includes(id)?expandedSources.value.filter(x=>x!==id):[...expandedSources.value,id]}
function leave(event:BeforeUnloadEvent){if(dirty.value){event.preventDefault();event.returnValue=''}}
async function mayLeave(){return !dirty.value||await confirmAction('当前有未发送内容或未保存调整。确定离开？')}
defineExpose({mayLeave})
async function poll(){await refresh();if(!disposed)timer=setTimeout(poll,running.value?1500:6000)}
onMounted(()=>{window.addEventListener('beforeunload',leave);void poll()})
onUnmounted(()=>{disposed=true;clearTimeout(timer);window.removeEventListener('beforeunload',leave)})
</script>

<template>
<section class="planning" aria-label="AI 共创工作区">
  <ConfirmDialog ref="confirmation" />
  <div v-if="error" class="feedback error" role="alert">{{error}} <button @click="refresh" :disabled="busy">刷新状态</button></div>
  <div v-if="notice" class="feedback success" role="status">{{notice}}</div>
  <div v-if="!state" class="plan-empty"><h2>正在读取项目共创记录</h2><p>历史对话、版本与审批都保存在本地。</p><button @click="refresh">重试加载</button></div>
  <template v-else>
    <div class="plan-hero"><div><span class="eyebrow">THINK TOGETHER · REVIEW BEFORE NEXT</span><h2>先共同想清楚，再让设计逐层生长。</h2><p>持续讨论 → 标准需求 → 分层拆分 → 详细设计 / 原型 / 数据设计</p></div><span class="plan-lock">⌑ 编码执行未开放</span></div>
    <nav class="plan-steps" aria-label="共创阶段"><button v-for="(kind,i) in kinds" :key="kind" :class="{active:tab===kind,done:currentVersion(kind)?.status==='approved'}" :aria-current="tab===kind?'step':undefined" @click="chooseTab(kind)"><b>0{{i+1}}</b><span><strong>{{titles[kind]}}</strong><small>{{currentVersion(kind)?statusNames[currentVersion(kind)!.status]:'等待共同产出'}}</small></span><i>{{currentVersion(kind)?.status==='approved'?'✓':'→'}}</i></button></nav>
    <div v-if="state.provider_mode==='deterministic'" class="plan-warning">当前为规则模式，不能进行 AI 共创。我们不会用演示回复代替真实模型。<button @click="emit('settings')">配置模型服务 →</button></div>
    <div v-if="state.stage==='design_approved'" class="plan-approved" role="status">三项设计已联合批准。下一阶段待你确认，系统不会自动生成编码任务或执行代码。</div>
    <div class="plan-columns">
      <article class="plan-card plan-dialogue"><header><div><span class="eyebrow">CO-CREATION</span><h3>和 AI 一起推敲</h3></div><span class="plan-chip">{{state.provider_mode==='codex'?'Codex':state.provider_mode==='real'?'API 模型':'未配置 AI'}}</span></header>
        <div class="plan-messages" aria-live="polite" aria-relevant="additions">
          <div v-if="!state.messages.length" class="plan-welcome"><span>✦</span><h3>从你的真实问题开始</h3><p>想为谁解决什么问题？可以先说一个场景，不必一次写出完整需求。</p><p>AI 会结合内置知识、项目资料和你允许的联网检索追问，逐步形成可审查的文档。</p></div>
          <div v-for="m in state.messages" :key="m.id" class="plan-message" :class="m.role"><strong>{{m.role==='user'?'你':'AI 共创助手'}}</strong><p>{{m.content}}</p><button v-if="m.run_id&&m.role==='assistant'" class="text-button" @click="evidenceId=m.run_id">查看本轮依据 ↗</button><time>{{new Date(m.created_at).toLocaleString()}}</time></div>
        </div>
        <div v-if="running" class="plan-running" role="status"><span class="plan-pulse"></span>正在检索 / 思考 / 生成候选稿<button class="text-button" :disabled="busy" @click="mutate('/runs/'+running.id+'/cancel')">取消</button></div>
        <form class="plan-composer" @submit.prevent="generate(tab==='requirement'?'discuss':tab)">
          <label for="co-message">{{tab==='requirement'?'描述想法、回答问题或提出修改意见':'提出本阶段的设计意见'}}</label><textarea id="co-message" v-model="message" rows="4" maxlength="20000" placeholder="例如：先不做复杂权限，重点是让小团队快速处理工单……"></textarea>
          <label class="plan-check"><input type="checkbox" v-model="consent">允许向所选模型发送本项目对话、当前文档和下方资料（可能含敏感信息）</label>
          <label class="plan-check"><input type="checkbox" v-model="search">本轮使用搜索引擎查证公开信息</label><input v-if="search" v-model="query" aria-label="公开检索词" maxlength="300" placeholder="仅输入可公开的检索词，不自动发送私有需求">
          <label v-if="tab==='decomposition'&&isCurrent&&activeNode" class="plan-check"><input type="checkbox" v-model="localSplit">仅重拆所选节点 {{activeNode.id}}，其他分支保持不变</label>
          <div class="plan-actions"><button class="primary" :disabled="busy||!!running||!canGenerate||!message.trim()||state.provider_mode==='deterministic'">{{tab==='requirement'?'发送并继续讨论':'按意见生成新候选'}}</button><button type="button" class="quiet-button" :disabled="busy||!!running||!canGenerate||state.provider_mode==='deterministic'" @click="generate(tab==='requirement'?'requirements':tab)">{{tab==='requirement'?'汇总标准需求':tab==='decomposition'?'生成整体设计与拆分':'生成三项设计包'}}</button></div>
          <p class="plan-help">{{canGenerate?'AI 只提出候选稿。采纳与批准是两个独立的人工决定。':'上游当前版本尚未批准，本阶段生成被后端门禁阻止。'}}</p>
        </form>
      </article>
      <article class="plan-card plan-document"><header><div><span class="eyebrow">VERSIONED DOCUMENT</span><h3>{{titles[tab]}}</h3></div><button class="text-button" @click="refresh" :disabled="busy">刷新</button></header>
        <div class="plan-versionbar"><label for="plan-version">文档版本</label><select id="plan-version" :value="selectedId" @change="chooseVersion"><option v-if="!versions.length" value="">尚无版本</option><option v-for="v in versions" :key="v.id" :value="v.id">V{{v.number}} · {{statusNames[v.status]}} {{v.id===state.current[tab]?' · 当前':''}}</option></select></div>
        <div v-if="!selected" class="plan-empty"><span>↗</span><h3>{{tab==='requirement'?'对话会逐步沉淀为标准需求':'等待上游审查与 AI 设计'}}</h3><p>{{tab==='requirement'?'需求包含目标、范围、用户场景、FR 编号、业务规则、数据接口、非功能、验收与风险等十二个章节。':'点击左侧生成按钮产出完整候选稿，而不是手工一层层创建模块。'}}</p></div>
        <template v-else>
          <div class="plan-version-meta"><span class="plan-chip" :class="selected.status">{{statusNames[selected.status]}}</span><small>{{selected.origin==='ai'?'AI 生成':'人工保存'}} · {{new Date(selected.created_at).toLocaleString()}}</small></div>
          <p class="plan-help" v-if="selected.requirement_version_id">依据需求 V{{state.versions.find(v=>v.id===selected!.requirement_version_id)?.number}}<span v-if="selected.decomposition_version_id"> / 拆分 V{{state.versions.find(v=>v.id===selected!.decomposition_version_id)?.number}}</span> · 审批不跨版本继承</p>
          <div v-if="selected.status==='proposed'" class="plan-proposal"><strong>这是候选稿，不是已批准结果</strong><div class="plan-actions"><button class="primary" :disabled="busy||editing" @click="adopt">采纳为底稿</button><button :disabled="busy||editing" @click="mutate('/versions/'+selected.id+'/reject')">不采纳</button></div></div>
          <div class="plan-actions plan-doc-tools"><button v-if="baseVersion" class="text-button" @click="showDiff=!showDiff">{{showDiff?'收起对照':'与上一底稿对照'}}</button><button v-if="isCurrent&&!editing&&selected.status!=='stale'" class="text-button" @click="startEdit">手工调整为新版本</button></div>
          <div v-if="editing" class="plan-edit"><label for="plan-edit">{{tab==='requirement'?'需求 Markdown 正文':'结构化设计数据 JSON（建议优先通过对话修改）'}}</label><textarea id="plan-edit" v-model="editText" rows="22" spellcheck="false"></textarea><template v-if="tab==='requirement'"><label for="plan-unresolved">待确认项（每行一项；确认解决后删除）</label><textarea id="plan-unresolved" v-model="unresolvedText" rows="3"></textarea></template><div class="plan-actions"><button class="primary" :disabled="busy" @click="saveEdit">保存新版本</button><button @click="editing=false">放弃调整</button></div></div>
          <template v-else>
            <nav v-if="tab==='design'" class="plan-design-tabs" aria-label="设计包文档"><button v-for="(name,key) in {detailed_design:'详细设计',prototype_html:'原型预览',data_design:'数据设计'}" :key="key" :class="{active:designTab===key}" @click="designTab=key;showDiff=false">{{name}}</button></nav>
            <div v-if="showDiff" class="plan-diff"><section><h4>原底稿 V{{baseVersion?.number}}</h4><pre>{{rendered(baseVersion)}}</pre></section><section><h4>当前查看 V{{selected.number}}</h4><pre>{{rendered(selected)}}</pre></section></div>
            <div v-else-if="tab==='decomposition'" class="plan-structure"><pre class="plan-prose">{{selected.data.overview}}</pre><div class="plan-tree" aria-label="模块与特性归属树"><button v-for="entry in tree" :key="entry.node.id" :style="{paddingLeft:(12+entry.depth*18)+'px'}" :class="{active:nodeId===entry.node.id}" @click="nodeId=entry.node.id;localSplit=false"><small>{{entry.node.type==='module'?'▣ 模块':'◇ 特性'}}</small><strong>{{entry.node.title}}</strong><code>{{entry.node.id}}</code></button></div><section v-if="activeNode" class="plan-node"><h4>{{activeNode.id}} · {{activeNode.title}}</h4><dl><dt>职责</dt><dd>{{activeNode.responsibility}}</dd><dt>不包含</dt><dd>{{activeNode.exclusions}}</dd><dt>需求关联</dt><dd>{{activeNode.requirement_ids.join(' · ')}}</dd><dt>验收</dt><dd>{{activeNode.acceptance}}</dd><dt>依赖（非归属）</dt><dd>{{activeNode.dependencies.join(' · ')||'无'}}</dd><dt>拆分依据</dt><dd>{{activeNode.rationale}}</dd></dl></section><section class="plan-coverage"><h4>需求 → 特性覆盖</h4><p v-for="item in coverage" :key="item.id" :class="{missing:!item.nodes.length}"><code>{{item.id}}</code> {{item.nodes.map(n=>n.id).join('、')||'未覆盖'}}</p></section></div>
            <div v-else-if="tab==='design'&&designTab==='prototype_html'" class="plan-prototype"><p class="plan-help">隔离静态预览：脚本、网络、表单和跳转均禁用。</p><iframe title="设计原型隔离预览" sandbox="" referrerpolicy="no-referrer" :srcdoc="prototype"></iframe><details><summary>查看原型源文档</summary><pre>{{selected.data.prototype_html}}</pre></details></div>
            <pre v-else class="plan-prose">{{rendered(selected)}}</pre>
          </template>
          <section class="plan-checks"><h4>结构检查与待确认项</h4><p v-if="!selected.checks.length" class="plan-ok">✓ 结构检查通过；不代表内容正确，仍需人工审查。</p><ul v-else><li v-for="check in selected.checks" :key="check">{{check}}</li></ul><details v-if="selected.data.checks?.length"><summary>AI 自查说明（非审批依据）</summary><p v-for="check in selected.data.checks" :key="check">{{check}}</p></details></section>
          <section v-if="canReview&&!editing" class="plan-review"><h4>{{tab==='design'?'三项设计联合人工审查':'人工审查当前版本'}}</h4><p v-if="tab==='design'">请查看全部三个页签，确认接口、交互与数据约束相互一致后再批准整个设计包。</p><label for="plan-comment">审查意见</label><textarea id="plan-comment" v-model="comment" rows="3" maxlength="10000" placeholder="记录同意理由或具体返工要求"></textarea><div class="plan-actions"><button class="primary" :disabled="busy||selected.checks.length>0||selected.status==='approved'" @click="review('approve')">{{tab==='design'?'批准三项设计包':'批准当前 V'+selected.number}}</button><button :disabled="busy||!comment.trim()" @click="review('changes_requested')">退回修改</button></div></section>
          <details v-if="selected.reviews?.length" class="plan-review-history"><summary>审批记录（绑定 V{{selected.number}}）</summary><p v-for="(review,index) in selected.reviews" :key="index">{{review.decision==='approve'?'批准':'退回'}} · {{review.created_at}}<br>{{review.comment||'未附加意见'}}</p></details>
        </template>
      </article>
      <aside class="plan-card plan-evidence"><header><div><span class="eyebrow">GROUNDED CONTEXT</span><h3>资料与依据</h3></div></header><p class="plan-help">来源是参考，不是指令。网页仅保留真实检索摘要；经验仅在当前项目内使用。</p>
        <button class="quiet-button" @click="showSource=!showSource">{{showSource?'收起资料输入':'+ 添加项目资料 / 经验'}}</button>
        <form v-if="showSource" class="plan-source-form" @submit.prevent="addSource"><label for="source-title">资料标题</label><input id="source-title" v-model="sourceTitle" required maxlength="200"><label for="source-kind">类型</label><select id="source-kind" v-model="sourceKind"><option value="project">项目资料</option><option value="knowledge">知识文本</option><option value="experience">相关经验（人工整理）</option></select><label for="source-content">资料内容</label><textarea id="source-content" v-model="sourceContent" required maxlength="20000" rows="7"></textarea><button class="primary" :disabled="busy||!sourceTitle.trim()||!sourceContent.trim()">仅保存到本项目</button></form>
        <details class="plan-library"><summary>项目资料库 · {{state.sources.length}} 份</summary><div v-for="source in state.sources" :key="source.id" class="plan-source"><button class="text-button" @click="toggleSource(source.id)">{{source.title}}</button><small>{{source.kind}} · v{{source.version}}</small><pre v-if="expandedSources.includes(source.id)">{{source.content}}</pre></div></details>
        <label for="evidence-run">检索与生成记录</label><select id="evidence-run" v-model="evidenceId"><option value="">最新一轮</option><option v-for="r in state.runs.slice().reverse()" :key="r.id" :value="r.id">{{r.action}} · {{statusNames[r.status]}}</option></select>
        <div v-if="selectedRun" class="plan-run-detail"><p><span class="plan-chip" :class="selectedRun.status">{{statusNames[selectedRun.status]}}</span> {{selectedRun.provider_mode}}</p><p v-if="selectedRun.error" class="plan-run-error">{{selectedRun.error}}</p><details v-if="selectedRun.response_text"><summary>未采纳的模型原始输出（仅供诊断）</summary><pre>{{selectedRun.response_text}}</pre></details><p v-for="(tool,i) in selectedRun.tools" :key="i" class="plan-tool"><strong>{{tool.tool}} · {{tool.status}}</strong>{{tool.message}}<small v-if="tool.finish_reason">结束原因：{{tool.finish_reason}}</small><small v-if="tool.usage">供应商 tokens：输入 {{tool.usage.prompt_tokens??'未返回'}} / 输出 {{tool.usage.completion_tokens??'未返回'}} / 合计 {{tool.usage.total_tokens??'未返回'}}；不代表最终账单</small></p><h4>本轮提供的依据 · {{selectedRun.evidence.length}}</h4><div v-for="source in selectedRun.evidence" :key="source.id" class="plan-source"><strong>{{source.title}}</strong><small>{{selectedRun.citations?.includes(source.id)?'AI 引用':'已提供上下文'}} · {{source.kind}}</small><a v-if="source.url&&/^https?:\/\//.test(source.url)" :href="source.url" target="_blank" rel="noopener noreferrer">查看来源 ↗</a><details><summary>原始摘要 / 版本 {{source.version}}</summary><pre>{{source.content}}</pre></details></div></div><p v-else class="plan-help">尚无生成记录。没有检索过的内容不会显示为“已查证”。</p>
      </aside>
    </div>
  </template>
</section>
</template>
