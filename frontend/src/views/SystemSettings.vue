<script setup lang="ts">
/** 系统设置（只读 S-02）：左侧分类菜单 + 右侧内容区。四类：模型服务 / 工程规则 / 模板 / 数据与备份。 */
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const router = useRouter()
const s = ref<any>(null)
const backupRunning = ref(false)
const active = ref<string>((route.query.tab as string) || 'models')

const menus = [
  { key: 'models', label: '模型服务', icon: '🤖', desc: 'Provider、默认模型、Key 注入' },
  { key: 'rules', label: '工程规则', icon: '📐', desc: '复杂度映射、硬约束、脱敏' },
  { key: 'templates', label: '项目模板', icon: '📁', desc: '新建项目的目录骨架' },
  { key: 'data', label: '数据与备份', icon: '💾', desc: '快照、路径、健康状态' }
]

watch(active, k => router.replace({ query: { tab: k } }))

onMounted(async () => {
  s.value = await get('/api/settings')
  const q = route.query.tab as string
  if (q && menus.some(m => m.key === q)) active.value = q
})

async function runBackup() {
  backupRunning.value = true
  try {
    const r = await post('/api/backups')
    ElMessageSilent(`快照完成：${r.files} 个文件${r.skipped_key_hits ? `，跳过 ${r.skipped_key_hits} 个疑似含 Key 文件` : ''}`)
    s.value = await get('/api/settings')
  } finally { backupRunning.value = false }
}

function ElMessageSilent(msg: string) {
  import('element-plus').then(({ ElMessage }) => ElMessage.success(msg))
}
</script>

<template>
  <el-container style="height:100%">
    <!-- 左侧分类菜单 -->
    <el-aside width="210px" class="cat-side">
      <el-button size="small" class="back-btn" @click="router.push('/')">← 返回项目列表</el-button>
      <div class="cat-title">系统设置 <el-tag size="small" type="info">只读</el-tag></div>
      <div v-for="m in menus" :key="m.key" class="cat-item" :class="{ active: active === m.key }"
        @click="active = m.key">
        <div class="cat-label"><span class="cat-icon">{{ m.icon }}</span>{{ m.label }}</div>
        <div class="cat-desc">{{ m.desc }}</div>
      </div>
    </el-aside>

    <!-- 右侧内容区 -->
    <el-main style="padding:16px 24px; overflow:auto" v-if="s">
      <!-- ═══ 模型服务 ═══ -->
      <template v-if="active === 'models'">
        <h2 style="margin-top:0">模型服务</h2>
        <el-card shadow="never">
          <template #header><b>Provider 列表（Key 恒显 ***，G-03）</b></template>
          <el-table :data="s.providers" size="small">
            <el-table-column prop="name" label="名称" width="140" />
            <el-table-column prop="base_url" label="Base URL" min-width="200" />
            <el-table-column prop="model" label="模型" width="140" />
            <el-table-column prop="key" label="Key" width="70" />
          </el-table>
          <div style="color:#909399; font-size:12px; margin-top:6px">
            当前默认：<b>{{ s.default_provider }}</b> · Key 从环境变量注入（key_env），永不落盘/入备份/入日志
          </div>
          <el-alert type="info" :closable="false" style="margin-top:10px">
            <template #title>如何新增 / 切换模型？（三步）</template>
            <div style="font-size:13px; line-height:1.9">
              ① 编辑配置文件 <b>{{ s.paths?.config_root }}\providers.json</b>：在 providers 数组中新增一项
              （字段 name / base_url / model / key_env / stream / timeout_s），保存后 <b>5 秒内热加载</b>，无需重启；<br />
              ② 设置 API Key 环境变量：变量名 = 该条目的 <b>key_env</b>（如 DEVWB_PROVIDER__OFFICIAL__API_KEY），
              Key 只存环境变量，永不落盘；<br />
              ③ 切换项目使用的模型：进入 <b>项目 → ⑦ 项目设置 → 默认 Provider</b>，从下拉中选择即可。
            </div>
          </el-alert>
        </el-card>
      </template>

      <!-- ═══ 工程规则 ═══ -->
      <template v-else-if="active === 'rules'">
        <h2 style="margin-top:0">工程规则</h2>
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header><b>复杂度 → 模块组合映射（G-08）</b></template>
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
        <el-card shadow="never">
          <template #header><b>脱敏规则（复制/导出强制过门）</b></template>
          <el-table :data="s.sanitize_rules?.rules || []" size="small">
            <el-table-column prop="id" label="规则" width="130" />
            <el-table-column prop="pattern" label="正则" min-width="240" />
            <el-table-column prop="severity" label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'block' ? 'danger' : 'warning'" size="small">{{ row.severity }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </template>

      <!-- ═══ 项目模板 ═══ -->
      <template v-else-if="active === 'templates'">
        <h2 style="margin-top:0">项目模板</h2>
        <el-card shadow="never">
          <template #header><b>目录模板（新建项目时生成目录骨架）</b></template>
          <el-table :data="s.templates" size="small">
            <el-table-column prop="name" label="模板" width="160" />
            <el-table-column label="生成的目录" min-width="320">
              <template #default="{ row }">
                <el-tag v-for="d in row.dirs" :key="d" size="small" type="info" style="margin-right:4px">{{ d }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </template>

      <!-- ═══ 数据与备份 ═══ -->
      <template v-else>
        <h2 style="margin-top:0">数据与备份</h2>
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>备份快照（NFR-08 · {{ s.backup?.schedule }}）</b>
              <div style="flex:1" />
              <el-button size="small" :loading="backupRunning" @click="runBackup">立即快照</el-button>
            </div>
          </template>
          <div style="color:#606266; font-size:13px; margin-bottom:8px">
            备份目录：{{ s.backup?.dir }} · 保留 {{ s.backup?.retention_days }} 天 · 备份前运行 Key 扫描
          </div>
          <el-table :data="s.backup?.recent || []" size="small">
            <el-table-column prop="date" label="快照" width="140" />
            <el-table-column prop="files" label="文件数" width="100" />
            <el-table-column prop="skipped_key_hits" label="跳过(疑似Key)" width="140" />
          </el-table>
        </el-card>
        <el-card shadow="never">
          <template #header><b>路径与健康状态</b></template>
          <div style="font-size:13px; color:#606266; line-height:2">
            数据根：{{ s.paths?.data_root }}<br />
            配置根：{{ s.paths?.config_root }}（热加载：修改 mtime 后 5s 内生效，AC-11）<br />
            审计日志：{{ s.paths?.audit_log }}
          </div>
          <el-alert v-if="s.degraded?.length" type="error" :closable="false" style="margin-top:8px"
            title="部分配置损坏已回退 .bak 副本，请检查 configs/ 目录" />
          <el-alert v-else type="success" :closable="false" style="margin-top:8px" title="全部配置加载正常" />
        </el-card>
      </template>
    </el-main>
  </el-container>
</template>

<style scoped>
.cat-side { border-right: 1px solid #e4e7ed; background: #fafbfc; padding: 14px 12px; }
.back-btn { width: 100%; margin-bottom: 14px; }
.cat-title { font-weight: 600; font-size: 15px; padding: 0 4px 12px; }
.cat-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; border: 1px solid transparent; }
.cat-item:hover { background: #f0f2f5; }
.cat-item.active { background: #ecf2ff; border-color: #adc6ff; }
.cat-label { font-size: 14px; color: #303133; font-weight: 500; }
.cat-item.active .cat-label { color: #2f54eb; font-weight: 600; }
.cat-icon { margin-right: 8px; }
.cat-desc { font-size: 12px; color: #909399; margin-top: 3px; padding-left: 22px; }
</style>
