<script setup>
import { ref, computed, onMounted } from 'vue'
import { messages, loadInitialMessages, clearMessages } from '../store'
import { formatClock, kindLabel } from '../utils/format'
import AppIcon from '../components/AppIcon.vue'

const dir = ref('')
const kind = ref('')
const search = ref('')
const confirmingClear = ref(false)
const kindCls = { private: 'badge-green', group: 'badge-blue', manual: 'badge-amber' }
const filtered = computed(() => {
  const query = search.value.trim().toLowerCase()
  return messages.filter((message) => {
    if (dir.value && message.dir !== dir.value) return false
    if (kind.value && message.kind !== kind.value) return false
    if (query && !`${message.contact || ''} ${message.sender || ''} ${message.text || ''}`.toLowerCase().includes(query)) return false
    return true
  })
})
function confirmClear() {
  if (!confirmingClear.value) { confirmingClear.value = true; setTimeout(() => { confirmingClear.value = false }, 4000); return }
  clearMessages()
  confirmingClear.value = false
}
onMounted(() => loadInitialMessages(200))
</script>
<template>
  <div class="messages-page">
    <div class="page-tools"><span class="live-label"><i></i>实时更新</span></div>
    <section class="card filter-panel" aria-label="消息筛选"><div class="search-field"><AppIcon name="Search" :size="17" /><input v-model="search" type="search" aria-label="搜索消息" placeholder="搜索联系人、发送者或消息内容" /><span v-if="search" class="result-count">{{ filtered.length }} 条结果</span></div><div class="filter-bottom"><div class="seg" role="group" aria-label="按消息方向筛选"><button :class="{ active: dir === '' }" @click="dir = ''">全部</button><button :class="{ active: dir === 'in' }" @click="dir = 'in'">接收</button><button :class="{ active: dir === 'out' }" @click="dir = 'out'">发送</button></div><select v-model="kind" class="select kind-filter" aria-label="按会话类型筛选"><option value="">全部类型</option><option value="private">私聊</option><option value="group">群聊</option><option value="manual">手动</option></select><span class="spacer"></span><button v-if="messages.length" class="btn btn-ghost btn-sm clear-btn" :class="{ confirming: confirmingClear }" title="只清空这一页显示的消息，本地存档与推送记录都不动（要删存档请到「存档」页）" @click="confirmClear"><AppIcon name="Trash2" :size="14" />{{ confirmingClear ? '再点一次：只清页面，存档不动' : '清空当前列表' }}</button></div></section>
    <section class="card feed-panel" aria-label="消息列表"><div class="feed-head"><h3>消息流</h3><span>{{ filtered.length }} / {{ messages.length }} 条</span></div><div v-if="!messages.length" class="empty"><AppIcon name="MessagesSquare" :size="30" /><strong>还没有消息</strong><span>桥接收到或发送消息后，会实时出现在这里。</span></div><div v-else-if="!filtered.length" class="empty"><AppIcon name="Search" :size="28" /><strong>没有匹配的消息</strong><span>试试调整方向、类型或搜索关键词。</span></div><div v-else class="message-list"><article v-for="(m, i) in filtered" :key="`${m.ts}-${i}`" class="message-item"><div class="message-avatar" :class="m.dir"><AppIcon :name="m.dir === 'in' ? 'ArrowDownToLine' : 'ArrowUpRight'" :size="17" /></div><div class="message-body"><div class="message-meta"><strong>{{ m.contact || '未知联系人' }}</strong><span v-if="m.sender" class="sender">{{ m.sender }}</span><span v-else-if="m.dir === 'out'" class="sender">我</span><span class="badge" :class="kindCls[m.kind] || 'badge-gray'">{{ kindLabel(m.kind) }}</span><time class="mono">{{ formatClock(m.ts) }}</time></div><p>{{ m.text }}</p></div><span class="direction-label" :class="m.dir">{{ m.dir === 'in' ? '接收' : '发送' }}</span></article></div></section>
  </div>
</template>
<style scoped>
.messages-page { display:flex; flex-direction:column; gap:16px; }
.live-label { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:11px; white-space:nowrap; }
.live-label i { width:6px; height:6px; border-radius:50%; background:var(--primary); }
.filter-panel { padding:13px 16px; box-shadow:none; }
.search-field { display:flex; align-items:center; gap:10px; padding:3px 0 13px; border-bottom:1px solid var(--border); color:var(--muted); }
.search-field input { flex:1; min-width:0; border:0; outline:0; background:transparent; color:var(--text-strong); font-size:12px; }
.search-field input::placeholder { color:var(--faint); }
.result-count { font-size:10px; white-space:nowrap; }
.filter-bottom { display:flex; align-items:center; gap:10px; padding-top:12px; }
.kind-filter { width:130px; min-height:34px; padding-block:5px; font-size:11px; }
.clear-btn.confirming { color:var(--danger); background:var(--danger-soft); }
.feed-panel { padding:0; box-shadow:none; overflow:hidden; }
.feed-head { display:flex; align-items:center; justify-content:space-between; padding:17px 20px; border-bottom:1px solid var(--border); }
.feed-head h3 { font-size:13px; font-weight:600; }
.feed-head span { color:var(--muted); font-size:10px; }
.message-item { display:flex; align-items:flex-start; gap:13px; padding:16px 20px; border-bottom:1px solid var(--border); }
.message-item:last-child { border:0; }
.message-item:hover { background:var(--inset); }
.message-avatar { display:grid; place-items:center; width:32px; height:32px; flex-shrink:0; border-radius:9px; color:var(--primary-deep); background:var(--primary-soft); }
.message-avatar.out { color:var(--info); background:var(--info-soft); }
.message-body { flex:1; min-width:0; }
.message-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.message-meta strong { max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; font-weight:600; color:var(--text-strong); }
.sender { color:var(--muted); font-size:10px; }
.message-meta time { margin-left:auto; color:var(--muted); font-size:10px; }
.message-body p { margin-top:5px; font-size:12px; line-height:1.7; color:var(--text); white-space:pre-wrap; overflow-wrap:anywhere; }
.direction-label { flex-shrink:0; margin-top:3px; font-size:10px; color:var(--success); }
.direction-label.out { color:var(--info); }
@media(max-width:767px) {
  .filter-panel { padding:12px; }
  .filter-bottom { flex-wrap:wrap; }
  .kind-filter { flex:1; min-width:110px; }
  .clear-btn { padding-inline:6px; }
  .feed-head { padding:15px; }
  .message-item { gap:10px; padding:14px; }
  .message-avatar { width:28px; height:28px; }
  .message-meta { gap:5px; }
  .message-meta time { order:2; }
  .direction-label { display:none; }
  .message-body p { font-size:12px; }
}
</style>
