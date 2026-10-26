<script setup lang="ts">
/** 项目规整工作区（FR-03）：路径白名单 + 扫描 + 结构画像 + 分级建议。 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { get, patch, post } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const pid = route.params.pid as string

const scanRoots = ref<string[]>([])
const target = ref('')
const maxDepth = ref(12)
const maxEntries = ref(20000)
const scanning = ref(false)
const summary = ref<any>(null)
const analysis = ref<Record<string, string>>({})
const newRoot = ref('')

onMounted(async () => {
  const p = await get(`/api/projects/${encodeURIComponent(pid)}`)
  scanRoots.value = p.scan_roots || []
  await loadAnalysis()
})

async function addRoot() {
  if (!newRoot.value.trim()) return
  scanRoots.value.push(newRoot.value.trim())
  await patch(`/api/projects/${encodeURIComponent(pid)}`, { scan_roots: scanRoots.value })
  newRoot.value = ''
  ElMessage.success('白名单已更新（NFR-04）')
}

async function removeRoot(r: string) {
  scanRoots.value = scanRoots.value.filter(x => x !== r)
  await patch(`/api/projects/${encodeURIComponent(pid)}`, { scan_roots: scanRoots.value })
}

async function runScan() {
  if (!target.value) { ElMessage.warning('请输入要扫描的同机路径'); return }
  scanning.value = true
  try {
    summary.value = await post(`/api/projects/${encodeURIComponent(pid)}/scan`, {
      target: target.value, max_depth: maxDepth.value, max_entries: maxEntries.value
    })
    await loadAnalysis()
    ElMessage.success('扫描完成，报告已归档至 analysis/')
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
  } finally { scanning.value = false }
}

async function loadAnalysis() {
  try { analysis.value = await get(`/api/projects/${encodeURIComponent(pid)}/analysis`) }
  catch { analysis.value = {} }
}
</script>

<template>
  <div style="padding:16px 22px; overflow:auto; height:100%">
    <h3 style="margin-top:0">项目规整（FR-03 · 只读扫描，不修改源文件 CON-01）</h3>

    <el-card shadow="never" style="margin-bottom:14px">
      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
        <el-input v-model="target" placeholder="同机目录路径，如 D:\code\legacy-project" style="width:420px" />
        <el-input-number v-model="maxDepth" :min="1" :max="32" />
        <span style="color:#909399; font-size:12px">深度上限</span>
        <el-input-number v-model="maxEntries" :min="100" :max="200000" :step="5000" />
        <span style="color:#909399; font-size:12px">条数上限</span>
        <el-button type="primary" :loading="scanning" @click="runScan">开始扫描</el-button>
      </div>
      <div style="margin-top:10px">
        <b style="font-size:13px">扫描白名单 scan_roots：</b>
        <el-tag v-for="r in scanRoots" :key="r" closable size="small" style="margin-right:6px"
          @close="removeRoot(r)">{{ r }}</el-tag>
        <el-input v-model="newRoot" size="small" placeholder="添加允许扫描的根目录" style="width:280px" />
        <el-button size="small" @click="addRoot">登记</el-button>
      </div>
    </el-card>

    <el-row v-if="summary" :gutter="14">
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><b>扫描概要</b></template>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="扫描根">{{ summary.root }}</el-descriptions-item>
            <el-descriptions-item label="文件数">{{ summary.files_done }}
              <el-tag v-if="summary.truncated" size="small" type="warning">已截断，可续扫</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="扫描时间">{{ summary.scanned_at }}</el-descriptions-item>
          </el-descriptions>
          <div style="margin-top:10px"><b>语言构成</b></div>
          <el-table :data="Object.entries(summary.languages || {}).map(([k, v]) => ({ lang: k, n: v }))"
            size="small" style="margin-top:6px">
            <el-table-column prop="lang" label="语言" />
            <el-table-column prop="n" label="文件数" width="90" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="16">
        <el-tabs>
          <el-tab-pane label="结构画像"><MarkdownView :source="analysis['结构画像.md'] || '（尚未扫描）'" /></el-tab-pane>
          <el-tab-pane label="规整建议">
            <el-alert type="info" :closable="false" style="margin-bottom:8px"
              title="绿=自动归位（低风险，交人工/外部 IDE 执行）；橙=需人工决策（仅列事实与风险）" />
            <MarkdownView :source="analysis['规整建议.md'] || '（尚未扫描）'" />
          </el-tab-pane>
        </el-tabs>
      </el-col>
    </el-row>
    <el-empty v-else description="输入路径开始首次扫描（数据不上传，仅本机分析）" />
  </div>
</template>
