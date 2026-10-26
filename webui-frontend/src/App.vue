<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import Sidebar from './components/Sidebar.vue'
import AppIcon from './components/AppIcon.vue'
import BrandMark from './components/BrandMark.vue'
import ToastContainer from './components/ToastContainer.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import Login from './pages/Login.vue'
import { get } from './api'
import { wsState, connectWS, closeWS, on } from './ws'
import { startStatusPolling, stopStatusPolling, refreshStatus, setMessages, setLogs, addMessage, addLog, status } from './store'
import { PAGE_MAP, pageFromHash } from './pages/index.js'
import { canLeave, leavePromptOpen, resolveLeave } from './composables/useDirtyGuard'
import { useTheme } from './useTheme'
import { pushOverlay, popOverlay } from './overlayStack'

const drawerOverlayId = Symbol('drawer')

const tab = ref(pageFromHash(window.location.hash))
const drawerOpen = ref(false)
const SIDEBAR_KEY = 'flandora-sidebar'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_KEY) === 'collapsed')
const { theme, toggleTheme } = useTheme()
const page = computed(() => PAGE_MAP[tab.value])
const wsLabel = computed(() => ({ connected: '实时连接正常', reconnecting: '正在重新连接', closed: '实时连接已断开' }[wsState.state] || '正在连接服务'))
const wsClass = computed(() => ({ connected: 'connected', reconnecting: 'reconnecting', closed: 'disconnected' }[wsState.state] || 'connecting'))

// 用户选「留在本页」时把地址栏拨回当前页：被拦下的那个 hash 已经进了历史，
// 不还原就会出现「地址栏换了页、内容没换」的错位。
function restoreHash() {
  const hash = `#/${tab.value}`
  if (window.location.hash !== hash) window.location.hash = `/${tab.value}`
}

async function changePage(id) {
  if (id !== tab.value && !(await canLeave())) {
    restoreHash()
    return
  }
  tab.value = id
  window.location.hash = `/${id}`
  drawerOpen.value = false
}
function openAbout() {
  window.location.hash = '/config?section=about'
  drawerOpen.value = false
}
async function onHashChange() {
  const hash = window.location.hash
  // #main-content 这类页内锚点不是路由，交给浏览器自己滚动，不能重置当前页面。
  if (hash && !hash.startsWith('#/')) return
  const next = pageFromHash(hash)
  if (next === tab.value) return
  if (!(await canLeave())) {
    restoreHash()
    return
  }
  tab.value = next
}
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed.value ? 'collapsed' : 'expanded')
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

// 面板设了密码时，未登录不拉数据也不连实时流：拿到的只会是 401。
const auth = ref({ checked: false, enabled: false, authenticated: true })
const authed = computed(() => !auth.value.enabled || auth.value.authenticated)
const unsubscribers = []
let booted = false

// 后台标签页里轮询会被跳过，切回前台补一次，别让顶栏状态停在旧值上。
function onVisibilityChange() {
  if (!document.hidden && booted) refreshStatus(true)
}

function boot() {
  if (booted) return
  booted = true
  startStatusPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
  unsubscribers.push(
    on('msg', addMessage),
    on('log', addLog),
    on('snapshot', (data) => {
      if (data.messages) setMessages(data.messages)
      if (data.logs) setLogs(data.logs)
    }),
    on('status', () => refreshStatus(true))
  )
  connectWS()
}

// 切回登录页时要把轮询、实时流与订阅一起停掉：只换界面的话，后台会继续
// 每 5 秒打一次 401，WS 也会无限重连，登录页上什么都看不到却一直在发请求。
function shutdown() {
  stopStatusPolling()
  closeWS()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  while (unsubscribers.length) unsubscribers.pop()()
  booted = false
}

async function loadAuth() {
  const res = await get('/api/auth', true)
  const data = res.data || {}
  auth.value = { checked: true, enabled: !!data.enabled, authenticated: !!data.authenticated }
  if (authed.value) boot()
}

function onAuthRequired() {
  auth.value = { checked: true, enabled: true, authenticated: false }
  shutdown()
}

async function onLoggedIn() {
  auth.value = { ...auth.value, checked: true, authenticated: true }
  await refreshStatus(true)
  boot()
}

onMounted(async () => {
  window.addEventListener('hashchange', onHashChange)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('flandora:auth-required', onAuthRequired)
  await loadAuth()
})
onBeforeUnmount(() => {
  shutdown()
  popOverlay(drawerOverlayId)
  window.removeEventListener('hashchange', onHashChange)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('flandora:auth-required', onAuthRequired)
})
</script>

<template>
  <Login v-if="auth.checked && !authed" @logged-in="onLoggedIn" />
  <div v-else-if="auth.checked" class="app-shell">
    <a href="#main-content" class="skip-link" @click="skipToMain">跳到主要内容</a>
    <aside class="desktop-sidebar" :class="{ collapsed: sidebarCollapsed }"><Sidebar :active="tab" :collapsed="sidebarCollapsed" @change="changePage" @about="openAbout" /></aside>
    <Teleport to="body">
      <div v-if="drawerOpen" class="drawer-mask" @click.self="closeDrawer">
        <aside ref="drawerEl" class="mobile-nav" role="dialog" aria-modal="true" aria-label="面板导航">
          <button class="icon-btn drawer-close" aria-label="关闭导航" @click="closeDrawer"><AppIcon name="X" /></button>
          <Sidebar :active="tab" @change="changePage" @about="openAbout" />
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
        <component :is="page.component" />
      </main>
    </div>
    <ToastContainer />
    <ConfirmDialog
      :open="leavePromptOpen"
      title="还有没保存的改动"
      message="这个页面还有没保存的改动，现在离开会丢掉它们。"
      confirm-text="放弃改动并离开"
      cancel-text="留在本页"
      danger
      @confirm="resolveLeave(true)"
      @cancel="resolveLeave(false)"
    />
  </div>
  <div v-else class="app-boot" role="status">正在检查登录状态…</div>
</template>
