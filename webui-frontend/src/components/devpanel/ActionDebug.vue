<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, post } from '../../api'
import { toast } from '../../store'
import AppIcon from '../AppIcon.vue'
import { segmentsToCq } from '../../utils/cq'

const catalog = ref({ categories: [], actions: [] })
const category = ref('')
const actionName = ref('')
const values = ref({})
const running = ref(false)
const result = ref(null)

const events = ref([])
const eventsLoading = ref(false)
const openedEvent = ref(null)

const builder = ref([{ type: 'text', value: '你好' }])

const actions = computed(() =>
  catalog.value.actions.filter((a) => !category.value || a.category === category.value),
)
const action = computed(() => catalog.value.actions.find((a) => a.name === actionName.value) || null)
const builderSegments = computed(() =>
  builder.value
    .filter((row) => String(row.value ?? '').trim() !== '')
    .map((row) => {
      if (row.type === 'text') return { type: 'text', data: { text: row.value } }
      if (row.type === 'at') return { type: 'at', data: { qq: Number(row.value) || 0 } }
      return { type: row.type, data: { file: row.value } }
    }),
)
const builderCq = computed(() => segmentsToCq(builderSegments.value))

async function loadCatalog() {
  const res = await get('/api/debug/actions')
  if (res.ok && res.data) catalog.value = res.data
  else toast('动作目录加载失败', 'error')
}

function onActionChange() {
  values.value = {}
  result.value = null
}

async function invoke() {
  if (!actionName.value) {
    toast('先选一个动作', 'error')
    return
  }
  const params = {}
  for (const p of action.value?.params || []) {
    const v = values.value[p.name]
    if (p.type === 'message') {
      const raw = String(v ?? '').trim()
      if (!raw) continue
      try {
        params[p.name] = JSON.parse(raw)
      } catch {
        toast(`${p.name} 不是合法 JSON`, 'error')
        return
      }
      continue
    }
    if (v === '' || v === undefined || v === null) continue
    if (p.type === 'int') params[p.name] = Number(v)
    else if (p.type === 'bool') params[p.name] = !!v
    else params[p.name] = v
  }
  running.value = true
  const res = await post('/api/debug/invoke', { action: actionName.value, params })
  running.value = false
  if (!res.data) {
    toast(res.status ? `调用失败（HTTP ${res.status}）` : '调用失败：没有响应体', 'error')
    return
  }
  result.value = res.data
  toast(res.data.ok ? `已执行，用时 ${res.data.elapsed_ms} ms` : '动作返回失败，原因见响应', res.data.ok ? 'success' : 'error')
}

function addRow(type) {
  builder.value.push({ type, value: '' })
}

function removeRow(index) {
  builder.value.splice(index, 1)
}

function fillSendMsg() {
  if (!builderSegments.value.length) {
    toast('先往构造器里加至少一段', 'error')
    return
  }
  category.value = ''
  actionName.value = 'send_msg'
  values.value = {
    message_type: 'group',
    message: JSON.stringify(builderSegments.value, null, 2),
  }
  result.value = null
  toast('已填进 send_msg，补齐 group_id 或 user_id 再执行', 'info')
}

async function copyCq() {
  try {
    await navigator.clipboard.writeText(builderCq.value)
    toast('CQ 码已复制', 'success')
  } catch {
    toast('复制失败，请手动选中文本', 'error')
  }
}

async function loadEvents() {
  eventsLoading.value = true
  const res = await get('/api/debug/events?limit=30', true)
  eventsLoading.value = false
  if (res.ok && res.data?.events) events.value = res.data.events
}

function toggleEvent(event) {
  openedEvent.value = openedEvent.value === event ? null : event
}

function eventTitle(event) {
  const who = event.message_type === 'group' ? `群 ${event.group_id}` : `用户 ${event.user_id}`
  return `${event.nickname || ''} ${who}`.trim()
}

onMounted(() => {
  loadCatalog()
  loadEvents()
})
</script>

<template>
  <div class="debug-panel">
    <div class="debug-layout">
      <section class="card invoke-card">
        <div class="section-head">
          <h3 class="card-title">动作调用</h3>
          <AppIcon name="Wrench" :size="18" />
        </div>

        <div class="form-grid">
          <div class="field">
            <label for="debug-category">分类</label>
            <select id="debug-category" v-model="category" class="input">
              <option value="">全部（{{ catalog.actions.length }} 个动作）</option>
              <option v-for="c in catalog.categories" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="field">
            <label for="debug-action">动作</label>
            <select id="debug-action" v-model="actionName" class="input" @change="onActionChange">
              <option value="">请选择</option>
              <option v-for="a in actions" :key="a.name" :value="a.name">
                {{ a.implemented ? a.name : a.name + '（未实现）' }}
              </option>
            </select>
          </div>
        </div>

        <div v-if="action" class="action-brief">
          <p class="action-summary">{{ action.summary }}</p>
          <p v-if="!action.implemented" class="action-wip">
            <AppIcon name="CircleAlert" :size="14" /><span>{{ action.wip || '暂未实现' }}</span>
          </p>
          <p v-if="action.aliases?.length" class="hint">别名：{{ action.aliases.join('、') }}</p>
        </div>

        <div v-if="action" class="params">
          <div v-if="!action.params.length" class="hint">这个动作没有参数。</div>
          <div v-for="p in action.params" :key="p.name" class="field">
            <label :for="`param-${p.name}`">
              {{ p.name }}<span class="param-type">{{ p.type }}</span>
              <span v-if="p.required" class="param-required">必填</span>
            </label>
            <textarea
              v-if="p.type === 'message'"
              :id="`param-${p.name}`"
              v-model="values[p.name]"
              class="textarea mono"
              rows="5"
              :placeholder="`消息段数组或 CQ 字符串，例如 [{&quot;type&quot;:&quot;text&quot;,&quot;data&quot;:{&quot;text&quot;:&quot;你好&quot;}}]`"
            ></textarea>
            <label v-else-if="p.type === 'bool'" class="check-label">
              <input v-model="values[p.name]" type="checkbox" />{{ p.desc || p.name }}
            </label>
            <input
              v-else
              :id="`param-${p.name}`"
              v-model="values[p.name]"
              class="input"
              :type="p.type === 'int' ? 'number' : 'text'"
              :placeholder="p.desc || ''"
            />
            <p v-if="p.type !== 'bool' && p.desc" class="hint">{{ p.desc }}</p>
          </div>
        </div>

        <div class="invoke-footer">
          <span class="hint">参数留空即不传；数量型按数字发送。</span>
          <button class="btn" :disabled="!actionName || running" @click="invoke">
            <AppIcon :name="running ? 'LoaderCircle' : 'Play'" :size="15" />
            {{ running ? '执行中…' : '执行' }}
          </button>
        </div>

        <div v-if="result" class="result">
          <div class="result-head">
            <span class="badge" :class="result.ok ? 'badge-green' : 'badge-red'">
              {{ result.ok ? '成功' : '失败' }} · {{ result.elapsed_ms }} ms
            </span>
          </div>
          <pre class="code-block">{{ JSON.stringify(result.response, null, 2) }}</pre>
        </div>
      </section>

      <aside class="debug-aside">
        <section class="card builder-card">
          <div class="section-head">
            <h3 class="card-title">消息构造器</h3>
            <AppIcon name="Blocks" :size="18" />
          </div>
          <div v-for="(row, i) in builder" :key="i" class="builder-row">
            <span class="builder-type">{{ row.type }}</span>
            <input
              v-model="row.value"
              class="input"
              :placeholder="row.type === 'at' ? '群成员的 user_id' : row.type === 'text' ? '文字内容' : '文件路径或 base64://'"
              :aria-label="`第 ${i + 1} 段内容`"
            />
            <button class="row-del" type="button" aria-label="删除这一段" @click="removeRow(i)">
              <AppIcon name="Trash2" :size="15" />
            </button>
          </div>
          <div class="builder-actions">
            <button v-for="t in ['text', 'at', 'image', 'face']" :key="t" class="btn btn-outline btn-sm" type="button" @click="addRow(t)">
              <AppIcon name="Plus" :size="14" />{{ t }}
            </button>
          </div>
          <div class="field">
            <label for="builder-cq">CQ 码预览</label>
            <textarea id="builder-cq" class="textarea mono" rows="3" readonly :value="builderCq"></textarea>
          </div>
          <div class="builder-actions">
            <button class="btn btn-outline btn-sm" type="button" @click="fillSendMsg">
              <AppIcon name="ArrowUpRight" :size="14" />填进 send_msg
            </button>
            <button class="btn btn-outline btn-sm" type="button" @click="copyCq">
              <AppIcon name="Copy" :size="14" />复制 CQ
            </button>
          </div>
        </section>

        <section class="card events-card">
          <div class="section-head">
            <h3 class="card-title">最近推送的事件</h3>
            <button class="btn btn-outline btn-sm" type="button" :disabled="eventsLoading" @click="loadEvents">
              <AppIcon name="RefreshCw" :size="14" />刷新
            </button>
          </div>
          <div v-if="!events.length" class="hint">还没有推送过消息。等微信来一条，或让机器人发一条。</div>
          <ul v-else class="event-list">
            <li v-for="event in events" :key="event.message_id">
              <button class="event-row" type="button" :aria-expanded="openedEvent === event" @click="toggleEvent(event)">
                <span class="event-id mono">#{{ event.message_id }}</span>
                <span class="event-who">{{ eventTitle(event) }}</span>
                <span class="event-text">{{ event.raw_message }}</span>
              </button>
              <pre v-if="openedEvent === event" class="code-block">{{ JSON.stringify(event, null, 2) }}</pre>
            </li>
          </ul>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.debug-panel { display:flex; flex-direction:column; gap:16px; }
.debug-layout { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr); gap:16px; align-items:start; max-width:1180px; }
.invoke-card, .builder-card, .events-card { padding:20px; }
.section-head > .app-icon { color:var(--primary-deep); }
.action-brief { margin-top:14px; padding:12px 14px; border-radius:8px; background:var(--inset); }
.action-summary { font-size:12.5px; color:var(--text-strong); }
.action-wip { display:flex; gap:7px; align-items:flex-start; margin-top:8px; font-size:12px; color:var(--warn, #b45309); }
.params { margin-top:16px; display:flex; flex-direction:column; gap:14px; }
.param-type { margin-left:7px; padding:1px 6px; border-radius:4px; background:var(--inset); font-size:10px; color:var(--muted); }
.param-required { margin-left:6px; font-size:10px; color:var(--danger, #b91c1c); }
.check-label { display:flex; align-items:center; gap:8px; font-size:12.5px; }
.invoke-footer { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:18px; padding-top:16px; border-top:1px solid var(--border); }
.result { margin-top:18px; }
.result-head { margin-bottom:8px; }
.code-block { margin:0; padding:12px 14px; max-height:320px; overflow:auto; border-radius:8px; background:var(--inset); font-family:var(--mono, monospace); font-size:11.5px; line-height:1.6; white-space:pre-wrap; word-break:break-all; }
.debug-aside { display:flex; flex-direction:column; gap:20px; }
.builder-row { display:grid; grid-template-columns:58px minmax(0,1fr) 30px; gap:8px; align-items:center; margin-bottom:8px; }
.builder-type { font-family:var(--mono, monospace); font-size:11px; color:var(--muted); }
.row-del { display:grid; place-items:center; width:30px; height:30px; border:0; border-radius:6px; background:transparent; color:var(--muted); cursor:pointer; }
.row-del:hover { background:var(--inset); color:var(--text-strong); }
.builder-actions { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 14px; }
.event-list { list-style:none; padding:0; margin:10px 0 0; display:flex; flex-direction:column; gap:6px; }
.event-row { display:grid; grid-template-columns:auto minmax(0,1fr); gap:4px 10px; width:100%; padding:9px 11px; border:1px solid var(--border); border-radius:8px; background:transparent; text-align:left; cursor:pointer; }
.event-row:hover { border-color:var(--border-strong); background:var(--inset); }
.event-id { font-size:11px; color:var(--muted); }
.event-who { font-size:12px; color:var(--text-strong); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.event-text { grid-column:1 / -1; font-size:11.5px; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
@media (max-width: 940px) { .debug-layout { grid-template-columns:1fr; } }
</style>
