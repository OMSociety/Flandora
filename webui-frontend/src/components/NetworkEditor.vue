<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  legacy: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const TYPES = [
  { value: 'ws_server', label: 'WS 服务端' },
  { value: 'http_server', label: 'HTTP 服务端' },
  { value: 'ws_client', label: '反向 WS' },
  { value: 'http_client', label: 'HTTP 上报' },
]
const ROLES = [
  { value: 'universal', label: '两者都做' },
  { value: 'api', label: '只收动作' },
  { value: 'event', label: '只推事件' },
]
const rows = computed(() => (Array.isArray(props.modelValue) ? props.modelValue : []))

function isServer(node) { return node.type === 'ws_server' || node.type === 'http_server' }

function blank(type, index) {
  const base = {
    type,
    name: `${type}-${index + 1}`,
    enabled: true,
    role: 'universal',
    access_token: '',
    message_format: '',
  }
  if (type === 'ws_server') return { ...base, host: '127.0.0.1', port: 7999, path: '/' }
  if (type === 'http_server') return { ...base, host: '127.0.0.1', port: 5700, path: '/' }
  if (type === 'ws_client') return { ...base, url: 'ws://127.0.0.1:6199/ws', reconnect_interval_ms: 5000 }
  return { ...base, url: 'http://127.0.0.1:8080/onebot', timeout_ms: 5000 }
}

function update(next) { emit('update:modelValue', next) }
function patch(index, key, value) {
  update(rows.value.map((node, i) => (i === index ? { ...node, [key]: value } : node)))
}
function addNode(type) { update([...rows.value, blank(type, rows.value.length)]) }
function removeNode(index) { update(rows.value.filter((_, i) => i !== index)) }
function useLegacy() {
  const port = Number(props.legacy.port) || 7999
  update([
    {
      type: 'ws_server',
      name: 'default',
      enabled: true,
      host: props.legacy.host || '127.0.0.1',
      port,
      path: '/',
      role: 'universal',
      access_token: props.legacy.token || '',
      message_format: '',
    },
  ])
}
</script>

<template>
  <div class="net">
    <div v-if="!rows.length" class="net-empty">
      没有单独配置网络入口，桥接会按上面的 Host 与端口起一个 WS 服务端。
    </div>
    <div v-for="(node, i) in rows" :key="i" class="net-row" :class="{ off: node.enabled === false }">
      <select class="input net-type" :value="node.type" aria-label="入口类型" @change="patch(i, 'type', $event.target.value)">
        <option v-for="t in TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <input class="input net-name" :value="node.name" placeholder="名字" aria-label="入口名字" @input="patch(i, 'name', $event.target.value)" />
      <template v-if="isServer(node)">
        <input class="input net-host" :value="node.host" placeholder="127.0.0.1" aria-label="监听地址" @input="patch(i, 'host', $event.target.value)" />
        <input class="input net-port" type="number" min="1" max="65535" :value="node.port" aria-label="端口" @input="patch(i, 'port', Number($event.target.value) || 0)" />
      </template>
      <input v-else class="input net-url" :value="node.url" placeholder="ws:// 或 http:// 地址" aria-label="目标地址" @input="patch(i, 'url', $event.target.value)" />
      <select class="input net-role" :value="node.role" aria-label="角色" @change="patch(i, 'role', $event.target.value)">
        <option v-for="r in ROLES" :key="r.value" :value="r.value">{{ r.label }}</option>
      </select>
      <input class="input net-token" :value="node.access_token" placeholder="令牌（可空）" aria-label="访问令牌" @input="patch(i, 'access_token', $event.target.value)" />
      <button type="button" class="net-del" title="删除这个入口" aria-label="删除这个入口" @click="removeNode(i)">
        <AppIcon name="Trash2" :size="15" />
      </button>
    </div>
    <div class="net-actions">
      <button v-for="t in TYPES" :key="t.value" type="button" class="btn btn-outline btn-sm" @click="addNode(t.value)">
        <AppIcon name="Plus" :size="14" />{{ t.label }}
      </button>
      <button v-if="!rows.length" type="button" class="btn btn-outline btn-sm" @click="useLegacy">用上面的 Host 与端口生成一个</button>
    </div>
    <p class="hint">同一个入口可同时挂多个，改完需要重启桥接生效。反向 WS 与 HTTP 上报填的地址是机器人框架那一侧。</p>
  </div>
</template>

<style scoped>
.net { display:flex; flex-direction:column; gap:8px; margin-top:10px; }
.net-empty { padding:10px 12px; border:1px dashed var(--border); border-radius:8px; color:var(--muted); font-size:12.5px; }
.net-row { display:grid; grid-template-columns:104px 116px minmax(0,1.4fr) 96px minmax(0,1fr) 32px; gap:6px; align-items:center; }
.net-row.off { opacity:.55; }
.net-row .input { height:34px; font-size:12.5px; }
.net-port { max-width:96px; }
.net-del { display:grid; place-items:center; width:32px; height:32px; border:0; border-radius:6px; background:transparent; color:var(--muted); cursor:pointer; }
.net-del:hover { background:var(--inset); color:var(--text-strong); }
.net-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:2px; }
@media (max-width: 860px) {
  .net-row { grid-template-columns:1fr 1fr; }
  .net-url, .net-name { grid-column:span 2; }
}
</style>
