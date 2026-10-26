<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { get } from '../api'
import { refreshStatus, status, toast } from '../store'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import PasswordInput from '../components/PasswordInput.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import NodeDialog from '../components/nodes/NodeDialog.vue'
import { NODE_TYPES, blankNode, nodeTarget, stateInfo, typeMeta, uniqueName } from '../components/nodes/nodeMeta'
import { useSettingsSave } from '../composables/useSettingsSave'
import { useDirtyGuard } from '../composables/useDirtyGuard'

// 没有单独配置入口时，桥接按这几个值造一个 WS 服务端——「通用设置」标签页管的就是它们。
// 附件目录放在这张卡里：它属于本机微信客户端，与入口本身无关。
const GENERAL_KEYS = ['ob_server_host', 'ob_server_port', 'ob_server_token', 'ob_message_format', 'ob_heartbeat_seconds', 'client_attachments']
const DEFAULTS = { ob_server_host: '127.0.0.1', ob_server_port: 7999, ob_server_token: '', ob_message_format: 'array', ob_heartbeat_seconds: 30, client_attachments: '' }

const general = reactive({ ...DEFAULTS })
const draft = ref([])
const baseline = ref({ general: {}, networks: [] })
const activeType = ref('general')
const editing = ref(null)
const editingNode = ref(null)
const pendingRemove = ref(null)
const loaded = ref(false)
const loadError = ref('')
const { saving, saved, save: putSettings } = useSettingsSave()
const { markDirty, clearDirty } = useDirtyGuard()

const self = computed(() => status.self || {})
const initials = computed(() => (self.value.nickname || self.value.wxid || '微').slice(0, 1))
const runtime = computed(() => (Array.isArray(status.ob?.nodes) ? status.ob.nodes : []))
const stale = computed(() => JSON.stringify(draft.value) !== JSON.stringify(baseline.value.networks))
const dirty = computed(() => stale.value || GENERAL_KEYS.some((key) => String(general[key] ?? '') !== String(baseline.value.general[key] ?? '')))
// 离开守卫只认这个 dirty：草稿与已保存条目的差异，和顶栏「有未保存的改动」同一判据。
// 保存成功会重新 load()，草稿回到基线，dirty 自然变回 false。
watch(dirty, (value) => (value ? markDirty() : clearDirty()))
const connections = computed(() => runtime.value.reduce((sum, node) => sum + (node.connections || 0), 0))
const enabledCount = computed(() => draft.value.filter((node) => node.enabled !== false).length)
const counts = computed(() => Object.fromEntries(NODE_TYPES.map((type) => [type.value, draft.value.filter((node) => node.type === type.value).length])))
const activeMeta = computed(() => typeMeta(activeType.value))
const endpoint = computed(() => `ws://${general.ob_server_host || '127.0.0.1'}:${general.ob_server_port || 7999}`)
// 草稿与已保存的一致时才敢用运行态的行（多一个没保存的入口就会串行）。
const rows = computed(() => {
  const live = stale.value ? [] : runtime.value
  return draft.value
    .map((node, index) => ({ node, index }))
    .filter(({ node }) => node.type === activeType.value)
    .map(({ node, index }) => {
      const match = live[index] && live[index].type === node.type && live[index].name === node.name ? live[index] : null
      const info = match ? stateInfo(match.state, node.enabled) : stateInfo('', node.enabled)
      return {
        node, index, match, target: nodeTarget(node),
        stateLabel: match ? info.label : (node.enabled === false ? '未启用' : '待保存'),
        tone: match ? info.tone : (node.enabled === false ? 'off' : 'warn'),
        connections: match ? match.connections || 0 : 0,
        error: match && match.error ? match.error : '',
      }
    })
})

function snapshotGeneral() {
  return Object.fromEntries(GENERAL_KEYS.map((key) => [key, general[key]]))
}

async function load() {
  loadError.value = ''
  const res = await get('/api/config')
  if (!res.ok || !res.data || typeof res.data !== 'object' || Array.isArray(res.data)) {
    loadError.value = res.data?.error || (res.status ? `HTTP ${res.status}` : '无法连接桥接服务')
    toast('加载节点配置失败：' + loadError.value, 'error')
    return
  }
  const data = res.data
  GENERAL_KEYS.forEach((key) => { general[key] = data[key] ?? DEFAULTS[key] })
  draft.value = Array.isArray(data.ob_networks) ? JSON.parse(JSON.stringify(data.ob_networks)) : []
  baseline.value = { general: snapshotGeneral(), networks: JSON.parse(JSON.stringify(draft.value)) }
  loaded.value = true
}

async function save() {
  const payload = {
    ob_server_host: general.ob_server_host,
    ob_server_port: Number(general.ob_server_port) || 0,
    ob_server_token: general.ob_server_token,
    ob_message_format: general.ob_message_format,
    ob_heartbeat_seconds: Number(general.ob_heartbeat_seconds) || 0,
    client_attachments: general.client_attachments,
    ob_networks: draft.value,
  }
  if (await putSettings(payload)) {
    await load()
    await refreshStatus(true)
  }
}

function setEnabled(index, value) {
  draft.value = draft.value.map((node, i) => (i === index ? { ...node, enabled: value } : node))
}
function openEdit(index) {
  editing.value = index
  editingNode.value = JSON.parse(JSON.stringify(draft.value[index]))
}
function applyEdit(node) {
  const index = editing.value
  draft.value = draft.value.map((item, i) => (i === index ? node : item))
  editing.value = null
  editingNode.value = null
}
function addNode(type) {
  const node = blankNode(type, draft.value.length)
  node.name = uniqueName(type, draft.value)
  draft.value = [...draft.value, node]
  activeType.value = type
  openEdit(draft.value.length - 1)
}
function confirmRemove() {
  draft.value = draft.value.filter((_, i) => i !== pendingRemove.value)
  pendingRemove.value = null
}
// 把「通用设置」造出来的那个隐式入口落成一个真入口，行为不变而多出一份可单独调的配置。
function materializeGeneral() {
  const node = blankNode('ws_server', draft.value.length)
  node.name = uniqueName('ws_server', draft.value)
  node.host = general.ob_server_host || '127.0.0.1'
  node.port = Number(general.ob_server_port) || 7999
  node.access_token = general.ob_server_token || ''
  draft.value = [...draft.value, node]
  activeType.value = 'ws_server'
  openEdit(draft.value.length - 1)
}

onMounted(load)
</script>

<template>
  <div class="nodes-page">
    <div v-if="!loaded && !loadError" class="card empty"><AppIcon name="LoaderCircle" :size="25" />正在读取节点配置…</div>
    <div v-else-if="loadError" class="card load-error">
      <AppIcon name="CircleAlert" :size="26" />
      <h3>无法读取节点配置</h3>
      <p>{{ loadError }}</p>
      <p class="hint">为避免用空值覆盖 config.json，入口编辑已停用。请确认桥接服务正在运行后重试。</p>
      <button class="btn btn-outline" @click="load"><AppIcon name="RefreshCw" :size="15" />重新读取</button>
    </div>

    <div v-else class="nodes-layout">
      <aside class="nodes-side">
        <section class="card account-card" aria-labelledby="account-heading">
          <h3 id="account-heading" class="card-title"><AppIcon name="Activity" :size="17" />本机账号</h3>
          <div class="account-row">
            <span class="avatar">{{ initials }}</span>
            <div class="account-name">
              <strong>{{ self.nickname || '还没有读到昵称' }}</strong>
              <small class="mono">{{ self.wxid || '—' }}</small>
            </div>
            <span class="status-dot" :class="{ on: status.running }" :title="status.running ? '桥接运行中' : '桥接未运行'"></span>
          </div>
          <ul class="account-stats">
            <li><span>入口</span><strong class="mono">{{ draft.length }}</strong></li>
            <li><span>启用</span><strong class="mono">{{ enabledCount }}</strong></li>
            <li><span>在线连接</span><strong class="mono">{{ connections }}</strong></li>
          </ul>
          <div class="field attach-field">
            <label for="attach-dir">客户端附件目录</label>
            <input id="attach-dir" v-model="general.client_attachments" class="input" placeholder="C:/path/to/wechat/attachments" />
            <p class="hint">微信收到的文件放在哪，用于把事件里的 file 段解析成真实路径。保存后立刻生效。</p>
          </div>
        </section>
      </aside>

      <section class="card nodes-card" aria-labelledby="nodes-heading">
        <header class="nodes-head">
          <div>
            <h3 id="nodes-heading" class="card-title">OneBot 协议端点</h3>
            <p class="hint mono">UIN {{ self.wxid || '—' }} · 共 {{ draft.length }} 个入口</p>
          </div>
          <div class="head-actions">
            <span v-if="dirty" class="dirty-flag">有未保存的改动</span>
            <button v-if="activeType !== 'general'" class="btn btn-outline" @click="addNode(activeType)">
              <AppIcon name="Plus" :size="15" />新建{{ activeMeta.tab }}
            </button>
            <button class="btn save-btn" :disabled="!dirty || saving" @click="save">
              <AppIcon :name="saving ? 'LoaderCircle' : saved ? 'Check' : 'CheckCheck'" :size="16" />
              {{ saving ? '正在保存…' : saved ? '已保存' : '保存' }}
            </button>
          </div>
        </header>

        <div class="node-tabs" role="tablist" aria-label="入口类型">
          <button type="button" role="tab" :aria-selected="activeType === 'general' ? 'true' : 'false'" :class="{ active: activeType === 'general' }" @click="activeType = 'general'">通用设置</button>
          <button
            v-for="type in NODE_TYPES" :key="type.value" type="button" role="tab"
            :aria-selected="activeType === type.value ? 'true' : 'false'"
            :class="{ active: activeType === type.value }"
            @click="activeType = type.value"
          >
            <AppIcon :name="type.icon" :size="14" />{{ type.tab }}<span class="tab-count mono">{{ counts[type.value] }}</span>
          </button>
        </div>

        <div v-if="activeType === 'general'" class="tab-panel" role="tabpanel">
          <div class="grid-2">
            <div class="field">
              <label for="ob-host">监听 Host</label>
              <input id="ob-host" v-model="general.ob_server_host" class="input" placeholder="127.0.0.1" />
              <p class="hint">本机使用建议保持回环地址。</p>
            </div>
            <div class="field">
              <label for="ob-port">监听端口</label>
              <input id="ob-port" v-model.number="general.ob_server_port" class="input" type="number" min="1" max="65535" />
            </div>
            <div class="field">
              <label for="ob-token">访问令牌</label>
              <PasswordInput id="ob-token" v-model="general.ob_server_token" placeholder="与客户端配置保持一致，留空则不校验" />
            </div>
            <div class="field">
              <label for="ob-format">消息格式</label>
              <select id="ob-format" v-model="general.ob_message_format" class="input">
                <option value="array">消息段数组（array）</option>
                <option value="string">CQ 码字符串（string）</option>
              </select>
              <p class="hint">事件里 message 字段的形态，raw_message 始终是 CQ 码字符串；发送方向两种都收。</p>
            </div>
            <div class="field">
              <label for="ob-heartbeat">心跳间隔（秒）</label>
              <input id="ob-heartbeat" v-model.number="general.ob_heartbeat_seconds" class="input" type="number" min="0" max="3600" />
              <p class="hint">0 = 不发心跳。连上先推 lifecycle(connect)，之后按间隔推 heartbeat。</p>
            </div>
          </div>
          <div class="endpoint-preview">
            <span class="mini-dot"></span>OneBot v11 正向 WebSocket<span class="mono">{{ endpoint }}</span>
          </div>
          <p class="hint">这些值只在没有启用中的入口时兜底；下面按类型配了入口，事件就按入口分发。</p>
        </div>

        <div v-else class="tab-panel" role="tabpanel">
          <div v-if="activeType === 'ws_server' && !draft.length" class="node-row general-row">
            <span class="state-dot on"></span>
            <div class="node-main">
              <strong>通用设置生成的入口</strong>
              <small class="mono">{{ general.ob_server_host || '127.0.0.1' }}:{{ general.ob_server_port || 7999 }}/</small>
            </div>
            <span class="node-state">按通用设置</span>
            <button class="btn btn-outline btn-sm" @click="materializeGeneral">转为独立入口</button>
          </div>
          <ul v-if="rows.length" class="node-list">
            <li v-for="row in rows" :key="row.index" class="node-row" :class="{ off: row.node.enabled === false }">
              <span class="state-dot" :class="row.tone" :title="row.error || row.stateLabel"></span>
              <div class="node-main">
                <strong>{{ row.node.name }}</strong>
                <small class="mono">{{ row.target }}</small>
              </div>
              <span class="node-state" :class="row.tone">{{ row.stateLabel }}</span>
              <span class="node-conn mono">{{ row.connections ? row.connections + ' 个连接' : '—' }}</span>
              <ToggleSwitch :model-value="row.node.enabled !== false" :label="`启用 ${row.node.name}`" @update:model-value="setEnabled(row.index, $event)" />
              <button type="button" class="icon-btn" :aria-label="`编辑 ${row.node.name}`" :title="`编辑 ${row.node.name}`" @click="openEdit(row.index)"><AppIcon name="Pencil" :size="15" /></button>
              <button type="button" class="icon-btn danger" :aria-label="`删除 ${row.node.name}`" :title="`删除 ${row.node.name}`" @click="pendingRemove = row.index"><AppIcon name="Trash2" :size="15" /></button>
            </li>
          </ul>
          <div v-else-if="!(activeType === 'ws_server' && !draft.length)" class="node-empty">
            <AppIcon :name="activeMeta.icon" :size="22" />
            <p>暂无{{ activeMeta.tab }}节点</p>
            <button class="btn btn-outline" @click="addNode(activeType)"><AppIcon name="Plus" :size="15" />创建第一个</button>
          </div>
          <p v-if="rows.length || (activeType === 'ws_server' && !draft.length)" class="hint">
            {{ activeMeta.hint }}同一条可以同时挂多个；改动在保存后才写进 config.json。
          </p>
        </div>
      </section>
    </div>

    <NodeDialog :open="editing !== null" :node="editingNode" @close="editing = null; editingNode = null" @save="applyEdit" />
    <ConfirmDialog
      :open="pendingRemove !== null"
      title="删除这个入口"
      :message="pendingRemove !== null ? `把「${draft[pendingRemove]?.name}」从入口列表里删掉，保存后桥接停止监听它。` : ''"
      confirm-text="删除"
      danger
      @cancel="pendingRemove = null"
      @confirm="confirmRemove"
    />
  </div>
</template>

<style scoped>
.nodes-page { display:flex; flex-direction:column; gap:16px; }
.nodes-layout { display:grid; grid-template-columns:262px minmax(0, 1fr); gap:16px; align-items:start; }
.nodes-side { display:flex; flex-direction:column; gap:16px; position:sticky; top:0; }
.account-card { padding:18px; box-shadow:none; }
.account-card .card-title { margin-bottom:14px; }
.account-row { display:flex; align-items:center; gap:11px; }
.avatar { display:grid; place-items:center; width:42px; height:42px; flex-shrink:0; border-radius:12px; background:var(--primary-soft); color:var(--primary-deep); font-size:16px; font-weight:600; }
.account-name { display:flex; flex-direction:column; gap:3px; min-width:0; flex:1; }
.account-name strong { font-size:13px; color:var(--text-strong); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.account-name small { font-size:10px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; }
.account-stats { display:flex; flex-direction:column; gap:9px; margin-top:16px; padding-top:14px; border-top:1px solid var(--border); }
.account-stats li { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:11px; color:var(--muted); }
.account-stats strong { font-size:12.5px; color:var(--text-strong); }
.attach-field { margin-top:16px; padding-top:14px; border-top:1px solid var(--border); }
.nodes-card { box-shadow:none; padding:20px; }
.nodes-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; flex-wrap:wrap; }
.nodes-head .card-title { margin-bottom:3px; }
.head-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.dirty-flag { font-size:10.5px; color:var(--warn); }
.save-btn { min-width:104px; }
.node-tabs { display:flex; gap:4px; margin:16px 0 14px; padding-bottom:2px; overflow-x:auto; border-bottom:1px solid var(--border); }
.node-tabs button { display:inline-flex; align-items:center; gap:6px; flex-shrink:0; padding:8px 12px; min-height:34px; border:0; border-bottom:2px solid transparent; background:transparent; color:var(--muted); font-size:12px; font-family:inherit; cursor:pointer; }
.node-tabs button:hover { color:var(--text-strong); }
.node-tabs button.active { color:var(--primary-deep); border-bottom-color:var(--primary); }
.tab-count { display:inline-grid; place-items:center; min-width:18px; height:18px; padding:0 4px; border-radius:9px; background:var(--inset); color:var(--muted); font-size:10px; }
.node-tabs button.active .tab-count { background:var(--primary-soft); color:var(--primary-deep); }
.tab-panel { display:flex; flex-direction:column; gap:14px; }
.tab-panel .grid-2 { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }
.node-list { display:flex; flex-direction:column; gap:8px; }
.node-row { display:grid; grid-template-columns:10px minmax(0, 1.6fr) 84px 84px 44px 34px 34px; gap:10px; align-items:center; padding:11px 12px; border:1px solid var(--border); border-radius:10px; background:var(--card); }
.node-row.off { opacity:.62; }
.node-main { display:flex; flex-direction:column; gap:3px; min-width:0; }
.node-main strong { font-size:12.5px; color:var(--text-strong); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.node-main small { font-size:10.5px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.node-state { font-size:10.5px; color:var(--muted); }
.node-state.ok { color:var(--primary-deep); }
.node-state.warn { color:var(--warn); }
.node-state.err { color:var(--danger); }
.node-conn { font-size:10.5px; color:var(--muted); text-align:right; }
.state-dot { width:8px; height:8px; border-radius:50%; background:var(--border); }
.state-dot.on { background:var(--primary); }
.state-dot.ok { background:var(--primary); }
.state-dot.warn { background:var(--warn); }
.state-dot.err { background:var(--danger); }
.icon-btn.danger:hover { color:var(--danger); }
.general-row { border-style:dashed; }
.general-row .node-main { grid-column:span 1; }
.node-empty { display:flex; flex-direction:column; align-items:center; gap:10px; padding:34px 20px; border:1px dashed var(--border); border-radius:10px; color:var(--muted); }
.node-empty p { font-size:12.5px; }
.endpoint-preview { display:flex; align-items:center; gap:8px; padding:13px; border-radius:8px; background:var(--inset); font-size:11px; color:var(--muted); }
.endpoint-preview .mini-dot { width:6px; height:6px; border-radius:50%; background:var(--primary); }
.endpoint-preview .mono { margin-left:auto; color:var(--text); }
.load-error { display:flex; flex-direction:column; align-items:center; gap:9px; padding:48px 24px; text-align:center; box-shadow:none; }
.load-error > .app-icon { color:var(--danger); }
.load-error h3 { font-size:14px; font-weight:600; }
.load-error p { max-width:420px; font-size:12px; color:var(--muted); line-height:1.8; }
.load-error .btn { margin-top:8px; }
@media(max-width:1000px) {
  .nodes-layout { grid-template-columns:1fr; }
  .nodes-side { position:static; }
}
@media(max-width:767px) {
  .nodes-page { gap:12px; }
  .nodes-card { padding:16px; }
  .node-row { grid-template-columns:10px minmax(0, 1fr) 44px 34px; row-gap:8px; }
  .node-conn { display:none; }
  .node-state { grid-column:2; }
  .head-actions { width:100%; }
  .head-actions .btn { flex:1; min-height:40px; }
}
</style>
