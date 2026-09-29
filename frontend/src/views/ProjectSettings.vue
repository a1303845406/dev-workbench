<script setup lang="ts">
/** 项目设置：rules.md 编辑（冲突告警 AC-17）、scan_roots、删除项目。 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, patch } from '../api'

const route = useRoute()
const router = useRouter()
const pid = route.params.pid as string

const project = ref<any>(null)
const rules = ref('')
const conflict = ref<any>(null)
const scanRoots = ref<string[]>([])
const newRoot = ref('')
const provider = ref('')

onMounted(async () => {
  project.value = await get(`/api/projects/${encodeURIComponent(pid)}`)
  rules.value = (await get(`/api/projects/${encodeURIComponent(pid)}/rules`)).content
  scanRoots.value = project.value.scan_roots || []
  provider.value = project.value.provider || ''
})

async function saveRules() {
  const r = await patch(`/api/projects/${encodeURIComponent(pid)}/rules`, { content: rules.value })
  conflict.value = r.conflict_warning
  if (r.conflict_warning) {
    ElMessage.warning(`检测到 ${r.conflict_warning.hits.length} 处放宽性表述（警示不阻断，2.9.3-2）`)
  } else {
    ElMessage.success('工程规则已保存（自动注入每个提示词）')
  }
}

async function addRoot() {
  if (!newRoot.value.trim()) return
  scanRoots.value.push(newRoot.value.trim())
  await patch(`/api/projects/${encodeURIComponent(pid)}`, { scan_roots: scanRoots.value })
  newRoot.value = ''
  ElMessage.success('已登记扫描白名单')
}

async function removeRoot(r: string) {
  scanRoots.value = scanRoots.value.filter(x => x !== r)
  await patch(`/api/projects/${encodeURIComponent(pid)}`, { scan_roots: scanRoots.value })
}

async function saveProvider() {
  await patch(`/api/projects/${encodeURIComponent(pid)}`, { provider: provider.value })
  ElMessage.success('Provider 已更新')
}

async function removeProject() {
  try {
    const { value } = await ElMessageBox.prompt(
      `请输入项目名「${pid}」以确认删除（数据将直接移除，依赖备份恢复 S-01）：`, '删除项目',
      { inputPattern: new RegExp(`^${pid}$`), inputErrorMessage: '输入不一致' })
    await del(`/api/projects/${encodeURIComponent(pid)}`, { confirm_name: value })
    ElMessage.success('项目已删除')
    router.push({ name: 'projects' })
  } catch { /* cancelled */ }
}
</script>

<template>
  <div style="padding:16px 22px; overflow:auto; height:100%">
    <h3 style="margin-top:0">项目设置</h3>
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>工程规则 rules.md（第 2 层 · 自动注入）</b>
              <div style="flex:1" />
              <el-button size="small" type="primary" @click="saveRules">保存</el-button>
            </div>
          </template>
          <el-alert v-if="conflict" type="warning" :closable="false" style="margin-bottom:10px"
            title="RULES_CONFLICT_WARNING：以下内容疑似放宽通用硬约束（仅供人工兜底审查，已正常保存）">
            <div v-for="h in conflict.hits" :key="h.line" style="font-size:12px">
              第 {{ h.line }} 行命中「{{ h.keyword }}」：{{ h.excerpt }}
            </div>
          </el-alert>
          <el-input v-model="rules" type="textarea" :rows="18" />
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>扫描白名单 scan_roots（NFR-04）</b></template>
          <el-tag v-for="r in scanRoots" :key="r" closable style="margin:0 6px 6px 0" @close="removeRoot(r)">{{ r }}</el-tag>
          <div style="margin-top:8px; display:flex; gap:8px">
            <el-input v-model="newRoot" size="small" placeholder="D:\code\some-root" />
            <el-button size="small" @click="addRoot">登记</el-button>
          </div>
        </el-card>
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>默认 Provider（仅存名称，Key 走环境变量 G-03）</b></template>
          <div style="display:flex; gap:8px">
            <el-input v-model="provider" size="small" />
            <el-button size="small" @click="saveProvider">保存</el-button>
          </div>
        </el-card>
        <el-card shadow="never">
          <template #header><b style="color:#f5222d">危险区</b></template>
          <el-button type="danger" @click="removeProject">删除项目（强确认）</el-button>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
