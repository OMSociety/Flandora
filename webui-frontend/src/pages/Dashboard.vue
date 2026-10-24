<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { get, post } from '../api'
import { status, refreshStatus, toast } from '../store'
import { formatUptime, fmt, hexHwnd, formatClock } from '../utils/format'
import HourlyBarChart from '../components/HourlyBarChart.vue'
import Modal from '../components/Modal.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import AppIcon from '../components/AppIcon.vue'

const stats = ref({ counters: {}, hourly: [], top_contacts: [] })
const busy = ref(false)
const winModal = ref(false)
const windows = ref([])
const winLoading = ref(false)
const winAction = ref(null)
const windowInfo = computed(() => status.window)
const isAutoActive = computed(() => !status.window_locked)

async function openWinModal() { winModal.value = true; await loadWindows() }
async function loadWindows() {
  winLoading.value = true
  const res = await get('/api/windows')
  winLoading.value = false
  if (res.ok && res.data) windows.value = res.data.windows || []
  else toast('识别窗口失败，请稍后重试', 'error')
}
async function flashWin(hwnd) {
  const res = await post('/api/windows/flash', { hwnd })
  if (res.ok && res.data && res.data.ok !== false) toast('请查看任务栏闪烁的微信图标', 'info')
  else toast('窗口闪烁失败', 'error')
}
async function selectWin(hwnd) {
  if (winAction.value === hwnd) return
  winAction.value = hwnd
  const res = await post('/api/windows/select', { hwnd })
  winAction.value = null
  if (res.ok && res.data && res.data.ok !== false) {
    toast(hwnd === 0 ? '已切换到自动选择模式' : '已绑定窗口', 'success')
    refreshStatus(true)
    await loadWindows()
  } else toast('绑定失败：' + ((res.data && res.data.error) || '未知错误'), 'error')
}

const bridgeCard = computed(() => !status.running ? { text: '未运行', cls: 'gray' } : status.paused ? { text: '已暂停', cls: 'amber' } : { text: '运行中', cls: 'green' })
const obCard = computed(() => status.ob?.connected ? { text: `${fmt(status.ob.clients)} 个客户端`, cls: 'green' } : { text: '未连接', cls: 'gray' })
const inboundCard = computed(() => status.inbound?.ready ? { text: '本地库就绪', cls: 'green' } : { text: '等待数据', cls: 'gray' })
const canStart = computed(() => !status.running && !busy.value)
const canStop = computed(() => status.running && !busy.value)
const canPause = computed(() => status.running && !status.paused && !busy.value)
const canResume = computed(() => status.running && status.paused && !busy.value)
const restartDialog = ref(false)
async function sendControl(action) {
  if (action === 'restart') { restartDialog.value = true; return }
  await applyControl(action)
}
async function applyControl(action) {
  busy.value = true
  const res = await post('/api/control', { action })
  busy.value = false
  if (res.ok && res.data && res.data.ok !== false) toast('操作成功', 'success')
  else toast('操作失败' + (res.data?.error ? ': ' + res.data.error : ''), 'error')
  refreshStatus(true)
}
async function loadStats() {
  const res = await get('/api/stats')
  if (res.ok && res.data) stats.value = { counters: res.data.counters || {}, hourly: res.data.hourly || [], top_contacts: res.data.top_contacts || [] }
  else toast('加载消息统计失败', 'error')
}
const updateInfo = ref({ mode: '', current: '', app_dir_writable: true, pending: null, latest: null })
const updateBusy = ref('')
const updateMsg = ref('')
const updateError = ref(false)
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
onMounted(() => { loadStats(); loadUpdate() })
</script>

<template>
  <div class="dashboard">
    <div class="page-intro">
      <div><h2>一眼看清桥接的每个环节。</h2><p>查看微信与 OneBot 客户端的连接状态。</p></div>
      <span class="version-label">Bridge {{ status.version || '1.0.0' }}</span>
    </div>
    <section class="card update-card">
      <div class="update-head">
        <div>
          <h3 class="card-title">版本与更新</h3>
          <p class="card-description">当前 {{ updateInfo.current || status.version }}，{{ modeText }}</p>
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
          <button class="btn btn-sm" :disabled="!!updateBusy" @click="installUpdate">{{ updateBusy === 'install' ? '安装中…' : '安装并重启' }}</button>
          <button class="btn btn-outline btn-sm" :disabled="!!updateBusy" @click="cancelUpdate">取消</button>
        </template>
        <span v-else class="hint">{{ updateInfo.mode === 'frozen' ? '程序目录不可写，请以管理员身份运行 data/updates 里的脚本' : '源码模式：在仓库里 git pull 后重装依赖' }}</span>
      </div>
    </section>

    <section class="card service-panel" aria-labelledby="service-heading">
      <div class="service-main">
        <div class="service-icon" :class="{ online: status.running }"><AppIcon :name="status.running ? 'Activity' : 'Zap'" :size="25" /></div>
        <div class="service-copy"><div class="service-title"><h3 id="service-heading">桥接服务</h3><span class="badge" :class="status.running ? 'badge-green' : 'badge-gray'">{{ bridgeCard.text }}</span></div><p>{{ status.running ? `已持续运行 ${formatUptime(status.uptime_sec)}` : '启动服务后，微信消息将通过 OneBot 推送给已连接的客户端。' }}</p></div>
      </div>
      <div class="service-controls">
        <button v-if="!status.running" class="btn" :disabled="!canStart" @click="sendControl('start')"><AppIcon name="Play" :size="15" />启动服务</button>
        <template v-else><button class="btn btn-outline" :disabled="!canResume && !canPause" @click="sendControl(status.paused ? 'resume' : 'pause')"><AppIcon :name="status.paused ? 'Play' : 'Pause'" :size="15" />{{ status.paused ? '恢复' : '暂停' }}</button><button class="btn btn-outline-danger btn-sm" :disabled="!canStop" @click="sendControl('stop')"><AppIcon name="Square" :size="13" />停止</button></template>
        <button class="icon-btn restart-btn" title="重启桥接" aria-label="重启桥接" :disabled="busy" @click="sendControl('restart')"><AppIcon name="RotateCcw" :size="16" /></button>
      </div>
    </section>

    <section class="status-grid" aria-label="链路状态">
      <article class="card status-tile"><div class="tile-top"><span>微信收件源</span><AppIcon name="MessageCircle" :size="17" /></div><strong :class="inboundCard.cls">{{ inboundCard.text }}</strong><div class="tile-foot"><span class="mini-dot" :class="inboundCard.cls"></span>微信本地数据库</div></article>
      <article class="card status-tile"><div class="tile-top"><span>OneBot 服务</span><AppIcon name="Zap" :size="17" /></div><strong :class="obCard.cls">{{ obCard.text }}</strong><div class="tile-foot">端口 {{ status.ob?.port || '—' }}<span class="foot-sep">/</span>WebSocket</div></article>
      <article class="card status-tile"><div class="tile-top"><span>发送通道</span><AppIcon name="Send" :size="17" /></div><strong>{{ status.send_method === 'uia' ? 'UI 自动化' : status.send_method || '未配置' }}</strong><button class="text-action" aria-label="识别与绑定微信窗口" @click="openWinModal">{{ windowInfo?.title || '识别微信窗口' }}<AppIcon name="ChevronRight" :size="13" /></button></article>
      <article class="card status-tile"><div class="tile-top"><span>服务进程</span><AppIcon name="TerminalSquare" :size="17" /></div><strong class="mono">PID {{ status.pid ?? '—' }}</strong><div class="tile-foot">控制面板端口 {{ status.web_port ?? '—' }}</div></article>
    </section>

    <div class="dashboard-grid">
      <div class="main-column">
        <section class="card activity-card">
          <div class="section-head"><h3 class="card-title">今日消息</h3><span class="section-note">{{ fmt(status.counters.recv) }} 次接收</span></div>
          <div class="metric-grid"><div><strong>{{ fmt(status.counters.recv) }}</strong><span><i class="metric-dot received"></i>接收消息</span></div><div><strong>{{ fmt(status.counters.pushed) }}</strong><span><i class="metric-dot pushed"></i>已推送事件</span></div><div><strong>{{ fmt(status.counters.sent) }}</strong><span><i class="metric-dot sent"></i>发送消息</span></div></div>
          <div v-if="status.counters.dropped > 0" class="drop-note"><AppIcon name="CircleAlert" :size="14" />{{ fmt(status.counters.dropped) }} 条消息被丢弃</div>
        </section>
        <section class="card chart-card"><div class="section-head"><h3 class="card-title">24 小时消息量</h3><span class="section-note">按小时统计</span></div><HourlyBarChart :data="stats.hourly" /><div v-if="!stats.hourly?.length" class="chart-empty">暂无可展示的消息统计</div></section>
      </div>
      <aside class="side-column">
        <section class="card contacts-card"><div class="section-head"><h3 class="card-title">活跃联系人</h3><AppIcon name="MoreHorizontal" :size="17" /></div><div v-if="stats.top_contacts?.length" class="contacts"><div v-for="(c, i) in stats.top_contacts" :key="i" class="contact-row"><span class="rank">{{ String(i + 1).padStart(2, '0') }}</span><span class="cname">{{ c.contact }}</span><span class="ccount">{{ fmt(c.count) }}</span></div></div><div v-else class="empty"><AppIcon name="MessagesSquare" :size="24" /><span>开始对话后，活跃联系人会显示在这里。</span></div></section>
      </aside>
    </div>

    <Modal :open="winModal" title="识别微信窗口" @close="winModal = false">
      <div class="auto-window"><div><strong>自动选择窗口</strong><p>由发送器自动识别微信窗口</p></div><button class="btn btn-sm" :class="isAutoActive ? '' : 'btn-outline'" :disabled="winAction === 0" @click="selectWin(0)">{{ isAutoActive ? '当前生效' : '设为自动' }}</button></div>
      <div v-if="winLoading" class="empty">正在识别窗口…</div><div v-else-if="!windows.length" class="empty">未找到微信窗口</div>
      <div v-else class="win-list"><div v-for="w in windows" :key="w.hwnd" class="win-row"><div class="win-info"><strong>{{ w.title || '微信' }}<span v-if="w.current" class="badge badge-green">当前绑定</span></strong><p>{{ w.process || 'Weixin.exe' }} · PID {{ w.pid ?? '—' }}</p><span class="mono">句柄 {{ hexHwnd(w.hwnd) }} · {{ w.iconic ? '最小化' : '正常' }} · 启动 {{ formatClock(w.start_time) }}</span></div><div class="win-actions"><button class="btn btn-ghost btn-sm" @click="flashWin(w.hwnd)"><AppIcon name="Zap" :size="13" />闪烁</button><button class="btn btn-sm" :disabled="winAction === w.hwnd" @click="selectWin(w.hwnd)">绑定</button></div></div></div>
      <p class="hint modal-hint">多开微信时请绑定机器人账号所在的窗口。微信重启后窗口句柄会变化，需要重新绑定。</p>
    </Modal>
    <ConfirmDialog
      :open="restartDialog"
      title="重启桥接服务"
      message="重启会中断当前连接，OneBot 客户端需要重新连入。确定现在重启吗？"
      confirm-text="重启"
      :danger="true"
      @cancel="restartDialog = false"
      @confirm="restartDialog = false; applyControl('restart')"
    />
  </div>
</template>

<style scoped>
.dashboard { display:flex; flex-direction:column; gap:18px; }
.version-label { color:var(--muted); font-size:11px; white-space:nowrap; }
.update-note { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:11px 14px; border:1px solid var(--border); border-radius:10px; background:var(--card); font-size:12px; }
.update-note span, .update-note a { display:flex; align-items:center; gap:8px; }
.service-panel { display:flex; justify-content:space-between; align-items:center; gap:20px; padding:22px; }
.service-main { display:flex; align-items:center; gap:15px; min-width:0; }
.service-icon { display:grid; place-items:center; width:48px; height:48px; flex-shrink:0; border-radius:12px; color:var(--muted); background:var(--inset); }
.service-icon.online { color:var(--primary-deep); background:var(--primary-soft); }
.service-title { display:flex; align-items:center; gap:10px; }
.service-title h3 { font-size:16px; font-weight:600; color:var(--text-strong); }
.service-copy p { margin-top:4px; font-size:11px; color:var(--muted); }
.service-controls { display:flex; align-items:center; gap:9px; flex-shrink:0; }
.restart-btn { border:1px solid var(--border); height:36px; width:36px; }
.status-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
.status-tile { padding:17px 18px; box-shadow:none; }
.tile-top { display:flex; align-items:center; justify-content:space-between; gap:10px; color:var(--muted); font-size:11px; }
.tile-top .app-icon { color:var(--faint); }
.status-tile > strong { display:block; margin-top:10px; color:var(--text-strong); font-size:18px; font-weight:600; letter-spacing:-.02em; }
.status-tile > strong.green { color:var(--success); }
.status-tile > strong.gray { color:var(--text-strong); }
.status-tile > strong.amber { color:var(--warning); }
.tile-foot, .text-action { display:flex; align-items:center; gap:5px; margin-top:6px; font-size:10px; color:var(--muted); }
.text-action { min-height:24px; padding:0; }
.foot-sep { margin-inline:3px; color:var(--border-strong); }
.mini-dot { width:5px; height:5px; border-radius:50%; background:var(--muted); }
.mini-dot.green { background:var(--primary); }
.text-action { padding:0; border:0; background:none; cursor:pointer; text-align:left; }
.text-action:hover { color:var(--primary-deep); }
.dashboard-grid { display:grid; grid-template-columns:minmax(0,1.75fr) minmax(260px,1fr); gap:18px; align-items:start; }
.main-column, .side-column { display:flex; flex-direction:column; gap:18px; min-width:0; }
.activity-card, .chart-card, .contacts-card { box-shadow:none; }
.metric-grid { display:grid; grid-template-columns:repeat(3,1fr); }
.metric-grid > div { padding:4px 18px; border-right:1px solid var(--border); }
.metric-grid > div:first-child { padding-left:0; }
.metric-grid > div:last-child { border:0; }
.metric-grid strong { display:block; font-size:29px; font-weight:600; letter-spacing:-.04em; color:var(--text-strong); font-variant-numeric:tabular-nums; }
.metric-grid span { display:flex; align-items:center; gap:6px; margin-top:5px; color:var(--muted); font-size:10px; white-space:nowrap; }
.metric-dot { width:5px; height:5px; border-radius:50%; background:var(--primary); }
.metric-dot.pushed { background:var(--muted); }
.metric-dot.sent { background:var(--text); }
.drop-note { display:flex; align-items:center; gap:5px; margin-top:10px; font-size:11px; color:var(--warning); }
.chart-card { padding-bottom:14px; }
.chart-empty { text-align:center; color:var(--muted); font-size:11px; margin-top:-20px; padding-bottom:12px; }
.contacts { display:flex; flex-direction:column; }
.contact-row { display:flex; align-items:center; gap:12px; padding:10px 0; border-bottom:1px solid var(--border); }
.contact-row:last-child { border-bottom:0; }
.rank { font-family:var(--mono); color:var(--faint); font-size:10px; }
.cname { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.ccount { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
.contacts-card .empty { min-height:130px; font-size:11px; padding-inline:10px; }
.learning-card { box-shadow:none; }
.learning-desc { margin:4px 0 0; }
.learning-actions { display:flex; align-items:center; gap:9px; }
.learning-progress { display:flex; align-items:center; gap:15px; padding-bottom:18px; border-bottom:1px solid var(--border); }
.learning-progress .progress-track { flex:1; }
.learning-progress > span { font-size:10px; color:var(--muted); }
.learning-detail { display:grid; grid-template-columns:1.5fr 1fr; gap:24px; margin-top:17px; }
.learning-detail h4 { margin-bottom:8px; font-size:11px; font-weight:500; color:var(--muted); }
.guide-row { display:flex; align-items:center; gap:10px; font-size:12px; }
.guide-row strong { font-weight:500; }
.last-learn { color:var(--text-strong); font-size:12px; }
.chips { display:flex; gap:6px; flex-wrap:wrap; margin-top:15px; }
.samples { margin-top:14px; font-size:11px; }
.samples summary { color:var(--muted); cursor:pointer; }
.sample-row { padding:7px 0; border-bottom:1px solid var(--border); }
.sample-row strong { margin-right:8px; }
.auto-window { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:14px; background:var(--inset); border-radius:9px; }
.auto-window strong { font-size:12px; }
.auto-window p { color:var(--muted); font-size:11px; margin-top:3px; }
.win-list { margin-top:12px; }
.win-row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 0; border-bottom:1px solid var(--border); }
.win-info { min-width:0; }
.win-info strong { display:flex; align-items:center; gap:7px; }
.win-info p, .win-info > span { font-size:10px; color:var(--muted); margin-top:4px; }
.win-actions { display:flex; gap:6px; flex-shrink:0; }
.modal-hint { margin-top:16px; }
@media(min-width:1500px) { .status-tile { padding:20px 22px; } }
@media(max-width:1150px) { .status-grid { grid-template-columns:repeat(2,1fr); } .dashboard-grid { grid-template-columns:1fr; } .side-column { display:grid; grid-template-columns:1fr 1fr; align-items:start; } }
@media(max-width:767px) {
  .dashboard { gap:14px; }
  .version-label { display:none; }
  .service-panel { align-items:flex-start; flex-direction:column; gap:17px; padding:17px; }
  .service-controls { width:100%; }
  .service-controls .btn:first-child { flex:1; }
  .status-grid { gap:10px; }
  .status-tile { padding:14px; }
  .status-tile > strong { font-size:15px; }
  .tile-top { font-size:10px; }
  .tile-foot, .text-action { font-size:9px; }
  .side-column { display:flex; }
  .metric-grid > div { padding-inline:10px; }
  .metric-grid strong { font-size:25px; }
  .metric-grid span { font-size:9px; white-space:normal; }
  .learning-detail { grid-template-columns:1fr; gap:18px; }
  .learning-progress { align-items:flex-start; flex-direction:column; gap:9px; }
  .learning-progress .progress-track { width:100%; }
  .learning-actions { gap:7px; }
  .update-note { align-items:flex-start; }
  .win-row { align-items:flex-start; flex-direction:column; }
}
.update-card { display:flex; flex-direction:column; gap:10px; }
.update-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.update-head .card-title { margin:0 0 2px; }
.update-msg { margin:0; font-size:12.5px; color:var(--green, #1f9d55); }
.update-msg.bad { color:var(--danger); }
.update-line { display:flex; flex-wrap:wrap; align-items:center; gap:10px; font-size:12.5px; }
.update-line span, .update-line a { display:flex; align-items:center; gap:6px; }
.update-line.pending { padding-top:8px; border-top:1px solid var(--border); }
</style>
