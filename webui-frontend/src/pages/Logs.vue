<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { logs, loadInitialLogs, clearLogs } from '../store'
import AppIcon from '../components/AppIcon.vue'

const levels = ['INFO', 'WARNING', 'ERROR', 'DEBUG']
const enabled = ref({ INFO: true, WARNING: true, ERROR: true, DEBUG: true })
const search = ref('')
const autoScroll = ref(true)
const confirmingClear = ref(false)
const logBox = ref(null)
const filtered = computed(() => {
  const query = search.value.trim().toLowerCase()
  return logs.filter((log) => enabled.value[log.level] && (!query || String(log.text || '').toLowerCase().includes(query)))
})
const levelLabel = { INFO: '信息', WARNING: '警告', ERROR: '错误', DEBUG: '调试' }
async function scrollToBottom() { await nextTick(); if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight }
function onScroll() {
  if (!logBox.value) return
  autoScroll.value = logBox.value.scrollHeight - logBox.value.scrollTop - logBox.value.clientHeight < 30
}
function download() {
  const blob = new Blob([filtered.value.map((log) => log.text || '').join('\n')], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  const date = new Date()
  link.href = url
  link.download = `flandora-${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(date.getDate()).padStart(2, '0')}-${String(date.getHours()).padStart(2, '0')}${String(date.getMinutes()).padStart(2, '0')}.log`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
function confirmClear() {
  if (!confirmingClear.value) { confirmingClear.value = true; setTimeout(() => { confirmingClear.value = false }, 4000); return }
  clearLogs()
  confirmingClear.value = false
}
watch(() => filtered.value.length, () => { if (autoScroll.value) scrollToBottom() })
onMounted(() => { loadInitialLogs(300); scrollToBottom() })
</script>
<template>
  <div class="logs-page">
    <div class="page-intro"><div><h2>运行细节，清晰可见。</h2><p>检索实时日志，快速定位连接与消息问题。</p></div><button class="btn btn-outline" :disabled="!filtered.length" @click="download"><AppIcon name="ArrowDownToLine" :size="15" />导出日志</button></div>
    <section class="card log-toolbar" aria-label="日志筛选"><div class="log-search"><AppIcon name="Search" :size="16" /><input v-model="search" type="search" aria-label="搜索日志" placeholder="搜索日志内容" /></div><div class="toolbar-bottom"><div class="level-filters"><label v-for="level in levels" :key="level" class="level-filter" :class="[{ disabled: !enabled[level] }, level.toLowerCase()]"><input v-model="enabled[level]" type="checkbox" /><span>{{ levelLabel[level] }}</span></label></div><span class="spacer"></span><label class="chk auto-scroll"><input v-model="autoScroll" type="checkbox" />跟随最新</label><button class="btn btn-ghost btn-sm" @click="scrollToBottom"><AppIcon name="ArrowDownToLine" :size="14" />到底部</button></div></section>
    <section class="card log-panel"><div class="log-panel-head"><span><AppIcon name="TerminalSquare" :size="15" />实时输出</span><div><span>{{ filtered.length }} / {{ logs.length }} 行</span><button v-if="logs.length" class="clear-log" :class="{ confirming: confirmingClear }" @click="confirmClear"><AppIcon name="Trash2" :size="13" />{{ confirmingClear ? '确认清空' : '清空' }}</button></div></div><div ref="logBox" class="log-box mono" role="log" aria-label="运行日志" @scroll="onScroll"><div v-if="!logs.length" class="empty"><AppIcon name="TerminalSquare" :size="28" />等待日志输出…</div><div v-else-if="!filtered.length" class="empty">没有匹配当前筛选条件的日志</div><div v-for="(log, i) in filtered" :key="`${log.ts}-${i}`" class="log-line" :class="(log.level || 'INFO').toLowerCase()"><span class="log-level">{{ log.level || 'INFO' }}</span><span class="log-text">{{ log.text }}</span></div></div><div class="log-status"><span class="status-dot"></span>{{ autoScroll ? '正在跟随最新日志' : '已暂停跟随，浏览历史记录' }}<span class="spacer"></span><span class="log-cap">仅保留当前会话日志</span></div></section>
  </div>
</template>
<style scoped>
.logs-page { display:flex; flex-direction:column; gap:16px; height:100%; min-height:550px; }
.log-toolbar { padding:13px 16px; box-shadow:none; }
.log-search { display:flex; align-items:center; gap:10px; padding-bottom:12px; border-bottom:1px solid var(--border); color:var(--muted); }
.log-search input { flex:1; border:0; outline:0; background:transparent; color:var(--text-strong); font-size:12px; }
.toolbar-bottom { display:flex; align-items:center; gap:12px; padding-top:10px; }
.level-filters { display:flex; align-items:center; gap:5px; }
.level-filter { display:flex; align-items:center; gap:5px; min-height:24px; padding:4px 8px; border-radius:5px; background:var(--inset); font-size:10px; cursor:pointer; }
.level-filter.info { color:var(--info); background:var(--info-soft); }
.level-filter.warning { color:var(--warning); background:var(--warning-soft); }
.level-filter.error { color:var(--danger); background:var(--danger-soft); }
.level-filter.debug { color:var(--muted); }
.level-filter.disabled { background:transparent; color:var(--faint); text-decoration:line-through; }
.level-filter input { width:12px; height:12px; }
.auto-scroll { font-size:11px; }
.log-panel { display:flex; flex:1; flex-direction:column; min-height:390px; padding:0; overflow:hidden; box-shadow:none; }
.log-panel-head { display:flex; align-items:center; justify-content:space-between; padding:13px 17px; border-bottom:1px solid var(--border); font-size:11px; }
.log-panel-head > span { display:flex; align-items:center; gap:7px; font-weight:500; }
.log-panel-head > div { display:flex; align-items:center; gap:14px; color:var(--muted); font-size:10px; }
.clear-log { display:inline-flex; align-items:center; gap:4px; min-height:24px; padding:0 6px; border:0; border-radius:4px; background:transparent; color:var(--muted); font-size:10px; cursor:pointer; }
.clear-log:hover { background:var(--inset); color:var(--text-strong); }
.clear-log.confirming { color:var(--danger); }
.log-box { flex:1; min-height:300px; max-height:calc(100dvh - 360px); overflow:auto; padding:10px 16px; background:var(--inset); font-size:11px; line-height:1.75; }
.log-line { display:flex; gap:12px; padding:3px 0; }
.log-level { width:54px; flex-shrink:0; color:var(--muted); font-size:9px; line-height:1.9; }
.log-text { white-space:pre-wrap; overflow-wrap:anywhere; color:var(--text); }
.log-line.info .log-level { color:var(--info); }
.log-line.warning .log-level { color:var(--warning); }
.log-line.warning .log-text { color:var(--warning); }
.log-line.error .log-level, .log-line.error .log-text { color:var(--danger); }
.log-line.debug .log-text { color:var(--muted); }
.log-status { display:flex; align-items:center; gap:7px; padding:9px 17px; border-top:1px solid var(--border); color:var(--muted); font-size:9px; }
.log-status .status-dot { width:5px; height:5px; background:var(--primary); }
@media(max-width:767px) {
  .logs-page { min-height:0; }
  .toolbar-bottom { align-items:flex-start; flex-wrap:wrap; gap:7px; }
  .level-filters { width:100%; }
  .level-filter { flex:1; justify-content:center; padding-inline:5px; }
  .log-box { max-height:calc(100dvh - 400px); min-height:330px; padding:8px 10px; }
  .log-line { gap:8px; }
  .log-level { width:43px; font-size:8px; }
  .log-status > .log-cap { display:none; }
}
</style>
