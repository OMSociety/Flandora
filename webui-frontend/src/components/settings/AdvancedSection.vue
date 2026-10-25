<script setup>
import { ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import SectionFoot from './SectionFoot.vue'
import { useSettingsSave } from '../../composables/useSettingsSave'

const props = defineProps({
  cfg: { type: Object, required: true },
  active: { type: Boolean, default: false },
  epoch: { type: Number, default: 0 },
})
const emit = defineEmits(['reload'])

const jsonText = ref('')
const jsonValid = ref(true)
const { saving, saved, save } = useSettingsSave()

// 与旧的「编辑原始 JSON」一致：每次打开面板或配置重载后，都从当前配置重新生成文本。
function resync() {
  jsonText.value = JSON.stringify(props.cfg, null, 2)
  jsonValid.value = true
}
watch(() => props.active, (on) => { if (on) resync() }, { immediate: true })
watch(() => props.epoch, () => { if (props.active) resync() })

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
        <p class="card-description">直接编辑完整 config.json；保存前请确认 JSON 语法有效，语法错误时无法应用。</p>
      </div>
      <span class="badge" :class="jsonValid ? 'badge-green' : 'badge-red'">{{ jsonValid ? '格式有效' : '格式错误' }}</span>
    </div>
    <textarea v-model="jsonText" class="textarea json-text" aria-label="原始 JSON 配置" @input="onJsonInput" spellcheck="false"></textarea>
    <SectionFoot :saving="saving" :saved="saved" :disabled="!jsonValid" label="应用并保存" hint="这里的改动以整份配置为单位保存" @save="apply" />
  </section>
</template>

<style scoped>
.settings-section { min-height:0; box-shadow:none; padding:20px; }
.section-head .card-description { margin-bottom:0; }
.json-text { min-height:50vh; font-family:var(--mono); font-size:12px; line-height:1.7; tab-size:2; margin-top:14px; }
@media(max-width:767px) { .settings-section { padding:17px; } .json-text { min-height:42vh; } }
</style>
