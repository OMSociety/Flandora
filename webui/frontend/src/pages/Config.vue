<script setup>
import { ref, reactive, onMounted } from 'vue'
import { get, put } from '../api'
import { toast } from '../store'
import PasswordInput from '../components/PasswordInput.vue'

// 表单各部分缺失时的默认值（保证 v-model 可写）
const DEFAULTS = {
  weflow_base_url: '', access_token: '', bot_nicknames: [], bot_wxid: '', self_name: '',
  send_method: 'uia', buffer_seconds: 5, context_messages: 0, web_port: 8766, group_reply_mode: 'mention',
  ob_server_host: '127.0.0.1', ob_server_port: 7998, ob_server_token: '', astrbot_ob_url: '',
  astrbot_attachments: '', image_caption_provider: 'ollama', image_caption_model: '',
  image_caption_api_key: '', image_caption_api_base: '', image_caption_prompt: '',
  ollama_base_url: '', ollama_timeout: 60,
}

const NUMERIC = ['buffer_seconds', 'context_messages', 'web_port', 'ob_server_port', 'ollama_timeout']

const cfg = reactive({})
const loaded = ref(false)
const saving = ref(false)
const jsonMode = ref(false)
const jsonText = ref('')
const jsonValid = ref(true)

const groupRows = reactive([])
const nickInput = ref('')

const modeOptions = [
  { value: 'mention', label: '仅@回复' },
  { value: 'all', label: '全部回复' },
  { value: 'batch', label: '批处理' },
]
const providerOptions = [
  { value: 'ollama', label: 'Ollama 本地' },
  { value: 'openai', label: 'OpenAI 兼容' },
  { value: 'none', label: '关闭' },
]

function applyConfig(obj) {
  Object.keys(cfg).forEach((k) => delete cfg[k])
  Object.assign(cfg, JSON.parse(JSON.stringify(obj || {})))
  for (const k of Object.keys(DEFAULTS)) {
    if (!(k in cfg)) cfg[k] = k === 'bot_nicknames' ? [] : DEFAULTS[k]
  }
  if (!Array.isArray(cfg.bot_nicknames)) cfg.bot_nicknames = []
  if (!cfg.group_name_map || typeof cfg.group_name_map !== 'object' || Array.isArray(cfg.group_name_map)) {
    cfg.group_name_map = {}
  }
  rebuildGroupRows()
}

function rebuildGroupRows() {
  groupRows.splice(0, groupRows.length)
  const gm = cfg.group_name_map || {}
  Object.keys(gm).forEach((k) => groupRows.push({ key: k, value: gm[k] }))
}

function buildGroupMap() {
  const gm = {}
  groupRows.forEach((r) => {
    if (r.key) gm[r.key] = r.value
  })
  return gm
}

function addRow() { groupRows.push({ key: '', value: '' }) }
function removeRow(i) { groupRows.splice(i, 1) }

// ---- bot nicknames (tag input) ----
function addNick() {
  const v = nickInput.value.trim()
  if (v && !cfg.bot_nicknames.includes(v)) cfg.bot_nicknames.push(v)
  nickInput.value = ''
}
function addNickFromKey(e) {
  if (e.key === 'Enter' || e.key === ',' || e.key === '，') {
    e.preventDefault()
    addNick()
  }
}
function removeNick(i) { cfg.bot_nicknames.splice(i, 1) }

// ---- JSON mode ----
function showJson() {
  cfg.group_name_map = buildGroupMap()
  jsonText.value = JSON.stringify(cfg, null, 2)
  jsonValid.value = true
  jsonMode.value = true
}
function hideJson() {
  try {
    applyConfig(JSON.parse(jsonText.value))
    jsonValid.value = true
    jsonMode.value = false
  } catch (e) {
    toast('JSON 格式错误，无法切换到表单模式', 'error')
  }
}
function onJsonInput() {
  try {
    const parsed = JSON.parse(jsonText.value)
    applyConfig(parsed)
    jsonValid.value = true
  } catch (e) {
    jsonValid.value = false
  }
}

async function save() {
  if (saving.value) return
  let payload
  if (jsonMode.value) {
    if (!jsonValid.value) { toast('JSON 格式错误，无法保存', 'error'); return }
    try {
      payload = JSON.parse(jsonText.value)
    } catch (e) {
      toast('JSON 格式错误，无法保存', 'error')
      return
    }
  } else {
    cfg.group_name_map = buildGroupMap()
    payload = JSON.parse(JSON.stringify(cfg))
  }
  NUMERIC.forEach((k) => {
    if (k in payload) payload[k] = Number(payload[k]) || 0
  })

  saving.value = true
  const res = await put('/api/config', payload)
  saving.value = false

  if (res.ok && res.data && res.data.ok !== false) {
    toast('配置已保存', 'success')
    const rr = res.data.restart_required
    if (Array.isArray(rr) && rr.length) {
      toast('部分配置需重启桥接生效：' + rr.join('、'), 'info', 6500)
    }
  } else {
    const errMsg = (res.data && res.data.error) || (res.status ? `HTTP ${res.status}` : '请求失败')
    toast('保存失败：' + errMsg, 'error')
  }
}

async function load() {
  const res = await get('/api/config')
  loaded.value = true
  if (res.ok && res.data) {
    applyConfig(res.data)
  } else {
    const errMsg = (res.data && res.data.error) || '加载失败'
    toast('加载配置失败：' + errMsg, 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="config-page">
    <div class="card config-head">
      <div class="head-left">
        <span class="card-title" style="margin-bottom: 0; border: none; padding: 0">配置编辑</span>
        <span class="head-file">config.json</span>
      </div>
      <label class="json-toggle">
        <input type="checkbox" :checked="jsonMode" @change="jsonMode ? hideJson() : showJson()" />
        <span>原始 JSON</span>
      </label>
    </div>

    <div v-if="!loaded" class="empty">加载配置中...</div>

    <!-- JSON 模式 -->
    <template v-else-if="jsonMode">
      <div class="card">
        <div class="json-status" :class="jsonValid ? 'ok' : 'err'">
          {{ jsonValid ? '✅ JSON 格式有效，可保存' : '❌ JSON 格式错误，无法保存' }}
        </div>
        <textarea v-model="jsonText" class="textarea json-text" @input="onJsonInput" spellcheck="false"></textarea>
      </div>
    </template>

    <!-- 表单模式 -->
    <template v-else>
      <div class="card">
        <div class="card-title">WeFlow 连接</div>
        <div class="grid-2">
          <div class="field">
            <label>WeFlow 地址</label>
            <input v-model="cfg.weflow_base_url" class="input" placeholder="http://127.0.0.1:5031" />
          </div>
          <div class="field">
            <label>Access Token</label>
            <password-input v-model="cfg.access_token" placeholder="输入 Token" />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">机器人</div>
        <div class="grid-2">
          <div class="field" style="grid-column: 1 / -1">
            <label>机器人昵称（多个，回车或逗号添加）</label>
            <div class="nick-editor">
              <span v-for="(n, i) in cfg.bot_nicknames" :key="i" class="chip">
                {{ n }} <span class="chip-x" @click="removeNick(i)">×</span>
              </span>
              <input
                v-model="nickInput"
                class="input nick-input"
                placeholder="输入昵称后回车"
                @keydown="addNickFromKey"
                @blur="addNick"
              />
            </div>
          </div>
          <div class="field">
            <label>机器人 wxid</label>
            <input v-model="cfg.bot_wxid" class="input" placeholder="wxid_xxx" />
          </div>
          <div class="field">
            <label>自我称呼（self_name）</label>
            <input v-model="cfg.self_name" class="input" placeholder="小E" />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">OneBot 服务端（MaiBot 连接）</div>
        <div class="grid-2">
          <div class="field">
            <label>监听 Host</label>
            <input v-model="cfg.ob_server_host" class="input" placeholder="127.0.0.1" />
          </div>
          <div class="field">
            <label>监听端口</label>
            <input v-model.number="cfg.ob_server_port" class="input" type="number" />
          </div>
          <div class="field">
            <label>ob_server_token</label>
            <password-input v-model="cfg.ob_server_token" placeholder="与 adapter 一致" />
          </div>
          <div class="field">
            <label>AstrBot OB 地址</label>
            <input v-model="cfg.astrbot_ob_url" class="input" placeholder="ws://127.0.0.1:7999" />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">桥接</div>
        <div class="grid-3">
          <div class="field">
            <label>消息缓冲（秒）</label>
            <input v-model.number="cfg.buffer_seconds" class="input" type="number" />
          </div>
          <div class="field">
            <label>上下文附带条数</label>
            <input v-model.number="cfg.context_messages" class="input" type="number" placeholder="5" />
            <span class="cfg-hint">mention 模式下，把被 @ 消息之前的最近 N 条群聊记录一并推给麦麦当上下文；0 = 关闭</span>
          </div>
          <div class="field">
            <label>群聊回复模式</label>
            <select v-model="cfg.group_reply_mode" class="select">
              <option v-for="o in modeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div class="field">
            <label>Web 面板端口</label>
            <input v-model.number="cfg.web_port" class="input" type="number" />
          </div>
          <div class="field">
            <label>发送方式（固定）</label>
            <input :value="'uia'" class="input" disabled />
          </div>
          <div class="field">
            <label>AstrBot 附件目录</label>
            <input v-model="cfg.astrbot_attachments" class="input" placeholder="E:\\MaiBot\\attachments" />
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">图片描述</div>
        <div class="grid-3">
          <div class="field">
            <label>描述服务</label>
            <select v-model="cfg.image_caption_provider" class="select">
              <option v-for="o in providerOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div class="field">
            <label>模型名</label>
            <input v-model="cfg.image_caption_model" class="input" placeholder="deepseek-v4-flash-vision-exp" />
          </div>
          <div class="field">
            <label>API Key</label>
            <password-input v-model="cfg.image_caption_api_key" placeholder="sk-xxx (OpenAI 模式时)" />
          </div>
          <div class="field">
            <label>API 地址</label>
            <input v-model="cfg.image_caption_api_base" class="input" placeholder="https://api.deepseek.com/v1" />
          </div>
          <div class="field">
            <label>Ollama 地址</label>
            <input v-model="cfg.ollama_base_url" class="input" placeholder="http://127.0.0.1:61000" />
          </div>
          <div class="field">
            <label>Ollama 超时（秒）</label>
            <input v-model.number="cfg.ollama_timeout" class="input" type="number" />
          </div>
          <div class="field" style="grid-column: 1 / -1">
            <label>描述提示词</label>
            <textarea v-model="cfg.image_caption_prompt" class="textarea" rows="2" placeholder="请用中文简短描述这张图片的内容"></textarea>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">群名映射（群ID@chatroom → 群显示名）</div>
        <div class="gm-editor">
          <div v-for="(r, i) in groupRows" :key="i" class="gm-row">
            <input v-model="r.key" class="input gm-key" placeholder="群ID@chatroom" />
            <span class="gm-arrow">→</span>
            <input v-model="r.value" class="input gm-val" placeholder="微信中显示的群名" />
            <button class="btn btn-ghost btn-sm gm-del" @click="removeRow(i)">删除</button>
          </div>
          <button class="btn btn-outline btn-sm" @click="addRow()">+ 添加一行</button>
          <div class="hint">群聊测试发送请填写此处配置的真实群名。</div>
        </div>
      </div>
    </template>

    <!-- 保存栏 -->
    <div v-if="loaded" class="save-bar">
      <div class="spacer"></div>
      <button class="btn btn-pink" :disabled="saving" @click="save()">
        {{ saving ? '保存中...' : '💾 保存配置' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.config-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.config-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
}
.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.head-file {
  font-size: 12px;
  color: var(--muted);
  background: var(--pink-tint-2);
  padding: 2px 10px;
  border-radius: 20px;
}
.json-toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  font-weight: 600;
}
.json-toggle input {
  accent-color: var(--pink);
  width: 16px;
  height: 16px;
}
.json-status {
  font-size: 12px;
  margin-bottom: 8px;
  padding: 6px 12px;
  border-radius: 8px;
}
.json-status.ok {
  background: #e8f5e9;
  color: #2e7d32;
}
.json-status.err {
  background: var(--red-soft);
  color: #c62828;
}
.json-text {
  min-height: 420px;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.5;
}
.nick-editor {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  padding: 8px;
  border: 1.5px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
}
.nick-input {
  border: none;
  flex: 1;
  min-width: 140px;
  padding: 4px 6px;
}
.nick-input:focus {
  box-shadow: none;
}
.gm-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.gm-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gm-key {
  flex: 1;
}
.gm-arrow {
  color: var(--muted);
}
.gm-val {
  flex: 1;
}
.gm-del {
  flex-shrink: 0;
}
.hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--faint);
}
.save-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 4px;
}
.cfg-hint {
  font-size: 11px;
  color: var(--faint);
  line-height: 1.5;
}
</style>
