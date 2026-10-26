<script setup lang="ts">
/** G-10 外发知情确认对话框：任何模型调用前必须确认（Provider + 文档清单 + 估算字符量）。 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get, makeOutbound, type OutboundConf } from '../api'

const visible = ref(false)
const loading = ref(false)
let resolveFn: ((c: OutboundConf | null) => void) | null = null
const provider = ref('mock-demo')
const files = ref<string[]>([])
const chars = ref(0)

const emit = defineEmits<{ (e: 'confirmed', c: OutboundConf): void }>()

async function open(fileIds: string[], estimatedChars: number): Promise<OutboundConf | null> {
  files.value = fileIds
  chars.value = estimatedChars
  loading.value = true
  try {
    const s = await get('/api/settings')
    provider.value = s.default_provider || s.providers?.[0]?.name || 'mock-demo'
  } catch { /* keep default */ }
  loading.value = false
  visible.value = true
  return new Promise(resolve => { resolveFn = resolve })
}

function confirm() {
  visible.value = false
  const conf = makeOutbound(provider.value, files.value, chars.value)
  resolveFn?.(conf)
  emit('confirmed', conf)
}

function cancel() {
  visible.value = false
  resolveFn?.(null)
  ElMessage.info('已取消：未获知情确认前不会发起模型外发')
}

defineExpose({ open })
</script>

<template>
  <el-dialog v-model="visible" title="外发知情确认（G-10）" width="560px" :close-on-click-modal="false">
    <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 12px"
      title="以下内容将发送给外部模型服务，请确认无敏感信息" />
    <el-descriptions :column="1" border>
      <el-descriptions-item label="目标 Provider">{{ provider }}</el-descriptions-item>
      <el-descriptions-item label="包含文档">
        <el-tag v-for="f in files" :key="f" size="small" style="margin-right:6px">{{ f }}</el-tag>
        <span v-if="!files.length" style="color:#909399">（无额外文档）</span>
      </el-descriptions-item>
      <el-descriptions-item label="估算字符量">约 {{ chars }} 字符</el-descriptions-item>
    </el-descriptions>
    <template #footer>
      <el-button @click="cancel">取消</el-button>
      <el-button type="primary" @click="confirm">确认并发起</el-button>
    </template>
  </el-dialog>
</template>
