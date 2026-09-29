<script setup lang="ts">
/** 项目工作台容器：左侧 ①~⑦ 导航（06 界面设计 §2）。 */
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const pid = route.params.pid as string

const nav = [
  { name: 'explore', label: '① 需求探索', icon: '🧭' },
  { name: 'scan', label: '② 项目规整', icon: '🗂' },
  { name: 'engineering', label: '③ 提示词工程', icon: '⚙️' },
  { name: 'testcases', label: '④ 测试用例', icon: '🧪' },
  { name: 'board', label: '⑤ 任务看板', icon: '📋' },
  { name: 'assets', label: '⑥ 资产库', icon: '📚' },
  { name: 'settings', label: '⑦ 项目设置', icon: '🔧' }
]
</script>

<template>
  <el-container style="height: 100%">
    <el-aside width="180px" class="side">
      <div class="proj">{{ pid }}</div>
      <div v-for="n in nav" :key="n.name" class="nav-item" :class="{ active: route.name === n.name }"
        @click="router.push({ name: n.name, params: { pid } })">
        <span style="margin-right:8px">{{ n.icon }}</span>{{ n.label }}
      </div>
    </el-aside>
    <el-main style="padding:0; overflow:hidden">
      <router-view :key="pid" />
    </el-main>
  </el-container>
</template>

<style scoped>
.side { border-right: 1px solid #e4e7ed; background: #fafbfc; padding: 12px 0; }
.proj { font-weight: 600; padding: 4px 16px 14px; color: #2f54eb; }
.nav-item { padding: 10px 16px; cursor: pointer; color: #303133; font-size: 14px; }
.nav-item:hover { background: #f0f2f5; }
.nav-item.active { background: #ecf2ff; color: #2f54eb; font-weight: 600; border-right: 3px solid #2f54eb; }
</style>
