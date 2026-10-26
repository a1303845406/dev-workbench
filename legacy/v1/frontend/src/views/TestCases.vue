<script setup lang="ts">
/** 测试用例视图（FR-05）：任务多选 + 性能开关 + 用例集展示。 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, post } from '../api'
import { makeOutbound } from '../api'
import MarkdownView from '../components/MarkdownView.vue'
import OutboundConfirmDialog from '../components/OutboundConfirmDialog.vue'

const route = useRoute()
const pid = route.params.pid as string
const confirmRef = ref()
const tasks = ref<any[]>([])
const picked = ref<string[]>([])
const performance = ref(false)
const cases = ref<any[]>([])
const current = ref<any>(null)
const generating = ref(false)

async function load() {
  try {
    tasks.value = (await get(`/api/projects/${encodeURIComponent(pid)}/tasks`)).tasks
  } catch { tasks.value = [] }
  cases.value = (await get(`/api/projects/${encodeURIComponent(pid)}/testcases`)).testcases
  if (cases.value.length && !current.value) current.value = cases.value[0]
}
onMounted(load)

async function generate() {
  if (!picked.value.length) { ElMessage.warning('请选择任务'); return }
  const conf = await confirmRef.value.open([`pipeline/tasks.json`], 3000)
  if (!conf) return
  generating.value = true
  try {
    const r = await post(`/api/projects/${encodeURIComponent(pid)}/testcases`, {
      task_ids: picked.value, performance: performance.value, outbound_confirmation: conf
    })
    for (const res of r.results) {
      for (const w of res.warnings || []) ElMessage.warning(w)
    }
    ElMessage.success('用例集已生成并归档')
    picked.value = []
    await load()
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
  finally { generating.value = false }
}
</script>

<template>
  <div style="padding:16px 22px; height:100%; display:flex; flex-direction:column; gap:12px; overflow:auto">
    <el-card shadow="never">
      <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap">
        <el-select v-model="picked" multiple placeholder="选择任务（叶子）" style="min-width:320px">
          <el-option v-for="t in tasks" :key="t.id" :value="t.id" :label="`${t.id} ${t.title}`" />
        </el-select>
        <el-switch v-model="performance" active-text="含性能用例（TC-P）" />
        <el-button type="primary" :loading="generating" @click="generate">生成用例集（外发确认）</el-button>
        <el-tag v-if="!performance" type="info" size="small">性能开关关闭时不会出现 TC-P-*（AC-05）</el-tag>
      </div>
    </el-card>

    <div style="display:flex; gap:14px; flex:1; min-height:0">
      <el-card shadow="never" style="width:330px; overflow:auto">
        <template #header><b>用例集（按任务归档）</b></template>
        <div v-for="c in cases" :key="c.path" class="case-item" :class="{ active: current?.path === c.path }"
          @click="current = c">
          <b>{{ c.task_id }}</b>
          <el-tag size="small" style="margin-left:6px">{{ (c.includes?.performance ? '含性能' : '功能+业务') }}</el-tag>
          <div style="color:#909399; font-size:12px">{{ c.file || c.path }}</div>
        </div>
        <el-empty v-if="!cases.length" description="暂无用例集" :image-size="60" />
      </el-card>
      <el-card shadow="never" style="flex:1; overflow:auto">
        <template #header><b>{{ current?.task_id }} 用例内容</b></template>
        <MarkdownView v-if="current" :source="current.body" />
        <el-empty v-else description="选择左侧用例集查看" :image-size="60" />
      </el-card>
    </div>
    <OutboundConfirmDialog ref="confirmRef" />
  </div>
</template>

<style scoped>
.case-item { padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 6px; border: 1px solid transparent; }
.case-item:hover { background: #f5f7fa; }
.case-item.active { border-color: #2f54eb; background: #ecf2ff; }
</style>
