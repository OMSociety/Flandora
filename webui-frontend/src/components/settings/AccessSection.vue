<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import AppIcon from '../AppIcon.vue'
import PasswordInput from '../PasswordInput.vue'
import ConfirmDialog from '../ConfirmDialog.vue'
import SectionFoot from './SectionFoot.vue'
import { useSettingsSave } from '../../composables/useSettingsSave'
import { get, post } from '../../api'
import { toast } from '../../store'

const props = defineProps({
  cfg: { type: Object, required: true },
  active: { type: Boolean, default: true },
})
const emit = defineEmits(['reload'])

// 证书文件是否真的在盘上、当前监听在哪，只有服务端自己知道，配置接口答不了，所以读 /api/status。
const webui = ref({ port: 4950, host: '127.0.0.1', tls: false, tls_cert_set: false, tls_key_set: false })
async function loadStatus() {
  const res = await get('/api/status', true)
  if (res.ok && res.data && res.data.webui) webui.value = res.data.webui
}
onMounted(() => { loadStatus(); loadAutostart() })
watch(() => props.active, (on) => { if (on) { loadStatus(); loadAutostart() } })

const restartDialog = ref(false)
const restarting = ref(false)
const restartFrom = ref('')

async function restartBridge() {
  if (restarting.value) return
  restarting.value = true
  const res = await post('/api/control', { action: 'restart' })
  restarting.value = false
  restartDialog.value = false
  if (res.ok && res.data && res.data.ok !== false) toast('已重启桥接服务', 'success')
  else toast('重启失败' + (res.data?.error ? ': ' + res.data.error : ''), 'error')
}

// ---------- 面板访问 ----------

const pwEnabled = ref(!!props.cfg.webui_password_set)
const pwNew = ref('')
const { saving: pwSaving, saved: pwSaved, save: savePassword } = useSettingsSave()
watch(() => props.cfg.webui_password_set, (set) => { pwEnabled.value = !!set })
const pwDirty = computed(() => pwEnabled.value !== !!props.cfg.webui_password_set || !!pwNew.value)
// 清密码要顺带收回监听：绑在所有网卡上时，后端不许留「0.0.0.0 + 无密码」，
// 硬这么干的话重启后面板直接拒绝启动。
const clearDialog = ref(false)
const clearingOpenBind = computed(() => !pwEnabled.value && props.cfg.web_bind_host === '0.0.0.0')

async function applyPassword() {
  let payload
  if (!pwEnabled.value) {
    if (clearingOpenBind.value) { clearDialog.value = true; return }
    payload = { webui_password_clear: true }
  } else if (pwNew.value) {
    if (pwNew.value.length < 6) { toast('密码至少 6 位', 'error'); return }
    payload = { webui_password: pwNew.value }
  } else if (!props.cfg.webui_password_set) { toast('启用登录要设一个密码', 'error'); return }
  else return
  if (await savePassword(payload)) {
    pwNew.value = ''
    emit('reload')
  }
}

async function clearPasswordAndBind() {
  clearDialog.value = false
  if (await savePassword({ webui_password_clear: true, web_bind_host: '127.0.0.1' })) {
    pwNew.value = ''
    emit('reload')
  }
}

// ---------- 服务监听 ----------

const { saving: listenSaving, saved: listenSaved, save: saveListenConfig } = useSettingsSave()
const portSaved = ref(false)

function listenPayload() {
  return {
    web_port: Number(props.cfg.web_port) || 0,
    web_bind_host: props.cfg.web_bind_host || '127.0.0.1',
  }
}
async function saveListen() {
  restartFrom.value = ''
  if (await saveListenConfig(listenPayload())) {
    portSaved.value = true
    restartFrom.value = 'listen'
    loadStatus()
  }
}
function onListenInput() { portSaved.value = false; restartFrom.value = '' }

// 监听地址要对外开放时，先得有一道门：没有密码就只让本机连。
const canOpenBind = computed(() => !!props.cfg.webui_password_set || props.cfg.web_bind_host === '0.0.0.0')

// ---------- HTTPS ----------

const certPem = ref('')
const keyPem = ref('')
const tlsTouched = ref(false)
const { saving: tlsSaving, saved: tlsSaved, save: saveTlsConfig } = useSettingsSave()
const installed = computed(() => !!(webui.value.tls_cert_set && webui.value.tls_key_set))
const certDirty = computed(() => !!certPem.value.trim() || !!keyPem.value.trim() || tlsTouched.value)
const canToggleTls = computed(() => installed.value || (!!certPem.value.trim() && !!keyPem.value.trim()))

async function saveTls() {
  restartFrom.value = ''
  const payload = { webui_tls: !!props.cfg.webui_tls }
  if (certPem.value.trim() || keyPem.value.trim()) {
    payload.webui_tls_cert = certPem.value.trim()
    payload.webui_tls_key = keyPem.value.trim()
  }
  if (await saveTlsConfig(payload)) {
    certPem.value = ''
    keyPem.value = ''
    tlsTouched.value = false
    if (Object.keys(payload).some((key) => key !== 'webui_tls')) restartFrom.value = 'tls'
    await loadStatus()
  }
}

// ---------- 开机启动 ----------

const autostart = ref({ enabled: false, command: '', reason: '' })
const autostartBusy = ref(false)
const autostartError = ref('')

async function loadAutostart() {
  const res = await get('/api/autostart', true)
  if (res.ok && res.data) {
    autostart.value = {
      enabled: !!res.data.enabled,
      command: res.data.command || '',
      reason: res.data.reason || '',
    }
  }
}

async function toggleAutostart(event) {
  const want = event.target.checked
  autostartError.value = ''
  autostartBusy.value = true
  const res = await post('/api/autostart', { enabled: want })
  autostartBusy.value = false
  if (!res.ok || !res.data || res.data.ok === false) {
    event.target.checked = !want
    autostartError.value = '设置失败：' + (res.data?.error || `HTTP ${res.status}`)
    return
  }
  await loadAutostart()
  toast(want ? '已设置开机自启' : '已取消开机自启', 'success')
}

// ---------- 备份与恢复 ----------

const exportSensitive = ref(false)
const restoreSensitive = ref(false)
const backupMsg = ref('')
const restoreMsg = ref('')
const pending = ref(null)
const restoreDialog = ref(false)
const filePicker = ref(null)

function stamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
}

async function exportBackup() {
  backupMsg.value = ''
  const res = await get('/api/config/backup?include_sensitive=' + (exportSensitive.value ? 1 : 0))
  if (!res.ok || !res.data || !res.data.config) {
    backupMsg.value = '导出失败：' + (res.data?.error || `HTTP ${res.status}`)
    return
  }
  const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `flandora-config-${stamp()}.json`
  link.click()
  URL.revokeObjectURL(url)
  const excluded = res.data.excluded_sensitive || []
  backupMsg.value = `已导出 ${Object.keys(res.data.config).length} 项配置`
    + (excluded.length ? `（未包含 ${excluded.join('、')}）` : '（包含令牌与密码）')
}

function pickBackup() { filePicker.value?.click() }

async function onBackupChosen(event) {
  const file = event.target.files && event.target.files[0]
  event.target.value = ''
  if (!file) return
  restoreMsg.value = ''
  let parsed
  try {
    parsed = JSON.parse(await file.text())
  } catch (err) {
    restoreMsg.value = '这个文件不是有效的 JSON。'
    return
  }
  if (!parsed || typeof parsed.config !== 'object' || Array.isArray(parsed.config) || !Object.keys(parsed.config).length) {
    restoreMsg.value = '这个文件不是 Flandora 的配置备份。'
    return
  }
  pending.value = parsed
  restoreDialog.value = true
}

async function applyRestore() {
  restoreDialog.value = false
  if (!pending.value) return
  const res = await post('/api/config/restore', {
    config: pending.value.config,
    include_sensitive: restoreSensitive.value ? 1 : 0,
  })
  pending.value = null
  if (!res.ok || !res.data || res.data.ok === false) {
    restoreMsg.value = '恢复失败：' + (res.data?.error || `HTTP ${res.status}`)
    return
  }
  const restored = res.data.restored || []
  const needRestart = res.data.restart_required || []
  restoreMsg.value = `已恢复 ${restored.length} 项配置；改动前的 config.json 已另存为 ${res.data.backup_path}。`
    + (res.data.password_restored ? ' 面板密码已按备份替换，需要重新登录。' : '')
  toast('配置已恢复', 'success')
  if (needRestart.length) restartFrom.value = 'restore'
  emit('reload')
  await loadStatus()
}
</script>

<template>
  <div class="access-section">
    <section class="card settings-section" aria-labelledby="access-password-heading">
      <div class="section-head">
        <div>
          <h3 id="access-password-heading" class="card-title">面板访问</h3>
          <p class="card-description">控制谁能打开这块面板。</p>
        </div>
        <AppIcon name="KeyRound" :size="19" />
      </div>
      <div class="grid-2">
        <div class="field">
          <label class="check-label"><input type="checkbox" v-model="pwEnabled" /> 打开面板时要密码</label>
          <PasswordInput v-if="pwEnabled" id="webui-password" v-model="pwNew" :placeholder="cfg.webui_password_set ? '已设置，留空表示不改' : '至少 6 位'" />
          <p class="hint">{{ cfg.webui_password_set ? '改密码会让其它浏览器上已登录的会话立刻失效。' : '设置后这块面板与它的全部接口都要先登录；不开密码时面板只监听本机（要绑所有网卡得先设密码）。' }}</p>
        </div>
      </div>
      <SectionFoot :saving="pwSaving" :saved="pwSaved" :disabled="!pwDirty" label="保存密码" @save="applyPassword" />
    </section>

    <section class="card settings-section" aria-labelledby="access-listen-heading">
      <div class="section-head">
        <div>
          <h3 id="access-listen-heading" class="card-title">服务监听</h3>
          <p class="card-description">面板在哪个地址、哪个端口上等连接。</p>
        </div>
        <AppIcon name="Server" :size="19" />
      </div>
      <div class="grid-2">
        <div class="field">
          <label for="web-port">Web 面板端口</label>
          <input id="web-port" v-model.number="cfg.web_port" class="input" type="number" min="1" max="65535" @input="onListenInput" />
        </div>
        <div class="field">
          <span class="field-label">绑定地址</span>
          <label class="radio-label"><input type="radio" name="bind-host" value="127.0.0.1" v-model="cfg.web_bind_host" @change="onListenInput" /> 仅本机 127.0.0.1</label>
          <label class="radio-label"><input type="radio" name="bind-host" value="0.0.0.0" v-model="cfg.web_bind_host" :disabled="!canOpenBind" @change="onListenInput" /> 所有网卡 0.0.0.0</label>
          <p v-if="!canOpenBind" class="hint">要先在「面板访问」里设一个密码，才允许局域网连进来。</p>
        </div>
      </div>
      <p class="hint restart-hint"><AppIcon name="CircleAlert" :size="14" />当前监听：{{ webui.host }}:{{ webui.port }}；端口与绑定地址保存后都要重启桥接才换绑。</p>
      <div v-if="restartFrom === 'listen'" class="restart-row">
        <span>{{ portSaved ? '监听设置已保存，重启桥接服务后生效。' : '配置已改动，重启桥接服务后生效。' }}</span>
        <button type="button" class="btn btn-outline btn-sm" :disabled="restarting" @click="restartDialog = true"><AppIcon name="RotateCcw" :size="14" />重启桥接服务</button>
      </div>
      <SectionFoot :saving="listenSaving" :saved="listenSaved" label="保存监听" hint="端口与绑定地址重启后生效" @save="saveListen" />
    </section>

    <section class="card settings-section" aria-labelledby="access-tls-heading">
      <div class="section-head">
        <div>
          <h3 id="access-tls-heading" class="card-title">HTTPS 证书</h3>
          <p class="card-description">给面板套一层 TLS，证书只留在服务器上。</p>
        </div>
        <AppIcon name="Lock" :size="19" />
      </div>
      <p class="cert-state" :class="installed ? 'ok' : 'missing'">
        <AppIcon :name="installed ? 'CircleCheck' : 'CircleAlert'" :size="15" />
        {{ installed ? '证书已装好' : '还没有证书' }}<span v-if="webui.tls"> · 当前正以 HTTPS 提供服务</span>
      </p>
      <label class="check-label"><input type="checkbox" v-model="cfg.webui_tls" :disabled="!canToggleTls" @change="tlsTouched = true" /> 用 HTTPS 提供面板</label>
      <div class="grid-2">
        <div class="field">
          <label for="tls-cert">证书 PEM</label>
          <textarea id="tls-cert" v-model="certPem" class="input mono" rows="4" placeholder="-----BEGIN CERTIFICATE-----"></textarea>
        </div>
        <div class="field">
          <label for="tls-key">私钥 PEM</label>
          <textarea id="tls-key" v-model="keyPem" class="input mono" rows="4" placeholder="-----BEGIN PRIVATE KEY-----"></textarea>
        </div>
      </div>
      <p class="hint">两段要一起贴；私钥写进服务器后权限收到 600，不会进 config.json。证书或私钥解析不了会当场拒绝保存。重启桥接后生效。</p>
      <div v-if="restartFrom === 'tls'" class="restart-row">
        <span>证书已写入，重启桥接服务后生效。</span>
        <button type="button" class="btn btn-outline btn-sm" :disabled="restarting" @click="restartDialog = true"><AppIcon name="RotateCcw" :size="14" />重启桥接服务</button>
      </div>
      <SectionFoot :saving="tlsSaving" :saved="tlsSaved" :disabled="!certDirty" label="保存证书" hint="替换证书后重启生效" @save="saveTls" />
    </section>

    <section class="card settings-section" aria-labelledby="access-autostart-heading">
      <div class="section-head">
        <div>
          <h3 id="access-autostart-heading" class="card-title">开机启动</h3>
          <p class="card-description">登录 Windows 后自动在托盘里启动管理器，桥接跟着起来。</p>
        </div>
        <AppIcon name="Zap" :size="19" />
      </div>
      <label class="check-label">
        <input type="checkbox" :checked="autostart.enabled" :disabled="autostartBusy || !autostart.command" @change="toggleAutostart" />
        登录后自动启动 Flandora
      </label>
      <p v-if="autostart.command" class="autostart-command mono">开机执行：{{ autostart.command }}</p>
      <p v-if="!autostart.command && autostart.reason" class="hint">{{ autostart.reason }}</p>
      <p v-if="autostartError" class="autostart-error">{{ autostartError }}</p>
      <p v-else class="hint">写在当前用户的 Run 项里，不需要管理员权限；勾选或取消立刻生效。</p>
    </section>

    <section class="card settings-section" aria-labelledby="access-backup-heading">
      <div class="section-head">
        <div>
          <h3 id="access-backup-heading" class="card-title">配置备份与恢复</h3>
          <p class="card-description">把 config.json 带走或者搬回来。</p>
        </div>
        <AppIcon name="Download" :size="19" />
      </div>
      <div class="backup-block">
        <label class="check-label"><input type="checkbox" v-model="exportSensitive" /> 导出时包含令牌与密码</label>
        <p class="hint">默认导出会剔掉 OneBot 令牌和面板密码；这份文件本身等同于钥匙，别随手传给别人。</p>
        <button type="button" class="btn btn-outline btn-sm" @click="exportBackup"><AppIcon name="Download" :size="14" />导出备份</button>
        <p v-if="backupMsg" class="backup-msg">{{ backupMsg }}</p>
      </div>
      <div class="backup-block">
        <label class="check-label"><input type="checkbox" v-model="restoreSensitive" /> 恢复时一并恢复令牌与密码</label>
        <p class="hint">只恢复备份里有的字段，其余保持现状；恢复前会把当前的 config.json 另存一份。</p>
        <button type="button" class="btn btn-outline btn-sm" @click="pickBackup"><AppIcon name="Upload" :size="14" />从备份恢复</button>
        <input ref="filePicker" class="file-input" type="file" accept=".json,application/json" @change="onBackupChosen" />
        <p v-if="restoreMsg" class="backup-msg">{{ restoreMsg }}</p>
      </div>
      <div v-if="restartFrom === 'restore'" class="restart-row">
        <span>备份里有需要重启才生效的配置。</span>
        <button type="button" class="btn btn-outline btn-sm" :disabled="restarting" @click="restartDialog = true"><AppIcon name="RotateCcw" :size="14" />重启桥接服务</button>
      </div>
    </section>

    <ConfirmDialog
      :open="restartDialog"
      title="重启桥接服务"
      message="重启期间消息收发会中断几秒钟，面板会自动恢复连接。确定现在重启吗？"
      confirm-text="重启"
      @cancel="restartDialog = false"
      @confirm="restartBridge"
    />
    <ConfirmDialog
      :open="clearDialog"
      title="清掉密码并收回监听"
      message="面板现在绑在所有网卡上，没有密码就不允许这么绑。确定后密码会被清掉、绑定地址同时改回 127.0.0.1（只有本机能连）；绑定地址要重启桥接才换。"
      confirm-text="清掉密码"
      :danger="true"
      @cancel="clearDialog = false"
      @confirm="clearPasswordAndBind"
    />
    <ConfirmDialog
      :open="restoreDialog"
      title="用这份备份覆盖配置"
      message="备份里的字段会覆盖当前设置，其余字段保持不动；改动前的 config.json 会自动另存一份。确定继续吗？"
      confirm-text="恢复"
      @cancel="restoreDialog = false; pending = null"
      @confirm="applyRestore"
    />
  </div>
</template>

<style scoped>
.access-section { display:flex; flex-direction:column; gap:16px; }
.settings-section { min-height:0; box-shadow:none; padding:20px; }
.section-head > .app-icon { color:var(--faint); }
.section-head .card-description { margin-bottom:0; }
.field-label { display:block; margin-bottom:6px; font-size:12px; color:var(--muted); }
.check-label { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--text); }
.check-label input { width:15px; height:15px; accent-color:var(--primary-strong); }
.radio-label { display:flex; align-items:center; gap:8px; margin-top:7px; font-size:12px; color:var(--text); }
.radio-label input { width:15px; height:15px; accent-color:var(--primary-strong); }
.radio-label input:disabled + * { color:var(--muted); }
.radio-label:has(input:disabled) { color:var(--muted); }
.cert-state { display:flex; align-items:center; gap:7px; margin:4px 0 14px; font-size:12px; color:var(--muted); }
.cert-state > .app-icon { color:var(--primary-strong); }
.cert-state.missing > .app-icon { color:var(--warning); }
.mono { font-family:var(--mono, ui-monospace, monospace); font-size:11px; resize:vertical; }
.autostart-command { margin-top:12px; font-size:11px; color:var(--muted); line-height:1.7; word-break:break-all; }
.autostart-error { margin-top:9px; font-size:11px; color:var(--danger); line-height:1.7; word-break:break-all; }
.backup-block { padding:12px 14px; border:1px solid var(--border); border-radius:10px; background:var(--inset); }
.backup-block + .backup-block { margin-top:12px; }
.backup-block .btn { margin-top:10px; }
.backup-msg { margin-top:9px; font-size:11px; color:var(--muted); line-height:1.7; word-break:break-all; }
.file-input { display:none; }
.restart-hint { display:flex; align-items:center; gap:6px; margin-top:16px; color:var(--warning); }
.restart-row { display:flex; flex-wrap:wrap; align-items:center; gap:10px 14px; margin-top:14px; padding:11px 14px; border:1px solid var(--border); border-radius:10px; background:var(--inset); font-size:12px; color:var(--muted); }
.restart-row span { display:inline-flex; align-items:center; }
.restart-row .btn { margin-left:auto; }
@media(max-width:767px) {
  .settings-section { padding:17px; }
  .radio-label { min-height:44px; }
  .check-label { min-height:44px; }
  .restart-row { align-items:flex-start; flex-direction:column; }
  .restart-row .btn { margin-left:0; }
}
</style>
