<script setup lang="ts">
/** 系统设置（只读 S-02）：Provider 脱敏、模板、映射、约束包、脱敏规则、备份。 */
import { onMounted, ref } from 'vue'
import { get, post } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const s = ref<any>(null)
const backupRunning = ref(false)

onMounted(async () => { s.value = await get('/api/settings') })

async function runBackup() {
  backupRunning.value = true
  try {
    const r = await post('/api/backups')
    ElMessageSilent(`快照完成：${r.files} 个文件${r.skipped_key_hits ? `，跳过 ${r.skipped_key_hits} 个疑似含 Key 文件` : ''}`)
    s.value = await get('/api/settings')
  } finally { backupRunning.value = false }
}

function ElMessageSilent(msg: string) {
  // local import avoidance
  import('element-plus').then(({ ElMessage }) => ElMessage.success(msg))
}
</script>

<template>
  <div style="padding:16px 28px; overflow:auto; height:100%" v-if="s">
    <div style="display:flex; align-items:center; margin-bottom:12px">
      <h2 style="margin:0">系统设置 <el-tag size="small" type="info" style="margin-left:8px">只读（S-02）</el-tag></h2>
    </div>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>Provider（Key 恒显 ***，G-03）</b></template>
          <el-table :data="s.providers" size="small">
            <el-table-column prop="name" label="名称" width="120" />
            <el-table-column prop="base_url" label="Base URL" min-width="180" />
            <el-table-column prop="model" label="模型" width="130" />
            <el-table-column prop="key" label="Key" width="70" />
          </el-table>
          <div style="color:#909399; font-size:12px; margin-top:6px">
            默认 Provider：{{ s.default_provider }} · Key 从环境变量注入（key_env），永不落盘/入备份/入日志
          </div>
        </el-card>

        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>复杂度映射（G-08）</b></template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="(v, k) in s.complexity_map?.levels || {}" :key="k" :label="String(k)">
              <el-tag v-for="m in (v.modules === '*all*' ? ['全部 10 模块'] : v.modules)" :key="String(m)"
                size="small" style="margin-right:4px">{{ m }}</el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>硬约束包（第 1 层 · 不可整体关闭）</b></template>
          <el-tag type="success">package_version {{ s.constraints_package?.package_version }}</el-tag>
          <div style="margin:8px 0">
            <el-tag v-for="k in s.constraints_package?.conflict_keywords || []" :key="k" size="small"
              type="warning" style="margin-right:6px">{{ k }}</el-tag>
          </div>
          <MarkdownView :source="s.constraints_package?.preview || ''" />
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>目录模板</b></template>
          <el-table :data="s.templates" size="small">
            <el-table-column prop="name" label="模板" width="120" />
            <el-table-column label="目录" min-width="240">
              <template #default="{ row }">
                <el-tag v-for="d in row.dirs" :key="d" size="small" type="info" style="margin-right:4px">{{ d }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>脱敏规则（复制/导出强制过门）</b></template>
          <el-table :data="s.sanitize_rules?.rules || []" size="small">
            <el-table-column prop="id" label="规则" width="110" />
            <el-table-column prop="pattern" label="正则" min-width="200" />
            <el-table-column prop="severity" label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'block' ? 'danger' : 'warning'" size="small">{{ row.severity }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>备份（NFR-08 · {{ s.backup?.schedule }}）</b>
              <div style="flex:1" />
              <el-button size="small" :loading="backupRunning" @click="runBackup">立即快照</el-button>
            </div>
          </template>
          <div style="color:#606266; font-size:13px; margin-bottom:8px">
            备份目录：{{ s.backup?.dir }} · 保留 {{ s.backup?.retention_days }} 天 · 备份前运行 Key 扫描
          </div>
          <el-table :data="s.backup?.recent || []" size="small">
            <el-table-column prop="date" label="快照" width="120" />
            <el-table-column prop="files" label="文件数" width="90" />
            <el-table-column prop="skipped_key_hits" label="跳过(疑似Key)" width="120" />
          </el-table>
        </el-card>

        <el-card shadow="never" style="margin-top:14px">
          <template #header><b>路径</b></template>
          <div style="font-size:13px; color:#606266; line-height:2">
            数据根：{{ s.paths?.data_root }}<br />
            配置根：{{ s.paths?.config_root }}（热加载：修改 mtime 后 5s 内生效，AC-11）<br />
            审计日志：{{ s.paths?.audit_log }}
          </div>
          <el-alert v-if="s.degraded?.length" type="error" :closable="false" style="margin-top:8px"
            title="部分配置损坏已回退 .bak 副本，请检查 configs/ 目录" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
