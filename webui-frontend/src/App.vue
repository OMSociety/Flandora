<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import Sidebar from './components/Sidebar.vue'
import AppIcon from './components/AppIcon.vue'
import BrandMark from './components/BrandMark.vue'
import ToastContainer from './components/ToastContainer.vue'
import Dashboard from './pages/Dashboard.vue'
import Messages from './pages/Messages.vue'
import Logs from './pages/Logs.vue'
import Config from './pages/Config.vue'
import Desktop from './pages/Desktop.vue'
import Wizard from './pages/Wizard.vue'
import TestSend from './pages/TestSend.vue'
import { wsState, connectWS, on } from './ws'
import { startStatusPolling, stopStatusPolling, refreshStatus, setMessages, setLogs, addMessage, addLog, status } from './store'
import { PAGE_MAP, pageFromHash } from './navigation'
import { useTheme } from './useTheme'
import { pushOverlay, popOverlay } from './overlayStack'

const drawerOverlayId = Symbol('drawer')

const tab = ref(pageFromHash(window.location.hash))
const drawerOpen = ref(false)
const sidebarCollapsed = ref(localStorage.getItem('webot-sidebar') === 'collapsed')
const { theme, toggleTheme } = useTheme()
const page = computed(() => PAGE_MAP[tab.value])
const wsLabel = computed(() => ({ connected: '实时连接正常', reconnecting: '正在重新连接', closed: '实时连接已断开' }[wsState.state] || '正在连接服务'))
const wsClass = computed(() => ({ connected: 'connected', reconnecting: 'reconnecting', closed: 'disconnected' }[wsState.state] || 'connecting'))

function changePage(id) {
  tab.value = id
  window.location.hash = `/${id}`
  drawerOpen.value = false
}
function onHashChange(event) {
  const hash = window.location.hash
  // #main-content 这类页内锚点不是路由，交给浏览器自己滚动，不能重置当前页面。
  if (hash && !hash.startsWith('#/')) return
  tab.value = pageFromHash(hash)
}
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('webot-sidebar', sidebarCollapsed.value ? 'collapsed' : 'expanded')
}
const drawerEl = ref(null)
function closeDrawer() { drawerOpen.value = false }
function onKeydown(event) {
  if (!drawerOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); return }
  if (event.key !== 'Tab' || !drawerEl.value) return
  const focusable = [...drawerEl.value.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]')]
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
watch(drawerOpen, async (open) => {
  if (open) {
    pushOverlay(drawerOverlayId)
    await nextTick()
    document.querySelector('.mobile-nav .nav-item')?.focus()
  } else {
    popOverlay(drawerOverlayId)
  }
})
function skipToMain(event) {
  event.preventDefault()
  const main = document.getElementById('main-content')
  main?.focus()
  main?.scrollIntoView({ block: 'start' })
}

onMounted(() => {
  startStatusPolling()
  on('msg', addMessage)
  on('log', addLog)
  on('snapshot', (data) => {
    if (data.messages) setMessages(data.messages)
    if (data.logs) setLogs(data.logs)
  })
  on('status', () => refreshStatus(true))
  connectWS()
  window.addEventListener('hashchange', onHashChange)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  stopStatusPolling()
  popOverlay(drawerOverlayId)
  window.removeEventListener('hashchange', onHashChange)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="app-shell">
    <a href="#main-content" class="skip-link" @click="skipToMain">跳到主要内容</a>
    <aside class="desktop-sidebar" :class="{ collapsed: sidebarCollapsed }"><Sidebar :active="tab" :collapsed="sidebarCollapsed" @change="changePage" /></aside>
    <Teleport to="body">
      <div v-if="drawerOpen" class="drawer-mask" @click.self="closeDrawer">
        <aside ref="drawerEl" class="mobile-nav" role="dialog" aria-modal="true" aria-label="控制台导航">
          <button class="icon-btn drawer-close" aria-label="关闭导航" @click="closeDrawer"><AppIcon name="X" /></button>
          <Sidebar :active="tab" @change="changePage" />
        </aside>
      </div>
    </Teleport>

    <div class="main-canvas">
      <header class="topbar">
        <button class="icon-btn mobile-menu" aria-label="打开导航" @click="drawerOpen = true"><AppIcon name="Menu" :size="21" /></button>
        <button class="icon-btn desktop-menu" :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar"><AppIcon :name="sidebarCollapsed ? 'PanelLeftOpen' : 'PanelLeftClose'" :size="17" /></button>
        <div class="mobile-brand"><BrandMark :size="28" /></div>
        <div class="page-heading"><AppIcon :name="page.icon" :size="18" /><h1>{{ page.label }}</h1><span>{{ page.description }}</span></div>
        <div class="top-actions">
          <span class="conn-status" :class="wsClass" role="status"><span class="status-dot"></span>{{ wsLabel }}</span>
          <span class="bridge-state" :class="{ online: status.running }">{{ status.running ? '服务运行中' : '服务未运行' }}</span>
          <button class="icon-btn" :aria-label="theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'" :title="theme === 'dark' ? '浅色主题' : '深色主题'" @click="toggleTheme"><AppIcon :name="theme === 'dark' ? 'Sun' : 'Moon'" :size="18" /></button>
        </div>
      </header>

      <main id="main-content" class="page" tabindex="-1">
        <Dashboard v-if="tab === 'dashboard'" />
        <Messages v-else-if="tab === 'messages'" />
        <Logs v-else-if="tab === 'logs'" />
        <Config v-else-if="tab === 'config'" />
        <Desktop v-else-if="tab === 'desktop'" />
        <Wizard v-else-if="tab === 'wizard'" />
        <TestSend v-else-if="tab === 'test'" />
      </main>
    </div>
    <ToastContainer />
  </div>
</template>
