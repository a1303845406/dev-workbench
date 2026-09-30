<script setup lang="ts">
/** 系统设置：左侧分类菜单 + 完整管理功能（模型服务/工程规则/项目模板/数据与备份）。 */
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, post, put } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const router = useRouter()
const s = ref<any>(null)
const backupRunning = ref(false)
const active = ref<string>((route.query.tab as string) || 'models')

const menus = [
  { key: 'models', label: '模型服务', icon: '🤖', desc: 'Provider 管理、默认模型' },
  { key: 'rules', label: '工程规则', icon: '📐', desc: '复杂度映射、脱敏、硬约束' },
  { key: 'templates', label: '项目模板', icon: '📁', desc: '目录骨架管理' },
  { key: 'data', label: '数据与备份', icon: '💾', desc: '快照、恢复、健康状态' }
]

const MODULES = ['requirement', 'design-outline', 'detailed-design', 'database',
  'development-backend', 'development-frontend', 'test', 'acceptance', 'prototype', 'regression']

watch(active, k => router.replace({ query: { tab: k } }))

onMounted(async () => {
  await reload()
  const q = route.query.tab as string
  if (q && menus.some(m => m.key === q)) active.value = q
})

async function reload() {
  s.value = await get('/api/settings')
  draft.providers = JSON.parse(JSON.stringify(s.value.providers || []))
  draft.default = s.value.default_provider || ''
  draft.levels = JSON.parse(JSON.stringify(s.value.complexity_map?.levels || {}))
  draft.rules = JSON.parse(JSON.stringify(s.value.sanitize_rules?.rules || []))
  constraintText.value = (await get('/api/settings/constraints-package')).content
}

// ───────────────────────── 模型服务 ─────────────────────────
const draft = reactive<{ providers: any[]; default: string; levels: any; rules: any[] }>({
  providers: [], default: '', levels: {}, rules: []
})
const provDialog = ref(false)
const provForm = ref<any>({})
const provEditing = ref(-1)

function openProvNew() {
  provEditing.value = -1
  provForm.value = { name: '', base_url: '', model: '', key_env: '', stream: true, timeout_s: 60, note: '' }
  provDialog.value = true
}

function openProvEdit(row: any, idx: number) {
  provEditing.value = idx
  provForm.value = { ...row }
  provDialog.value = true
}

function saveProvForm() {
  const f = provForm.value
  if (!f.name?.trim() || !f.base_url?.trim() || !f.model?.trim()) {
    ElMessage.warning('名称 / Base URL / 模型 为必填'); return
  }
  const dup = draft.providers.findIndex(p => p.name === f.name.trim())
  if (provEditing.value < 0 && dup >= 0) { ElMessage.warning(`名称已存在：${f.name}`); return }
  if (provEditing.value >= 0 && dup >= 0 && dup !== provEditing.value) {
    ElMessage.warning(`名称已存在：${f.name}`); return
  }
  const item = { ...f, name: f.name.trim() }
  if (provEditing.value >= 0) draft.providers[provEditing.value] = item
  else draft.providers.push(item)
  if (!draft.default) draft.default = item.name
  provDialog.value = false
}

function removeProv(idx: number) {
  const p = draft.providers[idx]
  if (p.name === draft.default) { ElMessage.warning('默认 Provider 不可删除，请先切换默认'); return }
  draft.providers.splice(idx, 1)
}

async function saveProviders() {
  try {
    s.value = await put('/api/settings/providers', { providers: draft.providers, default: draft.default })
    ElMessage.success('Provider 配置已保存（5 秒内热加载生效）')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

// ───────────────────────── 工程规则 ─────────────────────────
const levelNames = ['简单', '中等', '复杂']
function levelModules(k: string): string[] {
  const m = draft.levels[k]?.modules
  return m === '*all*' ? ['*all*'] : (m || [])
}
function setLevelModules(k: string, v: string[]) {
  const mods = v.includes('*all*') ? '*all*' : v.filter(x => x !== '*all*')
  draft.levels[k] = { ...(draft.levels[k] || {}), modules: mods }
}
async function saveComplexity() {
  try {
    s.value = await put('/api/settings/complexity-map', { levels: draft.levels })
    ElMessage.success('复杂度映射已保存')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

function addRule() {
  draft.rules.push({ id: '', pattern: '', severity: 'confirm' })
}
async function saveRules() {
  try {
    s.value = await put('/api/settings/sanitize-rules', { rules: draft.rules })
    ElMessage.success('脱敏规则已保存')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

const constraintText = ref('')
const savingConstraints = ref(false)
async function saveConstraints() {
  savingConstraints.value = true
  try {
    s.value = await put('/api/settings/constraints-package', { content: constraintText.value })
    ElMessage.success('硬约束包已保存')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) } finally { savingConstraints.value = false }
}

// ───────────────────────── 项目模板 ─────────────────────────
const tplDialog = ref(false)
const tplForm = ref<any>({ name: '', description: '', dirs: [] })
function openTplNew() {
  tplForm.value = { name: '', description: '', dirs: ['docs/需求', 'docs/过程档案', 'src', 'tests'] }
  tplDialog.value = true
}
async function saveTpl() {
  const f = tplForm.value
  if (!f.name?.trim() || !f.dirs?.length) { ElMessage.warning('模板名与目录列表为必填'); return }
  try {
    await post('/api/templates', { name: f.name.trim(), description: f.description, dirs: f.dirs })
    ElMessage.success('模板已创建')
    tplDialog.value = false
    s.value = await get('/api/settings')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}
async function removeTpl(row: any) {
  try {
    await ElMessageBox.confirm(`确认删除模板「${row.name}」？该操作不可恢复。`, '删除模板', { type: 'warning' })
  } catch { return }
  try {
    await del(`/api/templates/${encodeURIComponent(row.name)}`)
    ElMessage.success('模板已删除')
    s.value = await get('/api/settings')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}

// ───────────────────────── 数据与备份 ─────────────────────────
async function runBackup() {
  backupRunning.value = true
  try {
    const r = await post('/api/backups')
    ElMessage.success(`快照完成：${r.files} 个文件${r.skipped_key_hits ? `，跳过 ${r.skipped_key_hits} 个疑似含 Key 文件` : ''}`)
    s.value = await get('/api/settings')
  } finally { backupRunning.value = false }
}

async function restoreBackup(row: any) {
  let v: string
  try {
    const r = await ElMessageBox.prompt(
      `将把 projects/ 恢复到快照「${row.date}」的状态。当前数据会先自动另存为恢复前快照。` +
      `请输入快照日期「${row.date}」以确认：`, '恢复快照', { type: 'warning' })
    v = (r.value || '').trim()
  } catch { return }
  try {
    const r = await post('/api/backups/restore', { date: row.date, confirm_name: v })
    ElMessage.success(`已恢复到 ${r.restored}（${r.files} 个文件，恢复前快照：${r.pre_restore_snapshot}）`)
    s.value = await get('/api/settings')
  } catch (e: any) { ElMessage.error(`${e.message}（${e.code}）`) }
}
</script>

<template>
  <el-container style="height:100%">
    <!-- 左侧分类菜单 -->
    <el-aside width="210px" class="cat-side">
      <el-button size="small" class="back-btn" @click="router.push('/')">← 返回项目列表</el-button>
      <div class="cat-title">系统设置</div>
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
          <template #header>
            <div style="display:flex; align-items:center">
              <b>Provider 列表（Key 恒显 ***，G-03）</b>
              <div style="flex:1" />
              <el-button size="small" type="primary" @click="openProvNew">＋ 新增 Provider</el-button>
              <el-button size="small" type="success" @click="saveProviders">保存更改</el-button>
            </div>
          </template>
          <el-table :data="draft.providers" size="small">
            <el-table-column prop="name" label="名称" width="130" />
            <el-table-column prop="base_url" label="Base URL" min-width="180" show-overflow-tooltip />
            <el-table-column prop="model" label="模型" width="130" />
            <el-table-column prop="key_env" label="Key 环境变量" width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ row.key_env || '（无需 Key）' }}</template>
            </el-table-column>
            <el-table-column label="默认" width="90">
              <template #default="{ row }">
                <el-button v-if="row.name !== draft.default" size="small" text type="primary
                  " @click="draft.default = row.name">设为默认</el-button>
                <el-tag v-else size="small" type="success">默认</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row, $index }">
                <el-button size="small" text type="primary" @click="openProvEdit(row, $index)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeProv($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div style="color:#909399; font-size:12px; margin-top:8px">
            修改后点「保存更改」落盘 providers.json，5 秒内热加载，无需重启。项目使用的模型在
            <b>⑦ 项目设置 → 默认 Provider</b> 中按项目切换。
          </div>
        </el-card>

        <el-dialog v-model="provDialog" :title="provEditing >= 0 ? '编辑 Provider' : '新增 Provider'" width="560px">
          <el-form label-width="110px" size="small">
            <el-form-item label="名称" required><el-input v-model="provForm.name" placeholder="如 官方-主模型" /></el-form-item>
            <el-form-item label="Base URL" required><el-input v-model="provForm.base_url" placeholder="https://api.openai.com/v1" /></el-form-item>
            <el-form-item label="模型" required><el-input v-model="provForm.model" placeholder="gpt-4o-mini / deepseek-chat …" /></el-form-item>
            <el-form-item label="Key 环境变量">
              <el-input v-model="provForm.key_env" placeholder="如 DEVWB_PROVIDER__OFFICIAL__API_KEY（留空=无需 Key）" />
            </el-form-item>
            <el-form-item label="流式输出"><el-switch v-model="provForm.stream" /></el-form-item>
            <el-form-item label="超时(秒)"><el-input-number v-model="provForm.timeout_s" :min="5" :max="600" /></el-form-item>
            <el-form-item label="备注"><el-input v-model="provForm.note" /></el-form-item>
          </el-form>
          <div style="color:#909399; font-size:12px">Key 本体只存环境变量，永不落盘/入备份/入日志（G-03）</div>
          <template #footer>
            <el-button @click="provDialog = false">取消</el-button>
            <el-button type="primary" @click="saveProvForm">确定</el-button>
          </template>
        </el-dialog>
      </template>

      <!-- ═══ 工程规则 ═══ -->
      <template v-else-if="active === 'rules'">
        <h2 style="margin-top:0">工程规则</h2>
        <el-card shadow="never" style="margin-bottom:14px">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>复杂度 → 模块组合映射（G-08）</b>
              <div style="flex:1" />
              <el-button size="small" type="success" @click="saveComplexity">保存映射</el-button>
            </div>
          </template>
          <el-form label-width="70px" size="small">
            <el-form-item v-for="k in levelNames" :key="k" :label="k">
              <el-select :model-value="levelModules(k)" multiple filterable allow-create
                default-first-option placeholder="选择模块组合" style="width:100%"
                @update:model-value="(v: string[]) => setLevelModules(k, v)">
                <el-option label="全部 10 模块" value="*all*" />
                <el-option v-for="m in MODULES" :key="m" :label="m" :value="m" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" style="margin-bottom:14px">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>脱敏规则（复制/导出强制过门）</b>
              <div style="flex:1" />
              <el-button size="small" @click="addRule">＋ 添加规则</el-button>
              <el-button size="small" type="success" @click="saveRules">保存规则</el-button>
            </div>
          </template>
          <el-table :data="draft.rules" size="small">
            <el-table-column label="规则 ID" width="150">
              <template #default="{ row }"><el-input v-model="row.id" size="small" placeholder="如 api-key" /></template>
            </el-table-column>
            <el-table-column label="正则（sk- 开头 Key 示例）" min-width="260">
              <template #default="{ row }"><el-input v-model="row.pattern" size="small" placeholder="正则表达式" /></template>
            </el-table-column>
            <el-table-column label="级别" width="120">
              <template #default="{ row }">
                <el-select v-model="row.severity" size="small">
                  <el-option label="block（阻断）" value="block" />
                  <el-option label="confirm（警示）" value="confirm" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="{ $index }">
                <el-button size="small" text type="danger" @click="draft.rules.splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>硬约束包（第 1 层 · 编辑影响所有生成）</b>
              <div style="flex:1" />
              <el-tag type="success" size="small" style="margin-right:8px">
                version {{ s.constraints_package?.package_version }}
              </el-tag>
              <el-button size="small" type="success" :loading="savingConstraints" @click="saveConstraints">保存硬约束包</el-button>
            </div>
          </template>
          <el-alert type="warning" :closable="false" style="margin-bottom:8px"
            title="硬约束不可整体关闭；保存前请确认 front matter（含 package_version）完整保留" />
          <el-input v-model="constraintText" type="textarea" :rows="16" style="font-family:Consolas,monospace" />
        </el-card>
      </template>

      <!-- ═══ 项目模板 ═══ -->
      <template v-else-if="active === 'templates'">
        <h2 style="margin-top:0">项目模板</h2>
        <el-card shadow="never">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>目录模板（新建项目时生成目录骨架）</b>
              <div style="flex:1" />
              <el-button size="small" type="primary" @click="openTplNew">＋ 新建空白模板</el-button>
            </div>
          </template>
          <el-table :data="s.templates" size="small">
            <el-table-column prop="name" label="模板" width="200" />
            <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
            <el-table-column label="生成的目录" min-width="280">
              <template #default="{ row }">
                <el-tag v-for="d in row.dirs" :key="d" size="small" type="info" style="margin-right:4px">{{ d }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button size="small" text type="danger" :disabled="row.id === 'default-template'"
                  @click="removeTpl(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        <el-alert type="info" :closable="false" style="margin-top:10px"
          title="把现有项目结构保存为模板：新建项目后可基于项目生成（接口 POST /api/templates + from_project）" />

        <el-dialog v-model="tplDialog" title="新建空白模板" width="560px">
          <el-form label-width="90px" size="small">
            <el-form-item label="模板名" required>
              <el-input v-model="tplForm.name" placeholder="仅字母数字-_，如 microservice-tpl" />
            </el-form-item>
            <el-form-item label="说明"><el-input v-model="tplForm.description" /></el-form-item>
            <el-form-item label="目录列表" required>
              <el-select v-model="tplForm.dirs" multiple filterable allow-create default-first-option
                placeholder="输入目录后回车，如 docs/需求" style="width:100%">
                <el-option v-for="d in ['docs/需求', 'docs/过程档案', 'docs/assets', 'src', 'tests']"
                  :key="d" :label="d" :value="d" />
              </el-select>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="tplDialog = false">取消</el-button>
            <el-button type="primary" @click="saveTpl">创建</el-button>
          </template>
        </el-dialog>
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
            <el-table-column prop="date" label="快照" width="180" />
            <el-table-column prop="files" label="文件数" width="100" />
            <el-table-column prop="skipped_key_hits" label="跳过(疑似Key)" width="140" />
            <el-table-column label="操作" width="140">
              <template #default="{ row }">
                <el-button size="small" text type="warning" @click="restoreBackup(row)">恢复到此快照</el-button>
              </template>
            </el-table-column>
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
