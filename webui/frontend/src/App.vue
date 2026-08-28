<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ToastContainer from './components/ToastContainer.vue'
import Dashboard from './pages/Dashboard.vue'
import Messages from './pages/Messages.vue'
import Logs from './pages/Logs.vue'
import Config from './pages/Config.vue'
import TestSend from './pages/TestSend.vue'

import { wsState, connectWS, on } from './ws'
import { startStatusPolling, stopStatusPolling, refreshStatus, setMessages, setLogs, addMessage, addLog } from './store'

const tab = ref('dashboard')

const wsLabel = computed(() => {
  switch (wsState.state) {
    case 'connected':
      return 'WebSocket 已连接'
    case 'reconnecting':
      return 'WebSocket 重连中'
    case 'closed':
      return 'WebSocket 已断开'
    default:
      return 'WebSocket 连接中'
  }
})

const wsClass = computed(() => {
  switch (wsState.state) {
    case 'connected':
      return 'dot-connected'
    case 'reconnecting':
      return 'dot-reconnecting'
    case 'closed':
      return 'dot-disconnected'
    default:
      return 'dot-connecting'
  }
})

onMounted(() => {
  startStatusPolling()

  on('msg', (d) => addMessage(d))
  on('log', (d) => addLog(d))
  on('snapshot', (d) => {
    if (d.messages) setMessages(d.messages)
    if (d.logs) setLogs(d.logs)
  })
  on('status', () => refreshStatus(true))

  connectWS()
})

onBeforeUnmount(() => {
  stopStatusPolling()
})
</script>

<template>
  <div class="app">
    <Sidebar :active="tab" @change="(t) => (tab = t)" />

    <div class="main">
      <header class="topbar">
        <div class="title-wrap">
          <h1 class="title">MaiBot Bridge</h1>
          <div class="subtitle">微信 ↔ 麦麦 桥接控制台</div>
        </div>
        <div class="conn">
          <span class="dot" :class="wsClass"></span>
          <span class="conn-text">{{ wsLabel }}</span>
        </div>
      </header>

      <main class="page">
        <Dashboard v-if="tab === 'dashboard'" />
        <Messages v-else-if="tab === 'messages'" />
        <Logs v-else-if="tab === 'logs'" />
        <Config v-else-if="tab === 'config'" />
        <TestSend v-else-if="tab === 'test'" />
      </main>
    </div>

    <ToastContainer />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 28px 12px;
}
.title-wrap {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.title {
  font-size: 24px;
  font-weight: 800;
  background: linear-gradient(135deg, #e8436e, var(--pink));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: 0.5px;
}
.subtitle {
  font-size: 13px;
  color: var(--muted);
  font-weight: 500;
}
.conn {
  display: flex;
  align-items: center;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 7px 14px;
  font-size: 12px;
  color: var(--muted);
  box-shadow: var(--shadow);
  white-space: nowrap;
}
.page {
  flex: 1;
  overflow-y: auto;
  padding: 6px 28px 28px;
}
</style>
