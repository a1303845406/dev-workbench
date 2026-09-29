import { createRouter, createWebHashHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'projects', component: () => import('./views/ProjectList.vue') },
    {
      path: '/project/:pid', name: 'workbench', component: () => import('./views/Workbench.vue'),
      children: [
        { path: '', redirect: { name: 'explore' } },
        { path: 'explore', name: 'explore', component: () => import('./views/Explore.vue') },
        { path: 'scan', name: 'scan', component: () => import('./views/ScanView.vue') },
        { path: 'engineering', name: 'engineering', component: () => import('./views/Engineering.vue') },
        { path: 'testcases', name: 'testcases', component: () => import('./views/TestCases.vue') },
        { path: 'board', name: 'board', component: () => import('./views/Board.vue') },
        { path: 'assets', name: 'assets', component: () => import('./views/Assets.vue') },
        { path: 'settings', name: 'psettings', component: () => import('./views/ProjectSettings.vue') }
      ]
    },
    { path: '/system', name: 'system', component: () => import('./views/SystemSettings.vue') }
  ]
})
