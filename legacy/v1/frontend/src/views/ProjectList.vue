<script setup lang="ts">
/** 项目列表页 + 新建项目向导（FR-02）+ 删除强确认（S-01）。 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { del, get, post } from '../api'

const router = useRouter()
const projects = ref<any[]>([])
const templates = ref<any[]>([])
const loading = ref(false)
const wizard = ref(false)
const step = ref(0)
const form = ref({ name: '', template: 'default-template' })
const creating = ref(false)

async function load() {
  loading.value = true
  try {
    projects.value = (await get('/api/projects')).projects
    templates.value = (await get('/api/templates')).templates
  } finally { loading.value = false }
}
onMounted(load)

const nameOk = () => /^[\u4e00-\u9fa5A-Za-z0-9_\-\s]{1,40}$/.test(form.value.name.trim())

async function create() {
  creating.value = true
  try {
    const res = await post('/api/projects', form.value)
    ElMessage.success('项目创建成功')
    wizard.value = false
    step.value = 0
    await load()
    router.push(`/project/${encodeURIComponent(res.name)}`)
  } catch (e: any) {
    ElMessage.error(`${e.message}（${e.code}）`)
  } finally { creating.value = false }
}

async function remove(p: any) {
  try {
    const { value } = await ElMessageBox.prompt(
      `删除项目将移除其全部数据（依赖每日备份恢复）。请输入项目名「${p.name}」确认：`, '删除项目（S-01 强确认）',
      { confirmButtonText: '删除', cancelButtonText: '取消', inputPattern: new RegExp(`^${p.name}$`), inputErrorMessage: '输入与项目名不一致' })
    await del(`/api/projects/${encodeURIComponent(p.name)}`, { confirm_name: value })
    ElMessage.success('已删除')
    await load()
  } catch { /* cancelled */ }
}

function tplOf(id: string) {
  return templates.value.find(t => t.id === id)
}
</script>

<template>
  <div style="padding: 20px 28px; overflow: auto; height: 100%">
    <div style="display:flex; align-items:center; margin-bottom: 16px">
      <h2 style="margin:0">项目</h2>
      <div style="flex:1" />
      <el-button type="primary" @click="wizard = true">＋ 新建项目</el-button>
    </div>

    <el-empty v-if="!loading && !projects.length" description="创建第一个项目，开始需求探索与提示词工程">
      <el-button type="primary" @click="wizard = true">＋ 新建项目</el-button>
    </el-empty>

    <el-row :gutter="16">
      <el-col v-for="p in projects" :key="p.name" :span="8" style="margin-bottom:16px">
        <el-card shadow="hover">
          <template #header>
            <div style="display:flex; align-items:center">
              <b>{{ p.name }}</b>
              <div style="flex:1" />
              <el-tag v-for="pl in p.pipelines_active" :key="pl.id" size="small" type="warning"
                style="margin-left:6px">{{ pl.title }}·{{ pl.stage }}</el-tag>
            </div>
          </template>
          <div style="color:#606266; font-size:13px; line-height:1.9">
            <div>模板：{{ p.template }}</div>
            <div>创建：{{ (p.created_at || '').slice(0, 19).replace('T', ' ') }}</div>
            <div style="color:#909399">最近落盘：{{ (p.last_write || '—').slice(0, 19).replace('T', ' ') }}</div>
          </div>
          <div style="margin-top: 12px; display:flex; gap:8px">
            <el-button type="primary" size="small"
              @click="router.push(`/project/${encodeURIComponent(p.name)}`)">打开工作台</el-button>
            <el-button size="small" type="danger" plain @click="remove(p)">删除</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="wizard" title="新建项目向导（FR-02）" width="640px">
      <el-steps :active="step" simple style="margin-bottom:18px">
        <el-step title="选模板" />
        <el-step title="填项目名" />
        <el-step title="确认" />
      </el-steps>

      <div v-if="step === 0">
        <el-card v-for="t in templates" :key="t.id" shadow="hover" :class="{ picked: form.template === t.id }"
          style="margin-bottom:10px; cursor:pointer" @click="form.template = t.id">
          <b>{{ t.name }}</b> <span style="color:#909399">{{ t.description }}</span>
          <div style="margin-top:6px">
            <el-tag v-for="d in t.dirs" :key="d" size="small" type="info" style="margin-right:6px">{{ d }}/</el-tag>
          </div>
        </el-card>
      </div>

      <div v-else-if="step === 1">
        <el-input v-model="form.name" placeholder="项目名（中文/字母/数字/_-）" maxlength="40" />
        <div style="color:#909399; font-size:12px; margin-top:6px">
          同名目录已存在时将阻断创建（AC-02）
        </div>
      </div>

      <div v-else>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="项目名">{{ form.name || '（未填写）' }}</el-descriptions-item>
          <el-descriptions-item label="模板">{{ tplOf(form.template)?.name }}</el-descriptions-item>
          <el-descriptions-item label="将生成的骨架">
            <div v-for="d in tplOf(form.template)?.dirs" :key="d">{{ d }}/</div>
            <div v-for="f in tplOf(form.template)?.files" :key="f.path">{{ f.path }}</div>
            <el-tag size="small" type="success" style="margin-top:4px">含 rules.md 骨架（G-07）</el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <template #footer>
        <el-button v-if="step > 0" @click="step--">上一步</el-button>
        <el-button v-if="step < 2" :disabled="step === 1 && !nameOk()" @click="step++">下一步</el-button>
        <el-button v-else type="primary" :loading="creating" :disabled="!nameOk()" @click="create">创建项目</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.picked { outline: 2px solid #2f54eb; }
</style>
