<script setup lang="ts">
/** 资产库（FR-07/FR-08）：版本时间线、两版对比、回滚、反馈、导出。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const pid = route.params.pid as string

const prompts = ref<any[]>([])
const feedback = ref('')
const current = ref<any>(null)
const versions = ref<any[]>([])
const tab = ref('detail')
const diff = ref<any>(null)
const v1 = ref<number | null>(null)
const v2 = ref<number | null>(null)

const FORMATS = [
  { key: 'spec-kit', name: 'Spec Kit（spec/plan/tasks）' },
  { key: 'taskmaster', name: 'Taskmaster（PRD/tasks.json）' },
  { key: 'openspec', name: 'OpenSpec（存量改造）' },
  { key: 'markdown', name: '通用 Markdown' }
]
const format = ref('markdown')
const scope = ref({ requirements: true, tasks: true, prompts: false })
const exportsList = ref<any[]>([])

const sameFamily = computed(() => {
  // group prompts of same module+task for version compare/rollback
  if (!current.value) return []
  return prompts.value.filter(p => p.module === current.value.module && p.task_id === current.value.task_id)
})

async function load() {
  prompts.value = (await get(`/api/projects/${encodeURIComponent(pid)}/prompts`)).prompts
  feedback.value = (await get(`/api/projects/${encodeURIComponent(pid)}/feedback`)).content
  exportsList.value = (await get(`/api/projects/${encodeURIComponent(pid)}/exports`)).exports
  if (prompts.value.length && !current.value) pick(prompts.value[0])
}

async function pick(p: any) {
  current.value = await get(`/api/prompts/${p.prompt_id}?pid=${encodeURIComponent(pid)}`)
  versions.value = sameFamily.value
  diff.value = null
  tab.value = 'detail'
}

async function runDiff() {
  if (!v1.value || !v2.value) { ElMessage.warning('请选择两个版本'); return }
  diff.value = await get(`/api/prompts/${current.value.prompt_id}/diff?pid=${encodeURIComponent(pid)}`,
    { v1: v1.value, v2: v2.value })
}

async function rollback(v: number) {
  await post(`/api/prompts/${current.value.prompt_id}/rollback?pid=${encodeURIComponent(pid)}`, { to_version: v })
  ElMessage.success(`已回滚：以 v${v} 内容生成新版本（历史保留，AC-07）`)
  await load()
  const np = prompts.value.find(p => p.module === current.value.module && p.task_id === current.value.task_id
    && p.version === Math.max(...versions.value.map(x => x.version)) + 1)
  if (np) await pick(np)
}

async function addFeedback(result: string) {
  let note = ''
  if (result === '失败') {
    note = prompt('备注（返工原因）：') || ''
  }
  await post(`/api/prompts/${current.value.prompt_id}/feedback?pid=${encodeURIComponent(pid)}`,
    { result, note })
  feedback.value = (await get(`/api/projects/${encodeURIComponent(pid)}/feedback`)).content
  ElMessage.success('反馈已记录（G6）')
}

async function doExport() {
  try {
    const r = await post(`/api/projects/${encodeURIComponent(pid)}/exports`,
      { format: format.value, scope: scope.value })
    if (r.pending_confirm) {
      ElMessage.warning(`脱敏命中 ${r.pending_confirm.length} 项需逐条确认，请到项目设置查看规则或调整内容后重试`)
      return
    }
    ElMessage.success(`导出完成：${r.export_id}（已过脱敏门）`)
    await load()
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
  }
}

onMounted(load)
</script>

<template>
  <div style="padding:16px 22px; height:100%; display:flex; flex-direction:column; gap:12px; overflow:auto">
    <div style="display:flex; gap:14px; flex:1; min-height:0">
      <!-- 左树 -->
      <el-card shadow="never" style="width:330px; overflow:auto">
        <template #header><b>提示词库（模块 → 任务 → 版本）</b></template>
        <div v-for="p in prompts" :key="p.prompt_id" class="tree-item" :class="{ active: current?.prompt_id === p.prompt_id }"
          @click="pick(p)">
          <el-tag size="small" :type="p.status === 'final' ? 'success' : 'warning'">
            {{ p.status === 'final' ? '定稿' : '草稿' }}
          </el-tag>
          <span style="margin-left:6px">{{ p.task_id }} · {{ p.module }} · v{{ p.version }}</span>
        </div>
        <el-empty v-if="!prompts.length" description="暂无提示词" :image-size="60" />
      </el-card>

      <!-- 详情 -->
      <el-card shadow="never" style="flex:1; overflow:auto">
        <template #header>
          <div style="display:flex; align-items:center" v-if="current">
            <b>{{ current.prompt_id }}</b>
            <div style="flex:1" />
            <el-button size="small" type="danger" plain @click="addFeedback('失败')">记失败</el-button>
            <el-button size="small" type="success" plain @click="addFeedback('成功')">记成功</el-button>
          </div>
        </template>
        <el-tabs v-model="tab">
          <el-tab-pane label="内容" name="detail">
            <MarkdownView v-if="current" :source="current.body" />
          </el-tab-pane>
          <el-tab-pane label="版本对比 / 回滚" name="versions">
            <div style="display:flex; gap:10px; align-items:center; margin-bottom:10px">
              <el-select v-model="v1" placeholder="v1" style="width:100px">
                <el-option v-for="v in versions" :key="v.prompt_id" :value="v.version" :label="'v' + v.version" />
              </el-select>
              <span>↔</span>
              <el-select v-model="v2" placeholder="v2" style="width:100px">
                <el-option v-for="v in versions" :key="v.prompt_id" :value="v.version" :label="'v' + v.version" />
              </el-select>
              <el-button size="small" @click="runDiff">对比</el-button>
            </div>
            <div v-for="v in versions" :key="v.prompt_id" style="margin-bottom:6px">
              <el-tag size="small" :type="v.status === 'final' ? 'success' : 'warning'">v{{ v.version }}·{{ v.status }}</el-tag>
              <span style="margin:0 8px">{{ (v.generated_at || '').slice(0, 19).replace('T', ' ') }}</span>
              <el-button size="small" text type="primary" @click="rollback(v.version)">回滚到此版</el-button>
            </div>
            <div v-if="diff" class="diff">
              <div class="diff-col">
                <div v-for="(l, i) in diff.left" :key="i" :class="{ del: l.startsWith('-') }">{{ l || ' ' }}</div>
              </div>
              <div class="diff-col">
                <div v-for="(l, i) in diff.right" :key="i" :class="{ add: l.startsWith('+') }">{{ l || ' ' }}</div>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="执行反馈（G6）" name="feedback">
            <pre class="feedback">{{ feedback || '（暂无反馈记录）' }}</pre>
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <!-- 导出 -->
      <el-card shadow="never" style="width:300px; overflow:auto">
        <template #header><b>适配导出（FR-08 · 过脱敏门）</b></template>
        <el-select v-model="format" style="width:100%; margin-bottom:10px">
          <el-option v-for="f in FORMATS" :key="f.key" :value="f.key" :label="f.name" />
        </el-select>
        <div style="margin-bottom:10px">
          <el-checkbox v-model="scope.requirements">需求文档</el-checkbox>
          <el-checkbox v-model="scope.tasks">任务清单</el-checkbox>
          <el-checkbox v-model="scope.prompts">已定稿提示词</el-checkbox>
        </div>
        <el-button type="primary" style="width:100%" @click="doExport">生成导出</el-button>
        <div style="margin-top:12px">
          <div v-for="e in exportsList" :key="e.export_id" style="margin-bottom:8px">
            <el-link type="primary" :href="`/api/projects/${encodeURIComponent(pid)}/exports/${e.export_id}/download`">
              ⬇ {{ e.export_id }}
            </el-link>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.tree-item { padding: 7px 9px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; border: 1px solid transparent; }
.tree-item:hover { background: #f5f7fa; }
.tree-item.active { border-color: #2f54eb; background: #ecf2ff; }
.diff { display: flex; gap: 8px; background: #fafbfc; border: 1px solid #ebeef5; border-radius: 6px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; }
.diff-col { flex: 1; white-space: pre-wrap; }
.del { background: #fff1f0; color: #cf1322; }
.add { background: #f6ffed; color: #389e0d; }
.feedback { background: #f5f7fa; padding: 12px; border-radius: 6px; font-size: 12px; white-space: pre-wrap; }
</style>
