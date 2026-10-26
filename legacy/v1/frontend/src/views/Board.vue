<script setup lang="ts">
/** 任务看板（G5）：待投喂/已投喂/已完成/失败 四列，流转写 feedback（G6）。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, patch } from '../api'

const route = useRoute()
const pid = route.params.pid as string
const kanban = ref<any>(null)

const MODULE_NAMES: Record<string, string> = {
  requirement: '需求', 'design-outline': '概设', prototype: '原型', 'detailed-design': '详设',
  database: '数据库设计', 'development-backend': '后端开发', 'development-frontend': '前端开发',
  test: '测试', regression: '回归', acceptance: '验收'
}

async function load() {
  try { kanban.value = await get(`/api/projects/${encodeURIComponent(pid)}/kanban`) }
  catch { kanban.value = null }
}
onMounted(load)

const progress = computed(() =>
  kanban.value ? Math.round((kanban.value.progress.done / Math.max(1, kanban.value.progress.total)) * 100) : 0)

async function move(task: any, status: string) {
  let note = ''
  if (status === 'failed') {
    try {
      const r = await ElMessageBox.prompt('失败必填备注（返工原因，写入 feedback.md G6）：', '标记失败',
        { inputPattern: /\S+/, inputErrorMessage: '备注不能为空' })
      note = r.value
    } catch { return }
  }
  await patch(`/api/projects/${encodeURIComponent(pid)}/tasks/${task.id}/status`, { status, note })
  ElMessage.success(`已流转：${task.id} → ${status}`)
  await load()
}
</script>

<template>
  <div style="padding:16px 22px; height:100%; overflow:auto">
    <div style="display:flex; align-items:center; margin-bottom:12px">
      <h3 style="margin:0">任务看板（G5 执行跟踪）</h3>
      <div style="flex:1" />
      <el-progress :percentage="progress" style="width:240px" />
    </div>
    <el-empty v-if="!kanban || !kanban.progress.total"
      description="尚未生成任务：先到「提示词工程」完成 ①~③ 步" />
    <div v-else class="board">
      <div v-for="col in kanban.columns" :key="col.key" class="kanban-col">
        <div class="col-head">
          <b>{{ col.name }}</b>
          <el-tag size="small">{{ col.tasks.length }}</el-tag>
        </div>
        <div v-for="t in col.tasks" :key="t.id" class="card">
          <div style="display:flex; gap:6px; align-items:center">
            <el-tag size="small">{{ MODULE_NAMES[t.module] || t.module }}</el-tag>
            <b>{{ t.id }}</b>
            <el-tag v-if="t.parent_id" size="small" type="info">子</el-tag>
          </div>
          <div style="margin:6px 0; font-size:13px">{{ t.title }}</div>
          <div class="ops">
            <el-button v-if="t.status === 'pending'" size="small" @click="move(t, 'fed')">投喂</el-button>
            <el-button v-if="t.status === 'fed'" size="small" type="success" @click="move(t, 'done')">完成</el-button>
            <el-button v-if="t.status !== 'failed'" size="small" type="danger" plain @click="move(t, 'failed')">失败</el-button>
            <el-button v-if="t.status === 'failed'" size="small" @click="move(t, 'pending')">重开</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.board { display: flex; gap: 12px; }
.kanban-col { flex: 1; background: #f5f7fa; border-radius: 8px; padding: 10px; min-height: 300px; }
.col-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.card { background: #fff; border-radius: 6px; padding: 10px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.ops { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
