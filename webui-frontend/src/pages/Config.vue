<script setup>
import { ref, reactive, onMounted } from 'vue'
import { get } from '../api'
import { toast } from '../store'
import AppIcon from '../components/AppIcon.vue'
import SettingsNav from '../components/settings/SettingsNav.vue'
import AccessSection from '../components/settings/AccessSection.vue'
import StorageSection from '../components/settings/StorageSection.vue'
import AdvancedSection from '../components/settings/AdvancedSection.vue'
import AboutSection from '../components/settings/AboutSection.vue'

const SECTIONS = [
  { id: 'access', label: '安全与服务', icon: 'ShieldCheck', description: '面板访问、监听与备份' },
  { id: 'storage', label: '存储', icon: 'Database', description: '占用、日志与消息存档' },
  { id: 'advanced', label: '高级', icon: 'Wrench', description: '逐键说明与 JSON 编辑' },
  { id: 'about', label: '关于', icon: 'Info', description: '版本、更新与仓库' },
]
const activeSection = ref('access')

const DEFAULTS = {
  web_port: 4950, web_bind_host: '127.0.0.1', webui_tls: false,
  log_max_mb: 8, log_backup_count: 5,
  webui_password_set: false,
}
const cfg = reactive({})
const loaded = ref(false)
const loadError = ref('')
const configEpoch = ref(0)

function applyConfig(obj) {
  Object.keys(cfg).forEach((k) => delete cfg[k])
  Object.assign(cfg, JSON.parse(JSON.stringify(obj || {})))
  Object.keys(DEFAULTS).forEach((key) => { if (!(key in cfg)) cfg[key] = DEFAULTS[key] })
  if (!cfg.group_name_map || typeof cfg.group_name_map !== 'object' || Array.isArray(cfg.group_name_map)) cfg.group_name_map = {}
}

async function load() {
  // 首次读取才进骨架态：分区保存后的重读不该把页面（连同它刚显示的结果）整块拆掉重来。
  if (!loaded.value) loaded.value = false
  loadError.value = ''
  const res = await get('/api/config')
  if (res.ok && res.data && typeof res.data === 'object' && !Array.isArray(res.data)) {
    applyConfig(res.data)
    loaded.value = true
    configEpoch.value++
  } else {
    // 读不到就停在错误态：若渲染出只含 DEFAULTS 的空表单，一次保存就会覆盖真实配置。
    loadError.value = res.data?.error || (res.status ? `HTTP ${res.status}` : '无法连接桥接服务')
    toast('加载配置失败：' + loadError.value, 'error')
  }
}
function selectSection(id) {
  activeSection.value = id
  history.replaceState(null, '', `#/config?section=${id}`)
}
onMounted(() => {
  const raw = new URLSearchParams((window.location.hash.split('?')[1] || '')).get('section')
  if (SECTIONS.some((s) => s.id === raw)) activeSection.value = raw
  load()
})
</script>

<template>
  <div class="settings-page">
    <div v-if="!loaded && !loadError" class="card empty"><AppIcon name="LoaderCircle" :size="25" />正在读取配置…</div>
    <div v-else-if="loadError" class="card load-error"><AppIcon name="CircleAlert" :size="26" /><h3>无法读取配置</h3><p>{{ loadError }}</p><p class="hint">为避免用空值覆盖 config.json，配置编辑已停用。请确认桥接服务正在运行后重试。</p><button class="btn btn-outline" @click="load"><AppIcon name="RefreshCw" :size="15" />重新读取</button></div>
    <div v-else class="settings-layout">
      <SettingsNav :items="SECTIONS" :active="activeSection" @select="selectSection" />
      <div class="settings-content">
        <AccessSection v-show="activeSection === 'access'" :cfg="cfg" :active="activeSection === 'access'" @reload="load" />
        <StorageSection v-show="activeSection === 'storage'" :cfg="cfg" :active="activeSection === 'storage'" />
        <AdvancedSection v-show="activeSection === 'advanced'" :cfg="cfg" :active="activeSection === 'advanced'" :epoch="configEpoch" @reload="load" />
        <AboutSection v-show="activeSection === 'about'" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-page { display:flex; flex-direction:column; gap:16px; }
.settings-layout { display:grid; grid-template-columns:220px minmax(0,1fr); gap:16px; align-items:start; }
@media(max-width:1000px) { .settings-layout { grid-template-columns:185px minmax(0,1fr); } }
@media(max-width:767px) {
  .settings-page { gap:12px; }
  .settings-layout { display:flex; flex-direction:column; gap:12px; }
}
.load-error { display:flex; flex-direction:column; align-items:center; gap:9px; padding:48px 24px; text-align:center; box-shadow:none; }
.load-error > .app-icon { color:var(--danger); }
.load-error h3 { font-size:14px; font-weight:600; }
.load-error p { max-width:420px; font-size:12px; color:var(--muted); line-height:1.8; }
.load-error .btn { margin-top:8px; }
</style>
