<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, post } from '../../api'
import { status } from '../../store'
import AppIcon from '../AppIcon.vue'
import ConfirmDialog from '../ConfirmDialog.vue'

const REPO_URL = 'https://github.com/OMSociety/Flandora'

const updateInfo = ref({ mode: '', current: '', app_dir_writable: true, pending: null, latest: null })
const updateBusy = ref('')
const updateMsg = ref('')
const updateError = ref(false)
// 安装会退出程序并替换文件，属于破坏性操作：先走确认框，别让一次误点直接换掉正在运行的程序。
const installDialog = ref(false)
const pendingUpdate = computed(() => updateInfo.value.pending)
const latestUpdate = computed(() => updateInfo.value.latest)
const canInstallUpdate = computed(() => updateInfo.value.mode === 'frozen' && updateInfo.value.app_dir_writable)
const modeText = computed(() => (updateInfo.value.mode === 'frozen' ? '打包安装' : '源码运行'))
function updateErrorText(res, fallback) { return (res && res.data && res.data.error) || fallback }

async function loadUpdate() {
  const res = await get('/api/update/status')
  if (res.ok && res.data) updateInfo.value = { ...updateInfo.value, ...res.data }
}
async function checkUpdate() {
  updateBusy.value = 'check'; updateMsg.value = ''
  const res = await post('/api/update/check')
  updateBusy.value = ''
  if (res.ok && res.data && res.data.ok) {
    updateInfo.value = { ...updateInfo.value, latest: res.data.latest }
    updateMsg.value = res.data.newer ? `发现新版本 ${res.data.latest.version}` : '已经是最新版本'
    updateError.value = false
  } else { updateMsg.value = updateErrorText(res, '检查失败'); updateError.value = true }
}
async function downloadUpdate() {
  updateBusy.value = 'download'; updateMsg.value = ''
  const res = await post('/api/update/download')
  updateBusy.value = ''
  if (res.ok && res.data && res.data.ok) {
    updateInfo.value = { ...updateInfo.value, pending: res.data.pending }
    updateMsg.value = '安装包已下载并暂存'; updateError.value = false
  } else { updateMsg.value = updateErrorText(res, '下载失败'); updateError.value = true }
}
async function installUpdate() {
  updateBusy.value = 'install'; updateMsg.value = ''
  const res = await post('/api/update/install')
  updateBusy.value = ''
  if (res.ok && res.data && res.data.ok) { updateMsg.value = res.data.message || '正在退出并替换，完成后会自动重启'; updateError.value = false }
  else { updateMsg.value = updateErrorText(res, '安装失败'); updateError.value = true }
}
async function cancelUpdate() {
  updateBusy.value = 'cancel'; updateMsg.value = ''
  const res = await post('/api/update/cancel')
  updateBusy.value = ''
  if (res.ok && res.data && res.data.ok) { updateInfo.value = { ...updateInfo.value, pending: null }; updateMsg.value = '已放弃待安装的包'; updateError.value = false }
  else { updateMsg.value = updateErrorText(res, '取消失败'); updateError.value = true }
}

onMounted(loadUpdate)
</script>

<template>
  <section class="card settings-section about-section" aria-labelledby="about-heading">
    <div class="section-head">
      <div>
        <h3 id="about-heading" class="card-title">关于 Flandora</h3>
        <p class="card-description">把微信消息桥接到 OneBot v11 生态的本地适配器。</p>
      </div>
      <AppIcon name="Info" :size="19" />
    </div>

    <div class="update-head">
      <div>
        <h4 class="about-sub-title">版本与更新</h4>
        <p class="update-current">当前 <span class="mono">{{ updateInfo.current || status.version }}</span><span class="update-sep"> · </span>{{ modeText }}</p>
      </div>
      <button class="btn btn-outline btn-sm" :disabled="!!updateBusy" @click="checkUpdate">{{ updateBusy === 'check' ? '检查中…' : '检查更新' }}</button>
    </div>
    <p v-if="updateMsg" class="update-msg" :class="{ bad: updateError }">{{ updateMsg }}</p>
    <div v-if="latestUpdate" class="update-line">
      <span><AppIcon name="Info" :size="15" />最新 {{ latestUpdate.version }}</span>
      <a :href="latestUpdate.url" target="_blank" rel="noreferrer">发布页<AppIcon name="ArrowUpRight" :size="13" /></a>
      <button v-if="!pendingUpdate" class="btn btn-sm" :disabled="!!updateBusy" @click="downloadUpdate">{{ updateBusy === 'download' ? '下载中…' : '下载并暂存' }}</button>
    </div>
    <div v-if="pendingUpdate" class="update-line pending">
      <span><AppIcon name="ArrowDownToLine" :size="15" />{{ pendingUpdate.version }} 已下载暂存</span>
      <template v-if="canInstallUpdate">
        <button class="btn btn-sm" :disabled="!!updateBusy" @click="installDialog = true">{{ updateBusy === 'install' ? '安装中…' : '安装并重启' }}</button>
        <button class="btn btn-outline btn-sm" :disabled="!!updateBusy" @click="cancelUpdate">取消</button>
      </template>
      <span v-else class="hint">{{ updateInfo.mode === 'frozen' ? '程序目录不可写，请以管理员身份运行 data/updates 里的脚本' : '源码模式：在仓库里 git pull 后重装依赖' }}</span>
    </div>

    <div class="about-links">
      <h4 class="about-sub-title">项目</h4>
      <div class="link-list">
        <a :href="REPO_URL" target="_blank" rel="noreferrer">仓库<AppIcon name="ArrowUpRight" :size="13" /></a>
        <a :href="`${REPO_URL}/releases`" target="_blank" rel="noreferrer">发行版<AppIcon name="ArrowUpRight" :size="13" /></a>
        <a :href="`${REPO_URL}/issues`" target="_blank" rel="noreferrer">反馈问题<AppIcon name="ArrowUpRight" :size="13" /></a>
      </div>
    </div>
    <ConfirmDialog
      :open="installDialog"
      title="安装并重启"
      message="安装会退出当前程序并替换程序文件，期间消息收发中断，替换完成后自动重新启动。确定现在安装吗？"
      confirm-text="安装并重启"
      :danger="true"
      @cancel="installDialog = false"
      @confirm="installDialog = false; installUpdate()"
    />
  </section>
</template>

<style scoped>
.settings-section { min-height:0; box-shadow:none; padding:20px; }
.section-head > .app-icon { color:var(--faint); }
.section-head .card-description { margin-bottom:0; }
.about-sub-title { font-size:13.5px; font-weight:600; color:var(--text-strong); }
.update-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-top:18px; }
.update-current { margin-top:4px; font-size:12px; line-height:1.55; color:var(--muted); }
.update-current .mono { color:var(--text); }
.update-sep { margin:0 2px; color:var(--border-strong); }
.update-msg { margin:12px 0 0; padding-top:12px; border-top:1px solid var(--border); font-size:12px; line-height:1.6; color:var(--success); }
.update-msg.bad { color:var(--danger); }
.update-line { display:flex; flex-wrap:wrap; align-items:center; gap:8px 14px; margin-top:12px; padding-top:12px; border-top:1px solid var(--border); font-size:12px; line-height:1.6; }
.update-line span, .update-line a { display:inline-flex; align-items:center; gap:6px; }
.update-line a { color:var(--primary-deep); text-decoration:none; }
.update-line a:hover { text-decoration:underline; }
.update-line .btn { margin-left:auto; }
.update-line.pending { margin-top:8px; }
.update-line .hint { flex-basis:100%; margin:0; }
.about-links { margin-top:22px; padding-top:16px; border-top:1px solid var(--border); }
.link-list { display:flex; flex-wrap:wrap; gap:8px 22px; margin-top:12px; }
.link-list a { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--primary-deep); text-decoration:none; }
.link-list a:hover { text-decoration:underline; }
@media(max-width:767px) {
  .settings-section { padding:17px; }
  .update-head { align-items:flex-start; }
  .update-line { gap:8px 10px; }
  .update-line .btn { margin-left:0; }
}
</style>
