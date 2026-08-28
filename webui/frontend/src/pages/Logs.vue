<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { logs, loadInitialLogs, clearLogs } from '../store'

const levels = ['INFO', 'WARNING', 'ERROR', 'DEBUG']
const enabled = ref({ INFO: true, WARNING: true, ERROR: true, DEBUG: true })
const search = ref('')
const autoScroll = ref(true)
const logBox = ref(null)

const levelCls = {
  INFO: 'lv-info',
  WARNING: 'lv-warning',
  ERROR: 'lv-error',
  DEBUG: 'lv-debug',
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return logs.filter((l) => {
    if (!enabled.value[l.level]) return false
    if (q && !String(l.text || '').toLowerCase().includes(q)) return false
    return true
  })
})

async function scrollToBottom() {
  await nextTick()
  const el = logBox.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = logBox.value
  if (!el) return
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
  if (autoScroll.value && !atBottom) autoScroll.value = false
  else if (!autoScroll.value && atBottom) autoScroll.value = true
}

function download() {
  const text = filtered.value.map((l) => l.text || '').join('\n')
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const d = new Date()
  a.href = url
  a.download = `maibot-bridge-${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}.log`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

watch(() => filtered.value.length, () => {
  if (autoScroll.value) scrollToBottom()
})

onMounted(() => {
  loadInitialLogs(300)
  scrollToBottom()
})
</script>

<template>
  <div class="logs">
    <div class="card filter-bar">
      <div class="level-filters">
        <label class="chk" v-for="lv in levels" :key="lv">
          <input type="checkbox" v-model="enabled[lv]" />
          <span class="lbl" :class="levelCls[lv]">{{ lv }}</span>
        </label>
      </div>
      <input v-model="search" class="input search" type="text" placeholder="搜索日志内容..." />
      <label class="auto">
        <input type="checkbox" v-model="autoScroll" />
        <span>自动滚动</span>
      </label>
      <div class="spacer"></div>
      <button class="btn btn-ghost btn-sm" @click="clearLogs()">🧹 清空</button>
      <button class="btn btn-blue btn-sm" @click="download()">⬇ 下载</button>
    </div>

    <div class="card log-panel">
      <div ref="logBox" class="log-box mono" @scroll="onScroll">
        <div v-if="!logs.length" class="empty">等待日志...</div>
        <div v-else-if="!filtered.length" class="empty">没有匹配的日志</div>
        <div
          v-for="(l, i) in filtered"
          :key="i"
          class="log-line"
          :class="levelCls[l.level] || 'lv-info'"
        >
          {{ l.text }}
        </div>
      </div>
      <div class="log-meta">{{ filtered.length }} / {{ logs.length }} 行</div>
    </div>
  </div>
</template>

<style scoped>
.logs {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 12px 16px;
}
.level-filters {
  display: flex;
  align-items: center;
  gap: 14px;
}
.chk {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
  font-weight: 600;
}
.chk input {
  accent-color: var(--pink);
}
.search {
  width: 220px;
}
.auto {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
}
.auto input {
  accent-color: var(--pink);
}
.log-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 10px;
}
.log-box {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #faf5f7;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.log-line {
  padding: 1px 0;
}
.lv-info { color: #557a57; }
.lv-warning { color: #b26a00; }
.lv-error { color: #c62828; }
.lv-debug { color: #9e9e9e; }
.log-meta {
  padding: 8px 4px 0;
  font-size: 11px;
  color: var(--faint);
}
</style>
