<script setup lang="ts">
/** 需求探索工作区（FR-01）：七阶段步骤条 + 对话流 + 底稿侧栏 + 确认门。 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, patch, post, type OutboundConf } from '../api'
import { onSse } from '../sse'
import MarkdownView from '../components/MarkdownView.vue'
import OutboundConfirmDialog from '../components/OutboundConfirmDialog.vue'

const route = useRoute()
const pid = route.params.pid as string

const pipelines = ref<any[]>([])
const plid = ref('')
const detail = ref<any>(null)
const input = ref('')
const sending = ref(false)
const streaming = ref(false)
const streamText = ref('')
const lastReply = ref('')
const draftEditing = ref(false)
const draftText = ref('')
const comment = ref('')
const confirmRef = ref()

const currentStage = computed(() => detail.value?.stage || 'S1')
const stages = computed(() => detail.value?.stages || [])
const currentStageInfo = computed(() => stages.value.find((s: any) => s.stage === currentStage.value))
const freeze = computed(() => detail.value?.freeze || {})

async function loadPipelines() {
  pipelines.value = (await get(`/api/projects/${encodeURIComponent(pid)}/pipelines`)).pipelines
  if (pipelines.value.length && !plid.value) plid.value = pipelines.value[0].id
  await loadDetail()
}

async function loadDetail() {
  if (!plid.value) { detail.value = null; return }
  detail.value = await get(`/api/pipelines/${plid.value}`)
}
onMounted(loadPipelines)

const offSse = onSse((event, data) => {
  if (data?.plid && data.plid !== plid.value) return
  if (event === 'pipeline.round_delta') {
    streaming.value = true
    streamText.value += data.delta || ''
  } else if (event === 'pipeline.round_done') {
    streaming.value = false
    streamText.value = ''
    loadDetail()
  }
})
onUnmounted(offSse)

async function newPipeline() {
  const res = await post(`/api/projects/${encodeURIComponent(pid)}/pipelines`, {})
  plid.value = res.id
  await loadPipelines()
  ElMessage.success('流水线已创建（断点续跑：状态已落盘）')
}

async function send() {
  if (!input.value.trim()) return
  const est = (detail.value?.draft?.length || 0) + input.value.length
  const conf: OutboundConf | null = await confirmRef.value.open(['docs/需求/滚动底稿.md'], est)
  if (!conf) return
  sending.value = true
  streamText.value = ''
  try {
    const r = await post(`/api/pipelines/${plid.value}/chat`, {
      stage: currentStage.value, user_input: input.value,
      outbound_confirmation: conf, stream: true
    })
    lastReply.value = r.reply_markdown
    input.value = ''
    await loadDetail()
    ElMessage.success(`第 ${r.round} 轮完成，已落盘（FR-06）`)
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
  } finally {
    sending.value = false
    streaming.value = false
  }
}

async function doConfirm(part?: 'business' | 'scope') {
  try {
    const r = await post(`/api/pipelines/${plid.value}/confirm`, {
      stage: currentStage.value, comment: comment.value, part
    })
    comment.value = ''
    await loadDetail()
    ElMessage.success(r.message || (r.next ? `已确认，进入 ${r.next}` : '已确认，流水线完成'))
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
  }
}

async function skipS5() {
  try {
    await post(`/api/pipelines/${plid.value}/skip-s5`)
    await loadDetail()
    ElMessage.success('已显式跳过范围冻结，进入 S6')
  } catch (e: any) { ElMessage.error(e.message) }
}

async function saveDraft() {
  // 底稿人工可读可改：直接整文件写回（以文件为准）
  const r = await fetch(`/api/projects/${encodeURIComponent(pid)}/docs/content?path=${encodeURIComponent('docs/需求/滚动底稿.md')}`)
  const cur = await r.json()
  void cur
  await patch(`/api/projects/${encodeURIComponent(pid)}/docs/raw`, { path: 'docs/需求/滚动底稿.md', content: draftText.value })
  draftEditing.value = false
  await loadDetail()
  ElMessage.success('底稿已保存（人工修订合法）')
}

function startEditDraft() {
  draftText.value = detail.value?.draft || ''
  draftEditing.value = true
}
</script>

<template>
  <div style="display:flex; height:100%">
    <!-- 左列：七阶段步骤条 -->
    <div class="col stage-col">
      <div style="display:flex; align-items:center; margin-bottom:10px">
        <b>流水线</b><div style="flex:1" />
        <el-button size="small" @click="newPipeline">＋ 新建</el-button>
      </div>
      <el-select v-model="plid" size="small" style="margin-bottom:12px" @change="loadDetail">
        <el-option v-for="p in pipelines" :key="p.id" :value="p.id"
          :label="`${p.title}（${p.stage}·${p.status}）`" />
      </el-select>
      <div v-if="detail">
        <div v-for="s in stages" :key="s.stage" class="stage" :class="{ current: s.current, dim: !s.current }">
          <div style="display:flex; align-items:center; gap:8px">
            <b>{{ s.stage }}</b><span>{{ s.name }}</span>
            <el-tag v-if="s.skippable" size="small" type="info">可跳过</el-tag>
            <div style="flex:1" />
            <el-tag size="small" :type="s.status === 'confirmed' ? 'success' : s.status === 'skipped' ? 'info' : s.status === 'not_started' ? 'info' : 'warning'">
              {{ s.status === 'confirmed' ? '已确认' : s.status === 'skipped' ? '已跳过' : s.status === 'not_started' ? '未开始' : '草稿' }}
            </el-tag>
          </div>
        </div>
      </div>
      <el-alert v-if="detail?.status === 'done'" type="success" :closable="false"
        title="流水线已完成（S7 结束）" style="margin-top:12px" />
    </div>

    <!-- 中列：对话流 -->
    <div class="col chat-col">
      <template v-if="detail">
        <div class="chat-head">
          <b>{{ currentStage }} · {{ currentStageInfo?.name }}</b>
          <el-tag size="small" style="margin-left:8px">确认门：未确认不能推进（AC-01）</el-tag>
        </div>
        <div class="chat-body">
          <div v-if="lastReply" class="bubble model">
            <MarkdownView :source="lastReply" />
          </div>
          <div v-if="streaming" class="bubble model streaming">
            <span class="breath">●</span> 生成中…
            <pre style="white-space:pre-wrap">{{ streamText }}</pre>
          </div>
          <div v-if="!lastReply && !streaming" class="empty-hint">
            在下方输入本轮内容并回车发起（每轮为无状态调用：角色层 + 底稿快照 + 本轮输入）
          </div>
        </div>

        <!-- 确认门 -->
        <div class="confirm-bar" v-if="currentStage !== 'S7' || true">
          <el-input v-model="comment" placeholder="确认意见（可空，将记入阶段记录）" size="small" />
          <template v-if="currentStage === 'S5' && !freeze.frozen_business">
            <el-button type="primary" size="small" @click="doConfirm('business')">冻结业务范围</el-button>
            <el-button size="small" @click="skipS5">跳过 S5（显式）</el-button>
          </template>
          <template v-else-if="currentStage === 'S5' && !freeze.frozen_scope">
            <el-button type="primary" size="small" @click="doConfirm('scope')">冻结功能清单</el-button>
          </template>
          <template v-else>
            <el-button type="primary" size="small" :disabled="currentStageInfo?.status === 'not_started'"
              @click="doConfirm()">确认并推进</el-button>
          </template>
        </div>

        <div class="input-bar">
          <el-input v-model="input" type="textarea" :rows="2" :disabled="sending"
            placeholder="本轮用户输入…" @keydown.ctrl.enter="send" />
          <el-button type="primary" :loading="sending" @click="send">发送</el-button>
        </div>
      </template>
      <el-empty v-else description="尚未创建需求探索流水线">
        <el-button type="primary" @click="newPipeline">＋ 新建流水线</el-button>
      </el-empty>
    </div>

    <!-- 右列：滚动底稿 -->
    <div class="col draft-col">
      <div style="display:flex; align-items:center; margin-bottom:8px">
        <b>滚动工作底稿（六区）</b><div style="flex:1" />
        <el-button size="small" @click="startEditDraft">编辑</el-button>
      </div>
      <template v-if="!draftEditing">
        <MarkdownView :source="detail?.draft || '（暂无底稿）'" style="overflow:auto; flex:1" />
      </template>
      <template v-else>
        <el-input v-model="draftText" type="textarea" :rows="24" style="flex:1" />
        <div style="margin-top:8px; display:flex; gap:8px">
          <el-button type="primary" size="small" @click="saveDraft">保存（整文件原子写回）</el-button>
          <el-button size="small" @click="draftEditing = false">取消</el-button>
        </div>
      </template>
    </div>
    <OutboundConfirmDialog ref="confirmRef" />
  </div>
</template>

<style scoped>
.col { padding: 14px; overflow: auto; }
.stage-col { width: 230px; border-right: 1px solid #e4e7ed; background: #fafbfc; }
.chat-col { flex: 1; display: flex; flex-direction: column; }
.draft-col { width: 380px; border-left: 1px solid #e4e7ed; display: flex; flex-direction: column; }
.stage { padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; background: #fff; border: 1px solid #ebeef5; }
.stage.current { border-color: #2f54eb; background: #ecf2ff; }
.stage.dim { opacity: .72; }
.chat-head { padding-bottom: 10px; border-bottom: 1px solid #ebeef5; margin-bottom: 10px; }
.chat-body { flex: 1; overflow: auto; }
.bubble { background: #f5f7fa; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }
.bubble.streaming { border: 1px dashed #2f54eb; background: #ecf2ff; }
.breath { animation: breath 1.2s infinite; color: #2f54eb; }
@keyframes breath { 0%,100% { opacity: .3 } 50% { opacity: 1 } }
.empty-hint { color: #909399; padding: 30px; text-align: center; }
.confirm-bar { display: flex; gap: 8px; padding-top: 10px; border-top: 1px solid #ebeef5; }
.input-bar { display: flex; gap: 8px; margin-top: 10px; align-items: flex-end; }
</style>
