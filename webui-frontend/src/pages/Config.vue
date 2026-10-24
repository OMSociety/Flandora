<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { get, put } from '../api'
import { toast } from '../store'
import PasswordInput from '../components/PasswordInput.vue'
import AppIcon from '../components/AppIcon.vue'

const DEFAULTS = {
  bot_nicknames: [], bot_wxid: '', self_name: '', buffer_seconds: 5,
  web_port: 8766, ob_server_host: '127.0.0.1',
  ob_server_port: 7999, ob_server_token: '', ob_message_format: 'array',
  astrbot_attachments: '',
}
const NUMERIC = ['buffer_seconds', 'web_port', 'ob_server_port']
const categories = [
  { id: 'bot', label: '收件与身份', icon: 'MessageCircle', description: '昵称与显示名称' },
  { id: 'onebot', label: 'OneBot 连接', icon: 'Zap', description: '服务端与鉴权' },
  { id: 'bridge', label: '消息与桥接', icon: 'SlidersHorizontal', description: '缓冲与发送' },
  { id: 'groups', label: '群名映射', icon: 'MessagesSquare', description: '群 ID 与显示名称' },
]
const activeCategory = ref('bot')
const activeLabel = computed(() => categories.find((c) => c.id === activeCategory.value)?.label)
const cfg = reactive({})
const loaded = ref(false)
const loadError = ref('')
const saving = ref(false)
const saved = ref(false)
const jsonMode = ref(false)
const jsonText = ref('')
const jsonValid = ref(true)
const groupRows = reactive([])
const nickInput = ref('')

function applyConfig(obj) {
  Object.keys(cfg).forEach((k) => delete cfg[k])
  Object.assign(cfg, JSON.parse(JSON.stringify(obj || {})))
  Object.keys(DEFAULTS).forEach((key) => { if (!(key in cfg)) cfg[key] = key === 'bot_nicknames' ? [] : DEFAULTS[key] })
  if (!Array.isArray(cfg.bot_nicknames)) cfg.bot_nicknames = []
  if (!cfg.group_name_map || typeof cfg.group_name_map !== 'object' || Array.isArray(cfg.group_name_map)) cfg.group_name_map = {}
  rebuildGroupRows()
}
function rebuildGroupRows() { groupRows.splice(0, groupRows.length, ...Object.keys(cfg.group_name_map || {}).map((key) => ({ key, value: cfg.group_name_map[key] }))) }
function buildGroupMap() { return Object.fromEntries(groupRows.filter((row) => row.key).map((row) => [row.key, row.value])) }
function addRow() { groupRows.push({ key: '', value: '' }) }
function removeRow(i) { groupRows.splice(i, 1) }
function addNick() {
  const value = nickInput.value.trim()
  if (value && !cfg.bot_nicknames.includes(value)) cfg.bot_nicknames.push(value)
  nickInput.value = ''
}
function addNickFromKey(event) {
  if (['Enter', ',', '，'].includes(event.key)) { event.preventDefault(); addNick() }
}
function showJson() { cfg.group_name_map = buildGroupMap(); jsonText.value = JSON.stringify(cfg, null, 2); jsonValid.value = true; jsonMode.value = true }
function hideJson() {
  try { applyConfig(JSON.parse(jsonText.value)); jsonValid.value = true; jsonMode.value = false }
  catch { toast('JSON 格式错误，请修正后切换回表单', 'error') }
}
function onJsonInput() {
  try { applyConfig(JSON.parse(jsonText.value)); jsonValid.value = true }
  catch { jsonValid.value = false }
}
async function save() {
  if (saving.value) return
  let payload
  if (jsonMode.value) {
    try { payload = JSON.parse(jsonText.value) }
    catch { toast('JSON 格式错误，无法保存', 'error'); return }
  } else {
    cfg.group_name_map = buildGroupMap()
    payload = JSON.parse(JSON.stringify(cfg))
  }
  NUMERIC.forEach((key) => { if (key in payload) payload[key] = Number(payload[key]) || 0 })
  saving.value = true
  saved.value = false
  const res = await put('/api/config', payload)
  saving.value = false
  if (res.ok && res.data && res.data.ok !== false) {
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
    toast('配置已保存', 'success')
    if (Array.isArray(res.data.restart_required) && res.data.restart_required.length) toast('部分配置需重启桥接生效：' + res.data.restart_required.join('、'), 'info', 6500)
  } else toast('保存失败：' + (res.data?.error || (res.status ? `HTTP ${res.status}` : '请求失败')), 'error')
}
async function load() {
  loaded.value = false
  loadError.value = ''
  const res = await get('/api/config')
  if (res.ok && res.data && typeof res.data === 'object' && !Array.isArray(res.data)) {
    applyConfig(res.data)
    loaded.value = true
  } else {
    // 读不到就停在错误态：若渲染出只含 DEFAULTS 的空表单，一次保存就会覆盖真实配置。
    loadError.value = res.data?.error || (res.status ? `HTTP ${res.status}` : '无法连接桥接服务')
    toast('加载配置失败：' + loadError.value, 'error')
  }
}
onMounted(load)
</script>

<template>
  <div class="config-page">
    <div class="page-intro"><div><h2>让桥接按你的方式工作。</h2><p>管理机器人身份、OneBot 连接和消息处理选项。</p></div><button class="btn btn-outline" @click="jsonMode ? hideJson() : showJson()"><AppIcon :name="jsonMode ? 'SlidersHorizontal' : 'FileText'" :size="15" />{{ jsonMode ? '返回表单' : '编辑原始 JSON' }}</button></div>
    <div v-if="!loaded && !loadError" class="card empty"><AppIcon name="LoaderCircle" :size="25" />正在读取配置…</div>
    <div v-else-if="loadError" class="card load-error"><AppIcon name="CircleAlert" :size="26" /><h3>无法读取配置</h3><p>{{ loadError }}</p><p class="hint">为避免用空值覆盖 config.json，配置编辑已停用。请确认桥接服务正在运行后重试。</p><button class="btn btn-outline" @click="load"><AppIcon name="RefreshCw" :size="15" />重新读取</button></div>
    <div v-else-if="jsonMode" class="card json-panel"><div class="section-head"><div><h3 class="card-title">config.json</h3><p class="card-description">直接编辑完整配置，保存前请确认 JSON 语法有效。</p></div><span class="badge" :class="jsonValid ? 'badge-green' : 'badge-red'">{{ jsonValid ? '格式有效' : '格式错误' }}</span></div><textarea v-model="jsonText" class="textarea json-text" aria-label="原始 JSON 配置" @input="onJsonInput" spellcheck="false"></textarea></div>
    <div v-else class="config-layout">
      <nav class="config-nav card" aria-label="配置分类"><button v-for="category in categories" :key="category.id" :class="{ active: activeCategory === category.id }" :aria-current="activeCategory === category.id ? 'page' : undefined" @click="activeCategory = category.id"><AppIcon :name="category.icon" :size="17" /><span><strong>{{ category.label }}</strong><small>{{ category.description }}</small></span><AppIcon name="ChevronRight" :size="14" /></button></nav>
      <div class="config-content">
        <section v-show="activeCategory === 'bot'" class="card config-section"><div class="section-head"><div><h3 class="card-title">收件与身份</h3><p class="card-description">昵称用于判断群消息是否 @ 了机器人；自我称呼是推送给客户端时显示的发送者名。</p></div><AppIcon name="MessageCircle" :size="19" /></div><div class="form-grid"><div class="field span-full"><label for="bot-nicknames">机器人昵称</label><div class="nick-editor"><span v-for="(n, i) in cfg.bot_nicknames" :key="i" class="chip">{{ n }}<button type="button" class="chip-x" :aria-label="`删除昵称 ${n}`" @click="cfg.bot_nicknames.splice(i, 1)"><AppIcon name="X" :size="12" /></button></span><input id="bot-nicknames" v-model="nickInput" class="nick-input" placeholder="输入昵称，回车或逗号添加" @keydown="addNickFromKey" @blur="addNick" /></div><p class="hint">可添加多个昵称；群聊中 @ 其中任意昵称即可触发。</p></div><div class="field"><label for="bot-wxid">机器人 wxid</label><input id="bot-wxid" v-model="cfg.bot_wxid" class="input" placeholder="留空以自动识别" /></div><div class="field"><label for="self-name">自我称呼</label><input id="self-name" v-model="cfg.self_name" class="input" placeholder="推送给客户端时显示的发送者名" /></div></div></section>
        <section v-show="activeCategory === 'onebot'" class="card config-section"><div class="section-head"><div><h3 class="card-title">OneBot 服务端</h3><p class="card-description">任意 OneBot v11 客户端通过这些信息连接到桥接。</p></div><AppIcon name="Zap" :size="19" /></div><div class="form-grid"><div class="field"><label for="ob-host">监听 Host</label><input id="ob-host" v-model="cfg.ob_server_host" class="input" placeholder="127.0.0.1" /><p class="hint">本机使用建议保持回环地址。</p></div><div class="field"><label for="ob-port">监听端口</label><input id="ob-port" v-model.number="cfg.ob_server_port" class="input" type="number" min="1" max="65535" /></div><div class="field"><label for="ob-token">访问令牌</label><PasswordInput id="ob-token" v-model="cfg.ob_server_token" placeholder="与客户端配置保持一致，留空则不校验" /></div><div class="field"><label for="ob-format">消息格式</label><select id="ob-format" v-model="cfg.ob_message_format" class="input"><option value="array">消息段数组（array）</option><option value="string">CQ 码字符串（string）</option></select><p class="hint">事件里 message 字段的形态，raw_message 始终是 CQ 码字符串；发送方向两种都收。</p></div></div><div class="endpoint-preview"><span class="mini-dot"></span>OneBot v11 正向 WebSocket<span class="mono">ws://{{ cfg.ob_server_host }}:{{ cfg.ob_server_port }}</span></div></section>
        <section v-show="activeCategory === 'bridge'" class="card config-section"><div class="section-head"><div><h3 class="card-title">消息与桥接</h3><p class="card-description">控制消息缓冲与发送通道。</p></div><AppIcon name="SlidersHorizontal" :size="19" /></div><div class="form-grid"><div class="field"><label for="buffer-seconds">消息缓冲（秒）</label><input id="buffer-seconds" v-model.number="cfg.buffer_seconds" class="input" type="number" min="0" /><p class="hint">合并同一联系人在此时间内的连续消息。</p></div><div class="field"><label for="web-port">Web 面板端口</label><input id="web-port" v-model.number="cfg.web_port" class="input" type="number" min="1" max="65535" /></div><div class="field"><label for="send-method">发送方式</label><input id="send-method" value="UI 自动化（uia）" class="input" disabled /></div><div class="field"><label for="attachments">客户端附件目录</label><input id="attachments" v-model="cfg.astrbot_attachments" class="input" placeholder="D:\attachments" /></div></div></section>
        <section v-show="activeCategory === 'groups'" class="card config-section"><div class="section-head"><div><h3 class="card-title">群名映射</h3><p class="card-description">把微信群 ID 映射为易读的真实群名。</p></div><button class="btn btn-outline btn-sm" @click="addRow"><AppIcon name="Plus" :size="14" />添加映射</button></div><div v-if="groupRows.length" class="mapping-head"><span>群 ID @chatroom</span><span>微信中显示的群名</span><span></span></div><div v-for="(row, i) in groupRows" :key="i" class="mapping-row"><input v-model="row.key" class="input" :aria-label="`第 ${i + 1} 行群 ID`" placeholder="群ID@chatroom" /><input v-model="row.value" class="input" :aria-label="`第 ${i + 1} 行群名`" placeholder="微信中显示的群名" /><button class="icon-btn" :aria-label="`删除第 ${i + 1} 行映射`" @click="removeRow(i)"><AppIcon name="Trash2" :size="16" /></button></div><div v-if="!groupRows.length" class="empty"><AppIcon name="MessagesSquare" :size="24" />尚未添加群名映射</div><p class="hint mapping-hint">发送测试中的群聊应填写这里配置的真实群名。</p></section>
      </div>
    </div>
    <div v-if="loaded" class="save-bar"><div><strong>{{ jsonMode ? '完整配置' : activeLabel }}</strong><span>部分配置需要重启桥接后生效</span></div><button class="btn save-btn" :disabled="saving || (jsonMode && !jsonValid)" @click="save"><AppIcon :name="saving ? 'LoaderCircle' : saved ? 'Check' : 'CheckCheck'" :size="16" />{{ saving ? '正在保存…' : saved ? '已保存' : '保存配置' }}</button></div>
  </div>
</template>

<style scoped>
.config-page { display:flex; flex-direction:column; gap:18px; }
.config-layout { display:grid; grid-template-columns:220px minmax(0,1fr); gap:18px; align-items:start; }
.config-nav { padding:8px; box-shadow:none; position:sticky; top:0; }
.config-nav button { display:flex; align-items:center; gap:10px; width:100%; padding:12px 10px; border:0; border-radius:8px; background:transparent; color:var(--muted); text-align:left; cursor:pointer; }
.config-nav button:hover { background:var(--inset); }
.config-nav button.active { background:var(--primary-soft); color:var(--primary-deep); }
.config-nav button > span { display:flex; flex-direction:column; gap:2px; flex:1; }
.config-nav strong { font-size:12px; font-weight:500; color:var(--text); }
.config-nav button.active strong { color:var(--primary-deep); }
.config-nav small { font-size:9px; color:var(--muted); white-space:nowrap; }
.config-section { min-height:390px; box-shadow:none; padding:24px; }
.section-head > .app-icon { color:var(--faint); }
.section-head .card-description { margin-bottom:0; }
.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:22px 18px; margin-top:24px; }
.nick-editor { display:flex; gap:6px; flex-wrap:wrap; align-items:center; padding:7px; min-height:43px; border:1px solid var(--border-strong); border-radius:8px; background:var(--card); }
.nick-editor:focus-within { border-color:var(--primary); box-shadow:0 0 0 3px var(--primary-soft); }
.nick-input { border:0; outline:0; background:transparent; color:var(--text-strong); flex:1; min-width:170px; padding:5px; font-size:12px; }
.endpoint-preview { display:flex; align-items:center; gap:8px; margin-top:26px; padding:14px; border-radius:8px; background:var(--inset); font-size:11px; color:var(--muted); }
.endpoint-preview .mini-dot { width:6px; height:6px; border-radius:50%; background:var(--primary); }
.endpoint-preview .mono { margin-left:auto; color:var(--text); }
.mapping-head, .mapping-row { display:grid; grid-template-columns:1fr 1fr 36px; gap:10px; align-items:center; }
.mapping-head { margin:24px 0 8px; font-size:10px; color:var(--muted); }
.mapping-row { margin-bottom:10px; }
.mapping-hint { margin-top:18px; padding-top:16px; border-top:1px solid var(--border); }
.save-bar { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:15px 18px; border:1px solid var(--border); border-radius:10px; background:var(--card); box-shadow:var(--shadow); }
.save-bar > div { display:flex; flex-direction:column; gap:2px; }
.save-bar strong { font-size:12px; font-weight:500; }
.save-bar span { font-size:10px; color:var(--muted); }
.save-btn { min-width:125px; }
.json-panel { box-shadow:none; }
.load-error { display:flex; flex-direction:column; align-items:center; gap:9px; padding:48px 24px; text-align:center; box-shadow:none; }
.load-error > .app-icon { color:var(--danger); }
.load-error h3 { font-size:14px; font-weight:600; }
.load-error p { max-width:420px; font-size:12px; color:var(--muted); line-height:1.8; }
.load-error .btn { margin-top:8px; }
.json-text { min-height:55vh; font-family:var(--mono); font-size:12px; line-height:1.7; tab-size:2; }
@media(max-width:1000px) { .config-layout { grid-template-columns:185px minmax(0,1fr); } .config-nav small { display:none; } .config-section { padding:20px; } }
@media(max-width:767px) {
  .page-intro .btn { padding-inline:10px; font-size:11px; }
  .config-layout { display:flex; flex-direction:column; gap:12px; }
  .config-nav { display:flex; gap:5px; width:100%; overflow-x:auto; position:static; padding:5px; }
  .config-nav button { width:auto; flex-shrink:0; padding:8px 11px; }
  .config-nav button > .app-icon:last-child, .config-nav small { display:none; }
  .config-nav strong { font-size:11px; }
  .config-section { min-height:0; padding:17px; }
  .form-grid { grid-template-columns:1fr; gap:18px; }
  .endpoint-preview { align-items:flex-start; flex-wrap:wrap; }
  .endpoint-preview .mono { width:100%; margin-left:14px; overflow-wrap:anywhere; }
  .mapping-head { display:none; }
  .mapping-row { grid-template-columns:1fr 36px; gap:8px; margin-bottom:16px; }
  .mapping-row .input:first-child { grid-column:1/-1; }
  .save-bar { padding:13px; }
  .save-bar span { max-width:140px; }
}
</style>
