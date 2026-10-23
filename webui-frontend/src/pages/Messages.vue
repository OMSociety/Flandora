<script setup>
import { ref, computed, onMounted } from 'vue'
import { messages, loadInitialMessages, clearMessages } from '../store'
import { formatClock, kindLabel } from '../utils/format'

const dir = ref('')
const kind = ref('')
const search = ref('')

const kindCls = { private: 'badge-pink', group: 'badge-blue', manual: 'badge-amber' }

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return messages.filter((m) => {
    if (dir.value && m.dir !== dir.value) return false
    if (kind.value && m.kind !== kind.value) return false
    if (q) {
      const hay = `${m.contact || ''} ${m.sender || ''} ${m.text || ''}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

onMounted(() => {
  loadInitialMessages(200)
})
</script>

<template>
  <div class="messages">
    <div class="card filter-bar">
      <div class="filter-field">
        <label>方向</label>
        <select v-model="dir" class="select">
          <option value="">全部</option>
          <option value="in">接收</option>
          <option value="out">发送</option>
        </select>
      </div>
      <div class="filter-field">
        <label>类型</label>
        <select v-model="kind" class="select">
          <option value="">全部</option>
          <option value="private">私聊</option>
          <option value="group">群聊</option>
          <option value="manual">手动</option>
        </select>
      </div>
      <div class="filter-field grow">
        <label>搜索</label>
        <input v-model="search" class="input" type="text" placeholder="搜索联系人 / 内容..." />
      </div>
      <button class="btn btn-ghost btn-sm" @click="clearMessages()">🧹 清空</button>
      <span class="count">{{ filtered.length }} 条 / 共 {{ messages.length }} 条</span>
    </div>

    <div class="card feed">
      <div v-if="!messages.length" class="empty">暂无消息记录</div>
      <div v-else-if="!filtered.length" class="empty">没有匹配的消息</div>
      <div v-else class="msg-list">
        <div v-for="(m, i) in filtered" :key="i" class="msg-item" :class="m.dir">
          <span class="time mono">{{ formatClock(m.ts) }}</span>
          <span class="badge" :class="m.dir === 'in' ? 'badge-pink' : 'badge-blue'">
            {{ m.dir === 'in' ? '接收' : '发送' }}
          </span>
          <span class="badge" :class="kindCls[m.kind] || 'badge-gray'">{{ kindLabel(m.kind) }}</span>
          <span class="contact">
            {{ m.contact || '--' }}
            <span v-if="m.sender" class="sender">· {{ m.sender }}</span>
            <span v-else-if="m.dir === 'out'" class="sender">· 我</span>
          </span>
          <span class="text">{{ m.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.messages {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.filter-bar {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  flex-wrap: wrap;
  padding: 14px 16px;
}
.filter-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.filter-field label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
}
.filter-field.grow {
  flex: 1;
  min-width: 200px;
}
.count {
  align-self: center;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  padding-bottom: 4px;
}
.feed {
  padding: 8px;
}
.msg-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}
.msg-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 10px;
  font-size: 12.5px;
}
.msg-item.in { background: #fdf2f5; }
.msg-item.out { background: #f5f9ff; }
.time {
  color: var(--faint);
  font-size: 11px;
  flex-shrink: 0;
}
.contact {
  font-weight: 600;
  color: var(--text-strong);
  flex-shrink: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sender {
  font-weight: 400;
  color: var(--muted);
  font-size: 11px;
}
.text {
  flex: 1;
  color: var(--text);
  word-break: break-word;
}
</style>
