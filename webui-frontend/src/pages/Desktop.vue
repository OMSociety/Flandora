<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { get, post } from '../api'
import { toast } from '../store'
import { useDesktopShot } from '../useDesktopShot'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
const desktopStatus = ref(null)
const busy = ref(false)
const stopDialog = ref(false)
const autoRefresh = ref(true)
const { imgUrl, lastError, shotAt, natural, refresh: refreshShot, onImgLoad, click, clear } = useDesktopShot()
let timer = null
let refreshing = false
const enabled = computed(() => !!desktopStatus.value?.enabled)
const running = computed(() => !!desktopStatus.value?.running)
const state = computed(() => desktopStatus.value?.state || 'absent')
const stateLabel = computed(() => ({ chat: '已登录，正在使用', login: '等待扫码或手机确认', absent: '微信未运行' }[state.value] || '状态未知'))
async function loadStatus() {
  const res = await get('/api/desktop/status', true)
  if (res.ok && res.data?.ok) desktopStatus.value = res.data
  else if (res.data?.error) lastError.value = res.data.error
}
async function onClickShot(event) {
  const error = await click(event)
  if (error) toast(error, 'error')
  setTimeout(refreshShot, 300)
}
async function startWeChat() {
  busy.value = true
  const res = await post('/api/desktop/start', {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { toast('微信已在隔离桌面上启动', 'success'); await loadStatus(); await refreshShot() }
  else toast(res.data?.error || '启动失败', 'error')
}
async function stopWeChat() {
  stopDialog.value = false
  busy.value = true
  const res = await post('/api/desktop/stop', {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { toast('已结束隔离桌面上的微信', 'success'); clear(); await loadStatus() }
  else toast(res.data?.error || '停止失败', 'error')
}
const defaultWeChatBlocking = computed(() => /已有微信在运行/.test(desktopStatus.value?.error || ''))
async function quitDefaultWeChat() {
  busy.value = true
  const res = await post('/api/desktop/quit-default-wechat', {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { toast('已结束桌面上的微信，可以重新启动了', 'success'); await loadStatus() }
  else toast(res.data?.error || '结束失败', 'error')
}
onMounted(async () => {
  await loadStatus()
  await refreshShot()
  timer = setInterval(async () => {
    if (!autoRefresh.value || refreshing || document.hidden) return
    refreshing = true
    try { await loadStatus(); await refreshShot() }
    catch (error) { lastError.value = '自动刷新失败：' + (error?.message || error) }
    finally { refreshing = false }
  }, 2000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
<template>
  <div class="desktop-page">
    <div class="page-intro"><div><h2>微信的另一张桌面，近在眼前。</h2><p>查看隔离桌面实况，完成登录和必要的窗口操作。</p></div><span class="badge" :class="running ? 'badge-green' : 'badge-gray'">{{ stateLabel }}</span></div>
    <div v-if="!enabled" class="banner"><AppIcon name="Info" :size="17" /><span>隔离桌面模式未开启。请在「桥接配置」中将 <code>isolated_desktop</code> 设为 true 并重启。开启后，自动化操作不会抢占你正在使用的桌面。</span></div>
    <div v-if="desktopStatus && !desktopStatus.exe_found" class="banner banner-danger"><AppIcon name="CircleAlert" :size="17" /><span>这台电脑上没找到微信。装好微信桌面版后刷新本页即可；装在非默认目录的话，在「桥接配置」里把 <code>wechat_exe</code> 填成 <code>Weixin.exe</code> 的完整路径。</span></div>
    <section class="card viewer-card"><div class="viewer-toolbar"><div class="viewer-title"><AppIcon name="Monitor" :size="17" /><strong>隔离桌面实况</strong><span v-if="desktopStatus?.desktop" class="desktop-name">{{ desktopStatus.desktop }}</span></div><div class="viewer-actions"><label class="chk"><input v-model="autoRefresh" type="checkbox" />自动刷新</label><button class="icon-btn" title="立即刷新" aria-label="立即刷新微信画面" @click="refreshShot"><AppIcon name="RefreshCw" :size="16" /></button><button class="btn btn-outline btn-sm" :disabled="busy || (desktopStatus && !desktopStatus.exe_found)" @click="startWeChat"><AppIcon name="Play" :size="13" />{{ running ? '重新确认启动' : '启动微信' }}</button><button v-if="running" class="btn btn-outline-danger btn-sm" :disabled="busy" @click="stopDialog = true"><AppIcon name="Square" :size="12" />结束</button></div></div><div v-if="lastError || desktopStatus?.error" class="viewer-error"><AppIcon name="CircleAlert" :size="16" />{{ lastError || desktopStatus.error }}</div><div v-if="defaultWeChatBlocking" class="viewer-quit"><button class="btn btn-outline-danger btn-sm" :disabled="busy" @click="quitDefaultWeChat"><AppIcon name="Square" :size="12" />结束桌面上的微信</button><span>结束的是你在自己桌面上开着的微信，点一下才会关。</span></div><div class="shot-wrap"><img v-if="imgUrl" :src="imgUrl" class="shot" alt="隔离桌面上的微信窗口，点击可转发操作" @load="onImgLoad" @click="onClickShot" /><div v-else class="shot-empty"><div class="empty-screen"><AppIcon name="Monitor" :size="32" /></div><strong>{{ running ? '正在等待微信画面' : (desktopStatus && !desktopStatus.exe_found ? '没有可启动的微信' : '微信尚未启动') }}</strong><span>{{ running ? '画面将在就绪后自动显示。' : (desktopStatus && !desktopStatus.exe_found ? '装好微信桌面版后刷新本页。' : '点击「启动微信」开始查看隔离桌面。') }}</span></div></div><div class="viewer-footer"><span><AppIcon name="Info" :size="13" />点击画面可将操作转发到微信</span><div><span v-if="natural.w">{{ natural.w }} × {{ natural.h }}</span><span v-if="shotAt">更新于 {{ shotAt }}</span></div></div></section>
    <section class="desktop-info"><div><span class="info-label">运行模式</span><strong>{{ enabled ? '隔离桌面' : '未开启' }}</strong></div><div><span class="info-label">微信进程</span><strong>{{ running ? '运行中' : '未运行' }}</strong></div><div><span class="info-label">登录状态</span><strong :class="{ warning: state === 'login' }">{{ stateLabel }}</strong></div><p>登录时在画面中点击「登录」，然后按微信提示使用手机扫码或确认。</p></section>
    <ConfirmDialog
      :open="stopDialog"
      title="结束隔离桌面上的微信"
      message="结束微信会断开当前登录与发送通道，未发送的内容可能丢失。确定结束吗？"
      confirm-text="结束微信"
      :danger="true"
      @cancel="stopDialog = false"
      @confirm="stopWeChat"
    />
  </div>
</template>
<style scoped>
.desktop-page { display:flex; flex-direction:column; gap:18px; }
.viewer-card { padding:0; overflow:hidden; box-shadow:none; }
.viewer-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 16px; border-bottom:1px solid var(--border); }
.viewer-title, .viewer-actions { display:flex; align-items:center; gap:9px; }
.viewer-title > .app-icon { color:var(--primary-deep); }
.viewer-title strong { font-size:12px; font-weight:500; }
.desktop-name { color:var(--muted); font-size:10px; padding-left:8px; border-left:1px solid var(--border); }
.viewer-actions .chk { font-size:10px; }
.viewer-error { display:flex; align-items:center; gap:8px; padding:10px 16px; color:var(--danger); background:var(--danger-soft); font-size:11px; }
.viewer-quit { display:flex; align-items:center; gap:10px; padding:10px 16px; border-bottom:1px solid var(--line); background:var(--surface-2); }
.viewer-quit span { font-size:11px; color:var(--muted); }
.shot-wrap { display:flex; align-items:center; justify-content:center; min-height:400px; padding:20px; background:var(--inset); }
.shot { display:block; max-width:100%; max-height:65dvh; border:1px solid var(--border-strong); border-radius:6px; background:var(--media-bg); cursor:crosshair; box-shadow:0 8px 28px rgb(0 0 0 / .12); }
.shot-empty { display:flex; align-items:center; flex-direction:column; gap:8px; min-height:330px; justify-content:center; color:var(--muted); }
.empty-screen { display:grid; place-items:center; width:66px; height:54px; margin-bottom:9px; border:1px solid var(--border-strong); border-radius:8px; color:var(--faint); }
.shot-empty strong { color:var(--text); font-size:13px; font-weight:500; }
.shot-empty span { font-size:11px; }
.viewer-footer { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 16px; color:var(--muted); font-size:10px; }
.viewer-footer > span, .viewer-footer > div { display:flex; align-items:center; gap:7px; }
.viewer-footer > div { gap:16px; }
.desktop-info { display:flex; align-items:center; gap:36px; padding:3px 3px 0; }
.desktop-info > div { display:flex; flex-direction:column; gap:4px; }
.info-label { font-size:10px; color:var(--muted); }
.desktop-info strong { font-size:12px; font-weight:500; }
.desktop-info strong.warning { color:var(--warning); }
.desktop-info > p { margin-left:auto; max-width:330px; font-size:10px; color:var(--muted); line-height:1.7; }
@media(max-width:1000px) { .viewer-toolbar { align-items:flex-start; } .viewer-actions { flex-wrap:wrap; justify-content:flex-end; } .desktop-info { flex-wrap:wrap; gap:20px; } .desktop-info > p { width:100%; margin-left:0; max-width:none; } }
@media(max-width:767px) { .viewer-toolbar { flex-direction:column; } .viewer-actions { width:100%; justify-content:flex-start; } .viewer-actions .btn { font-size:10px; } .shot-wrap { min-height:310px; padding:12px; } .shot-empty { min-height:260px; } .viewer-footer { align-items:flex-start; flex-direction:column; gap:6px; } .desktop-info { gap:18px; } }
</style>
