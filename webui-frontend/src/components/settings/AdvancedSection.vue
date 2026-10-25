<script setup>
import { ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import SectionFoot from './SectionFoot.vue'
import { useSettingsSave } from '../../composables/useSettingsSave'
import { get } from '../../api'

const props = defineProps({
  cfg: { type: Object, required: true },
  active: { type: Boolean, default: false },
  epoch: { type: Number, default: 0 },
})
const emit = defineEmits(['reload'])

const jsonText = ref('')
const jsonValid = ref(true)
const { saving, saved, save } = useSettingsSave()

// 发送按键是这一册里唯一常用到需要点选的配置，给一个下拉；其余键改 JSON。
// 选项文案在前端，取值与校验都在后端 fields.py。
const SEND_KEY_OPTIONS = [
  { value: 'auto', label: '自动（先按回车，发不出去再点「发送」按钮）' },
  { value: 'enter', label: '按回车' },
  { value: 'button', label: '点「发送」按钮' },
]
const sendKey = ref('auto')

// 与旧的「编辑原始 JSON」一致：每次打开面板或配置重载后，都从当前配置重新生成文本。
function resync() {
  jsonText.value = JSON.stringify(props.cfg, null, 2)
  jsonValid.value = true
  sendKey.value = SEND_KEY_OPTIONS.some((o) => o.value === props.cfg.send_key)
    ? props.cfg.send_key
    : 'auto'
}
watch(() => props.epoch, () => { if (props.active) resync() })

async function changeSendKey() {
  const ok = await save({ send_key: sendKey.value })
  if (ok) await emit('reload')
  else resync()
}

// 右栏说明来自后端的字段表（fields.FIELDS）：文案只有一份来源，前端不抄。
// 说明不随配置值变化，切到这个分区时取一次即可；失败则下次进入重试。
const schema = ref([])
const schemaError = ref('')
let schemaLoaded = false
async function loadSchema() {
  schemaError.value = ''
  const res = await get('/api/config/schema', true)
  if (res.ok && res.data && Array.isArray(res.data.fields)) {
    schema.value = res.data.fields
    schemaLoaded = true
  } else {
    schemaError.value = res.data?.error || `HTTP ${res.status}`
  }
}

watch(() => props.active, (on) => {
  if (!on) return
  resync()
  if (!schemaLoaded) loadSchema()
}, { immediate: true })

function formatDefault(value) {
  return value === undefined ? '' : JSON.stringify(value)
}

function onJsonInput() {
  try { JSON.parse(jsonText.value); jsonValid.value = true }
  catch { jsonValid.value = false }
}

async function apply() {
  let parsed
  try { parsed = JSON.parse(jsonText.value) }
  catch { return }
  const ok = await save(parsed)
  if (ok) await emit('reload')
}
</script>

<template>
  <section class="card settings-section" aria-labelledby="advanced-heading">
    <div class="section-head">
      <div>
        <h3 id="advanced-heading" class="card-title">高级</h3>
        <p class="card-description">这里只列配置项；左边改值，右边是每个键的说明与默认值。</p>
      </div>
      <span class="badge" :class="jsonValid ? 'badge-green' : 'badge-red'">{{ jsonValid ? '格式有效' : '格式错误' }}</span>
    </div>
    <div class="field send-key-field">
      <label for="advanced-send-key">发送按键</label>
      <select id="advanced-send-key" v-model="sendKey" class="select" :disabled="saving" @change="changeSendKey">
        <option v-for="option in SEND_KEY_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <p class="field-hint">微信「设置 → 快捷键」里换过发送键时，这里的「点发送按钮」与那条设置无关；选自动则第一次没发出去之后会自己改用按钮。</p>
    </div>
    <div class="advanced-grid">
      <textarea v-model="jsonText" class="textarea json-text" aria-label="原始 JSON 配置" @input="onJsonInput" spellcheck="false"></textarea>
      <aside class="schema-panel" aria-label="配置项说明">
        <p v-if="schemaError" class="schema-error">读取配置项说明失败：{{ schemaError }}</p>
        <p v-else-if="!schema.length" class="schema-empty">正在读取配置项说明…</p>
        <ul v-else class="schema-list">
          <li v-for="item in schema" :key="item.key" class="schema-item">
            <div class="schema-head">
              <code class="schema-key">{{ item.key }}</code>
              <span v-if="item.restart" class="schema-restart">改后需重启</span>
            </div>
            <p class="schema-desc">{{ item.desc }}</p>
            <p class="schema-default">默认值 <code>{{ formatDefault(item.default) }}</code></p>
          </li>
        </ul>
      </aside>
    </div>
    <SectionFoot :saving="saving" :saved="saved" :disabled="!jsonValid" label="应用并保存" hint="这里的改动以整份配置为单位保存" @save="apply" />
  </section>
</template>

<style scoped>
.settings-section { min-height:0; box-shadow:none; padding:20px; }
.section-head .card-description { margin-bottom:0; }
.send-key-field { margin-top:14px; max-width:520px; }
.send-key-field .field-hint { font-size:11px; line-height:1.75; color:var(--muted); }
.advanced-grid { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:14px; align-items:start; margin-top:14px; }
.json-text { min-height:50vh; font-family:var(--mono); font-size:12px; line-height:1.7; tab-size:2; }
.schema-panel { position:sticky; top:12px; align-self:start; max-height:calc(100vh - 170px); overflow-y:auto; padding:12px 14px; border:1px solid var(--border); border-radius:10px; background:var(--inset); }
.schema-list { list-style:none; display:flex; flex-direction:column; gap:12px; }
.schema-item { padding-top:12px; border-top:1px dashed var(--border); }
.schema-item:first-child { padding-top:0; border-top:0; }
.schema-head { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.schema-key { font-family:var(--mono); font-size:11px; color:var(--text); }
.schema-restart { padding:1px 6px; border:1px solid var(--border); border-radius:999px; font-size:10px; color:var(--warning); }
.schema-desc { margin-top:5px; font-size:11px; line-height:1.75; color:var(--muted); }
.schema-default { margin-top:5px; font-size:10px; color:var(--faint); }
.schema-default code { font-family:var(--mono); font-size:10px; color:var(--muted); }
.schema-error { font-size:11px; line-height:1.7; color:var(--danger); }
.schema-empty { font-size:11px; color:var(--muted); }
@media(max-width:1199px) {
  .advanced-grid { grid-template-columns:1fr; }
  .schema-panel { position:static; max-height:none; }
}
@media(max-width:767px) { .settings-section { padding:17px; } .json-text { min-height:42vh; } }
</style>
