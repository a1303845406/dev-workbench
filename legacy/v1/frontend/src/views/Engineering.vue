<script setup lang="ts">
/** 提示词工程工作区（FR-04）六步工作流。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post, put, type OutboundConf } from '../api'
import { onSse } from '../sse'
import MarkdownView from '../components/MarkdownView.vue'
import OutboundConfirmDialog from '../components/OutboundConfirmDialog.vue'

const route = useRoute()
const pid = route.params.pid as string
const confirmRef = ref()
const step = ref(0)

// ① 上下文
const docs = ref<any[]>([])
const picked = ref<string[]>([])
const totalChars = computed(() =>
  docs.value.filter(d => picked.value.includes(d.id)).reduce((a, b) => a + b.chars, 0))

// ② 复杂度与组合
const complexity = ref('中等')
const modules = ref<string[]>([])
const hardRow = ['requirement', '<任一 development-*>', 'test', 'acceptance']
const MODULE_NAMES: Record<string, string> = {
  requirement: '需求', 'design-outline': '概设', prototype: '原型', 'detailed-design': '详设',
  database: '数据库设计', 'development-backend': '后端开发', 'development-frontend': '前端开发',
  test: '测试', regression: '回归', acceptance: '验收'
}
const proposal = ref<any>(null)
const composing = ref(false)

// ③ 拆分
const contextLimit = ref(128000)
const coef = ref(0.6)
const tasksData = ref<any>(null)
const overbudget = ref<string[]>([])

// ④/⑤ 计划与执行
const plan = ref<any>(null)
const running = ref(false)
const ringLog = ref<string[]>([])
const resumeHint = ref('')

// ⑥ 提示词
const prompts = ref<any[]>([])
const currentPrompt = ref<any>(null)
const promptTab = ref('preview')
const editingBody = ref('')

async function loadDocs() {
  docs.value = (await get(`/api/projects/${encodeURIComponent(pid)}/docs`)).files
}
onMounted(async () => { await loadDocs(); await refreshPrompts() })

async function doCompose() {
  const conf: OutboundConf | null = await confirmRef.value.open(picked.value, totalChars.value + 3000)
  if (!conf) return
  composing.value = true
  try {
    const r = await post(`/api/projects/${encodeURIComponent(pid)}/compose`, {
      complexity: complexity.value, context_file_ids: picked.value, outbound_confirmation: conf
    })
    proposal.value = r
    modules.value = r.modules_selected
    ElMessage.success('组合建议已生成（硬约束校验通过）')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
  finally { composing.value = false }
}

async function doSplit() {
  try {
    const r = await post(`/api/projects/${encodeURIComponent(pid)}/split`, {
      proposal: proposal.value, complexity: complexity.value, modules_selected: modules.value,
      context_limit_chars: contextLimit.value, coefficient: coef.value
    })
    tasksData.value = r
    overbudget.value = r.overbudget_leaves || []
    ElMessage.success(`任务拆分完成：${r.tasks.length} 个任务单元（预算 ${r.effective_chars} 字符）`)
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

async function buildPlan() {
  try {
    plan.value = await post(`/api/projects/${encodeURIComponent(pid)}/plan/generate`)
    ElMessage.success(`编排计划已生成：${plan.value.rings.length} 环（任务×模块）`)
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

async function loadPlan() {
  try { plan.value = await get(`/api/projects/${encodeURIComponent(pid)}/plan`) } catch { plan.value = null }
}

async function confirmPlan() {
  const conf: OutboundConf | null = await confirmRef.value.open(picked.value, totalChars.value + 5000)
  if (!conf) return
  running.value = true
  try {
    await post(`/api/projects/${encodeURIComponent(pid)}/plan/confirm`, { outbound_confirmation: conf })
    step.value = 4
    ElMessage.success('编排执行已启动（每环落盘即自动推进）')
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
    await loadPlan()
  }
}

async function adjustSeq(ring: any, dir: -1 | 1) {
  const rings = [...plan.value.rings]
  const i = rings.findIndex((r: any) => r.ring_id === ring.ring_id)
  const j = i + dir
  if (j < 0 || j >= rings.length) return
  ;[rings[i], rings[j]] = [rings[j], rings[i]]
  try {
    plan.value = await put(`/api/projects/${encodeURIComponent(pid)}/plan`, {
      rings: rings.map((r: any, k: number) => ({ ring_id: r.ring_id, seq: k + 1, parallel_group: r.parallel_group }))
    })
  } catch (e: any) {
    ElMessage.warning(`调序被拒绝：${e.message}`)
    await loadPlan()
  }
}

async function resumePlan() {
  try {
    await post(`/api/plans/${plan.value.id}/resume?pid=${encodeURIComponent(pid)}`, {})
    ElMessage.success('已从断点恢复（前序环不重算）')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

async function pausePlan() {
  try {
    await post(`/api/plans/${plan.value.id}/pause?pid=${encodeURIComponent(pid)}`, {})
    await loadPlan()
    ElMessage.success('已暂停')
  } catch (e: any) { ElMessage.error(e.message) }
}

const offSse = onSse((event, data) => {
  if (event === 'ring.started') ringLog.value.unshift(`▶ ${data.ring_id} ${data.task}·${data.module} 开始`)
  if (event === 'ring.completed') {
    ringLog.value.unshift(`✓ ${data.ring_id} 完成（输出 ${data.output_chars} 字符，输入包 ${data.budget_actual}）`)
    loadPlan()
  }
  if (event === 'ring.failed' || event === 'ring.suspended') {
    ringLog.value.unshift(`✗ ${data.ring_id} ${data.error_code}`)
    loadPlan().then(() => { resumeHint.value = plan.value?.resume_hint || '' })
  }
  if (event === 'plan.status') {
    loadPlan().then(() => {
      if (plan.value?.status === 'done') {
        running.value = false
        ElMessage.success('全部环完成（AC-14）')
        refreshPrompts()
      }
    })
  }
})
onUnmounted(offSse)

async function refreshPrompts() {
  try { prompts.value = (await get(`/api/projects/${encodeURIComponent(pid)}/prompts`)).prompts } catch { }
}

function openPrompt(p: any) {
  get(`/api/prompts/${p.prompt_id}?pid=${encodeURIComponent(pid)}`).then(r => {
    currentPrompt.value = r
    editingBody.value = r.body
    promptTab.value = 'preview'
  })
}

async function saveEdit() {
  const r = await put(`/api/prompts/${currentPrompt.value.prompt_id}?pid=${encodeURIComponent(pid)}`,
    { body: editingBody.value })
  ElMessage.success(`已保存为新草稿版本 v${r.version}`)
  await refreshPrompts()
  openPrompt(r)
}

async function finalize() {
  try {
    const r = await post(`/api/prompts/${currentPrompt.value.prompt_id}/finalize?pid=${encodeURIComponent(pid)}`)
    ElMessage.success(`已定稿 v${r.version}`)
    if (r.handoff_stale) ElMessage.warning(`交接摘要过期：${r.handoff_stale.affected_rings.join('、')}，可一键重生成`)
    await refreshPrompts()
    openPrompt(currentPrompt.value)
  } catch (e: any) { ElMessage.error(e.message) }
}

async function regenHandoff() {
  const r = await post(`/api/prompts/${currentPrompt.value.prompt_id}/regen-handoff?pid=${encodeURIComponent(pid)}`)
  ElMessage.success(`交接摘要已按定稿 v${r.based_on_version} 重生成`)
}

function copyPrompt() {
  navigator.clipboard?.writeText(editingBody.value).then(
    () => ElMessage.success('已复制（投喂外部 IDE 前请过脱敏裁决）'),
    () => ElMessage.warning('剪贴板不可用（非安全上下文），请手动全选复制'))
}

const leafTasks = computed(() => (tasksData.value?.tasks || []).filter(
  (t: any) => !(tasksData.value?.tasks || []).some((x: any) => x.parent_id === t.id)))
</script>

<template>
  <div style="padding:14px 22px; overflow:auto; height:100%">
    <el-steps :active="step" simple style="margin-bottom:14px">
      <el-step title="① 勾选上下文" @click="step = 0" />
      <el-step title="② 复杂度与组合" @click="step = 1" />
      <el-step title="③ 任务拆分" @click="step = 2" />
      <el-step title="④ 编排计划" @click="step = 3" />
      <el-step title="⑤ 环执行" @click="step = 4" />
      <el-step title="⑥ 提示词管理" @click="step = 5" />
    </el-steps>

    <!-- ① 上下文 -->
    <el-card v-show="step === 0" shadow="never">
      <template #header><b>手动选择上下文（C9：F1 产出 → F3 输入）</b></template>
      <el-checkbox-group v-model="picked">
        <div v-for="d in docs" :key="d.id" style="margin:4px 0">
          <el-checkbox :value="d.id">{{ d.id }} <span style="color:#909399">（{{ d.chars }} 字符）</span></el-checkbox>
        </div>
      </el-checkbox-group>
      <el-alert :closable="false" style="margin-top:10px"
        :title="`已选 ${picked.length} 个文件，合计 ${totalChars} 字符`" type="info" />
      <div style="margin-top:12px">
        <el-button type="primary" :disabled="!picked.length" @click="step = 1">继续</el-button>
      </div>
    </el-card>

    <!-- ② 复杂度与组合 -->
    <el-card v-show="step === 1" shadow="never">
      <template #header><b>复杂度 → 模块组合（G-08 映射 · 硬约束锁定）</b></template>
      <el-radio-group v-model="complexity" style="margin-bottom:12px">
        <el-radio-button value="简单">简单</el-radio-button>
        <el-radio-button value="中等">中等</el-radio-button>
        <el-radio-button value="复杂">复杂</el-radio-button>
      </el-radio-group>
      <div style="margin-bottom:10px">
        <el-button type="primary" :loading="composing" @click="doCompose">获取组合建议（外发确认）</el-button>
      </div>
      <div v-if="proposal">
        <el-alert :closable="false" type="success"
          :title="`建议组合：${modules.map(m => MODULE_NAMES[m] || m).join(' → ')}${proposal.deviation_from_map ? '（已人工调整·偏离映射）' : ''}`"
          style="margin-bottom:10px" />
        <el-checkbox-group v-model="modules">
          <el-checkbox v-for="m in Object.keys(MODULE_NAMES)" :key="m" :value="m"
            :disabled="hardRow.includes(m) || (m.startsWith('development-') && modules.length && modules.filter(x => x.startsWith('development-')).length === 1 && modules.includes(m))">
            {{ MODULE_NAMES[m] }}
          </el-checkbox>
        </el-checkbox-group>
        <el-alert :closable="false" type="warning" style="margin-top:10px"
          title="硬约束（FR-04 规则 3）：最少组合必含 需求 → 任一开发 → 测试 → 验收" />
        <div style="margin-top:12px">
          <el-button @click="step = 0">上一步</el-button>
          <el-button type="primary" @click="step = 2">下一步：任务拆分</el-button>
        </div>
      </div>
    </el-card>

    <!-- ③ 任务拆分 -->
    <el-card v-show="step === 2" shadow="never">
      <template #header>
        <div style="display:flex; align-items:center">
          <b>任务拆分 · 层级树（按外部 IDE 上下文预算）</b>
          <div style="flex:1" />
          <span style="color:#909399; font-size:12px; margin-right:10px">
            窗口上限 <el-input-number v-model="contextLimit" :step="32000" :min="16000" size="small" />
            系数 <el-input-number v-model="coef" :step="0.1" :min="0.3" :max="0.9" size="small" />
          </span>
          <el-button type="primary" size="small" @click="doSplit">执行拆分</el-button>
        </div>
      </template>
      <template v-if="tasksData">
        <el-table :data="tasksData.tasks" size="small" row-key="id" default-expand-all>
          <el-table-column label="任务" min-width="220">
            <template #default="{ row }">
              <el-tag v-if="tasksData.tasks.some((x: any) => x.parent_id === row.id)" size="small" type="info"
                style="margin-right:6px">容器</el-tag>
              <b>{{ row.id }}</b> {{ row.title }}
            </template>
          </el-table-column>
          <el-table-column label="模块" width="110">
            <template #default="{ row }">{{ MODULE_NAMES[row.module] || row.module }}</template>
          </el-table-column>
          <el-table-column label="依赖" width="120">
            <template #default="{ row }">{{ (row.depends_on || []).join('、') || '—' }}</template>
          </el-table-column>
          <el-table-column label="估算/预算" width="140">
            <template #default="{ row }">
              <span :style="row.est_chars > row.budget_chars ? 'color:#f5222d' : ''">
                {{ row.est_chars }} / {{ row.budget_chars }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-alert v-if="overbudget.length" :closable="false" type="error"
          :title="`超预算叶子：${overbudget.join('、')} —— 阻断进入编排计划`" style="margin-top:10px" />
        <div style="margin-top:12px">
          <el-button @click="step = 1">上一步</el-button>
          <el-button type="primary" :disabled="!!overbudget.length" @click="step = 3; buildPlan()">下一步：生成编排计划</el-button>
        </div>
      </template>
      <el-empty v-else description="点击右上角「执行拆分」生成任务清单（tasks.json）" />
    </el-card>

    <!-- ④ 编排计划 -->
    <el-card v-show="step === 3" shadow="never">
      <template #header><b>编排计划预览（可调序 · 预算预演 · 确认后执行）</b></template>
      <template v-if="plan">
        <el-table :data="plan.rings" size="small">
          <el-table-column label="调整" width="90">
            <template #default="{ row }">
              <el-button size="small" text @click="adjustSeq(row, -1)">↑</el-button>
              <el-button size="small" text @click="adjustSeq(row, 1)">↓</el-button>
            </template>
          </el-table-column>
          <el-table-column prop="ring_id" label="环" width="80" />
          <el-table-column label="任务×模块" min-width="180">
            <template #default="{ row }">{{ row.task }} · {{ MODULE_NAMES[row.module] || row.module }}</template>
          </el-table-column>
          <el-table-column prop="parallel_group" label="并行组" width="90" />
          <el-table-column label="依赖环" min-width="140">
            <template #default="{ row }">{{ (row.depends_rings || []).join('、') || '—' }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
        <div style="margin-top:12px">
          <el-button @click="step = 2">上一步</el-button>
          <el-button type="primary" :disabled="plan.status === 'running'" @click="confirmPlan">
            确认并执行（外发确认）
          </el-button>
        </div>
      </template>
      <el-empty v-else description="尚未生成编排计划" />
    </el-card>

    <!-- ⑤ 环执行 -->
    <el-card v-show="step === 4" shadow="never">
      <template #header>
        <div style="display:flex; align-items:center">
          <b>环执行监控（落盘即推进 · 定稿不阻断）</b>
          <div style="flex:1" />
          <el-tag v-if="plan">{{ plan.status }}</el-tag>
          <el-button size="small" style="margin-left:10px" @click="pausePlan" :disabled="plan?.status !== 'running'">暂停</el-button>
          <el-button size="small" type="primary" style="margin-left:8px" @click="resumePlan"
            :disabled="!['suspended', 'paused'].includes(plan?.status)">从断点恢复</el-button>
        </div>
      </template>
      <el-alert v-if="resumeHint && plan?.status === 'suspended'" type="error" :closable="false"
        :title="`挂起：${resumeHint}`" style="margin-bottom:10px" />
      <el-progress v-if="plan" :percentage="Math.round((plan.done_rings / Math.max(1, plan.total_rings)) * 100)"
        style="margin-bottom:12px" />
      <div class="ring-log">
        <div v-for="(l, i) in ringLog" :key="i" class="log-line">{{ l }}</div>
        <div v-if="!ringLog.length" style="color:#909399">等待环事件（SSE 实时推送）…</div>
      </div>
    </el-card>

    <!-- ⑥ 提示词管理 -->
    <el-card v-show="step === 5" shadow="never">
      <template #header>
        <div style="display:flex; align-items:center">
          <b>提示词管理（预览 → 微调 → 定稿；未定稿不可归档导出）</b>
          <div style="flex:1" />
          <el-button size="small" @click="refreshPrompts">刷新</el-button>
        </div>
      </template>
      <div style="display:flex; gap:14px">
        <el-table :data="prompts" size="small" style="width:340px" highlight-current-row
          @current-change="openPrompt">
          <el-table-column label="提示词" min-width="200">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'final' ? 'success' : 'warning'" style="margin-right:6px">
                {{ row.status === 'final' ? '定稿' : '草稿' }}
              </el-tag>v{{ row.version }} · {{ row.task_id }}·{{ MODULE_NAMES[row.module] || row.module }}
            </template>
          </el-table-column>
        </el-table>
        <div style="flex:1" v-if="currentPrompt">
          <el-tabs v-model="promptTab">
            <el-tab-pane label="预览" name="preview">
              <MarkdownView :source="currentPrompt.body" style="max-height:420px; overflow:auto" />
              <div style="color:#909399; font-size:12px; margin-top:6px">
                gen_params：{{ JSON.stringify(currentPrompt.gen_params) }}
              </div>
            </el-tab-pane>
            <el-tab-pane label="编辑（微调生成新草稿版）" name="edit">
              <el-input v-model="editingBody" type="textarea" :rows="16" />
              <div style="margin-top:8px; display:flex; gap:8px">
                <el-button type="primary" size="small" @click="saveEdit">保存新版本</el-button>
                <el-button size="small" @click="copyPrompt">复制</el-button>
                <el-button size="small" type="success" :disabled="currentPrompt.status === 'final'" @click="finalize">
                  定稿
                </el-button>
                <el-button v-if="currentPrompt.status === 'final'" size="small" type="warning" @click="regenHandoff">
                  一键重生成摘要（AC-16）
                </el-button>
              </div>
            </el-tab-pane>
          </el-tabs>
        </div>
        <el-empty v-else description="从左侧选择一条提示词" style="flex:1" />
      </div>
    </el-card>

    <OutboundConfirmDialog ref="confirmRef" />
  </div>
</template>

<style scoped>
.ring-log { background: #0b1021; color: #d6e2ff; border-radius: 6px; padding: 12px; font-family: Consolas, monospace; font-size: 12px; max-height: 300px; overflow: auto; }
.log-line { line-height: 1.8; }
</style>
