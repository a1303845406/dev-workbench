<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { connectSse, onStatus } from './sse'
import OutboundConfirmDialog from './components/OutboundConfirmDialog.vue'

const sseOk = ref(false)
onMounted(() => {
  connectSse()
  onStatus(ok => (sseOk.value = ok))
})
</script>

<template>
  <el-container style="height: 100vh">
    <el-header height="48px" class="topbar">
      <div class="brand">
        <span class="logo">⌘</span>
        <span class="title">开发工作台 <span class="en">Dev Workbench</span></span>
      </div>
      <div class="spacer" />
      <el-tooltip content="事件流连接状态">
        <span class="dot" :class="sseOk ? 'ok' : 'bad'">●</span>
      </el-tooltip>
      <router-link to="/system" class="syslink">系统设置</router-link>
    </el-header>
    <el-main style="padding: 0; overflow: hidden">
      <router-view />
    </el-main>
  </el-container>
  <OutboundConfirmDialog />
</template>

<style>
.topbar {
  display: flex; align-items: center; gap: 16px;
  border-bottom: 1px solid #e4e7ed; background: #fff;
}
.brand { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.brand .logo { color: #2f54eb; font-size: 20px; }
.brand .en { color: #909399; font-weight: 400; font-size: 12px; }
.spacer { flex: 1; }
.dot { font-size: 14px; }
.dot.ok { color: #52c41a; }
.dot.bad { color: #f5222d; }
.syslink { color: #2f54eb; text-decoration: none; font-size: 14px; }
body { font-family: 'Microsoft YaHei', 'PingFang SC', system-ui, sans-serif; font-size: 14px; color: #303133; }
.md-body { line-height: 1.7; }
.md-body table { border-collapse: collapse; margin: 8px 0; }
.md-body th, .md-body td { border: 1px solid #dcdfe6; padding: 4px 10px; font-size: 13px; }
.md-body pre { background: #f5f7fa; padding: 10px; border-radius: 4px; overflow: auto; }
.md-body code { background: #f5f7fa; padding: 1px 4px; border-radius: 3px; font-size: 13px; }
</style>
