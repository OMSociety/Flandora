<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { get, post } from '../api'
import { toast } from '../store'
import { useDesktopShot } from '../useDesktopShot'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
const wiz = ref(null)
const busy = ref(false)
const accepted = ref(false)
// 勾选是本地意图：轮询不能每 2 秒把它覆盖回后端事实，否则用户来不及点确认就被清空。
let acceptedTouched = false
function onAcceptedChange() { acceptedTouched = true }
const manual = ref('')
const showAdvanced = ref(false)
const autoRefresh = ref(true)
const resetDialog = ref(false)
const stopDialog = ref(false)
const { imgUrl, lastError: shotError, shotAt, natural, refresh: refreshShot, onImgLoad, click, clear } = useDesktopShot()
let timer = null
let refreshing = false
function step(name) { return wiz.value?.steps?.[name] || { state: 'idle', message: '', log: [] } }
const disclaimer = computed(() => step('disclaimer'))
const dbkey = computed(() => step('dbkey'))
const imagekey = computed(() => step('imagekey'))
const verify = computed(() => step('verify'))
const current = computed(() => wiz.value?.current || 'disclaimer')
const dbKey = computed(() => wiz.value?.db_key || {})
const imageKey = computed(() => wiz.value?.image_key || {})
const manualRva = computed(() => wiz.value?.manual_rva || 0)
const keytoolRunning = computed(() => !!wiz.value?.keytool_running)
const imagekeyRunning = computed(() => !!wiz.value?.imagekey_running)
const needLive = computed(() => current.value === 'dbkey' || current.value === 'imagekey')
const done = computed(() => current.value === 'done')
// 扫描一轮可能就要数秒，若只靠 2 秒轮询返回的 started_at 计算，秒数会跳着走；
// 用本地时钟每秒自增，再与后端起点对齐，才能给出平滑的已等待时长。
const SCAN_LIMIT_SECONDS = 600
const scanLimitMin = SCAN_LIMIT_SECONDS / 60
const nowTick = ref(Date.now())
let tickTimer = null
const scanStartedAt = computed(() => {
  const started = Number(imagekey.value.started_at) || 0
  if (!imagekeyRunning.value) return 0
  return started > 0 ? started * 1000 : 0
})
const scanElapsedText = computed(() => {
  if (!imagekeyRunning.value) return '00:00'
  if (!scanStartedAt.value) return '刚刚开始'
  const seconds = Math.max(0, Math.floor((nowTick.value - scanStartedAt.value) / 1000))
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0')
  const ss = String(seconds % 60).padStart(2, '0')
  return `已等待 ${mm}:${ss}`
})
const currentIndex = computed(() => ({ disclaimer: 0, dbkey: 1, imagekey: 2, verify: 3, done: 4 }[current.value] ?? 0))
const steps = computed(() => [
  { name: '免责声明', detail: '使用边界与授权', state: disclaimer.value.state },
  { name: '数据库口令', detail: '读取本地消息', state: dbkey.value.state },
  { name: '图片密钥', detail: '解码聊天图片', state: imagekey.value.state },
  { name: '校验完成', detail: '验证数据库访问', state: verify.value.state },
])
const chipClass = (state) => ['step-status', state]
const chipText = (state) => ({ idle: '待开始', running: '进行中', ok: '已完成', failed: '未通过' }[state] || state)
function shortWxid(id) { return !id ? '未知' : id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-3)}` : id }
async function loadStatus() {
  const res = await get('/api/wizard/status', true)
  if (!res.ok || !res.data?.ok) return
  wiz.value = res.data
  // 只在用户还没动过勾选时同步服务端状态；确认成功后重置，改由后端事实驱动。
  if (res.data.disclaimer_accepted) {
    accepted.value = true
    acceptedTouched = false
  } else if (!acceptedTouched) {
    accepted.value = false
  }
}
async function call(path, body, okMsg) {
  busy.value = true
  const res = await post(path, body || {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { if (okMsg) toast(okMsg, 'success'); await loadStatus(); return true }
  toast(res.data?.error || `请求失败 (${res.status})`, 'error')
  await loadStatus()
  return false
}
const accept = () => call('/api/wizard/disclaimer', {}, '免责声明已确认')
const startDbkey = () => call('/api/wizard/dbkey/start', {}, '取钥器已启动，请在微信里重新登录')
const cancelDbkey = () => call('/api/wizard/dbkey/cancel')
const startImagekey = () => call('/api/wizard/imagekey/start', {}, '已开始扫描，请点开一张没看过的图片')
const cancelImagekey = () => call('/api/wizard/imagekey/cancel')
const runVerify = () => call('/api/wizard/verify')
async function resetWizard() {
  resetDialog.value = false
  await call('/api/wizard/reset', {}, '已回到第一步')
}
async function saveManual() {
  if (!manual.value.trim()) { toast('请先填入微信数据库口令', 'error'); return }
  if (await call('/api/wizard/dbkey/manual', { passphrase: manual.value })) manual.value = ''
}
async function startWeChat() {
  busy.value = true
  const res = await post('/api/desktop/start', {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { await loadStatus(); await refreshShot() }
  else toast(res.data?.error || '启动失败', 'error')
}
async function stopWeChat() {
  stopDialog.value = false
  busy.value = true
  const res = await post('/api/desktop/stop', {}, true)
  busy.value = false
  if (res.ok && res.data?.ok) { clear(); await loadStatus() }
  else toast(res.data?.error || '停止失败', 'error')
}
async function onClickShot(event) {
  const error = await click(event)
  if (error) toast(error, 'error')
  setTimeout(refreshShot, 300)
}
onMounted(async () => {
  await loadStatus()
  await refreshShot()
  tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000)
  timer = setInterval(async () => {
    if (!autoRefresh.value || refreshing || document.hidden) return
    refreshing = true
    try { await loadStatus(); if (needLive.value) await refreshShot() }
    catch (error) { toast('向导状态刷新失败：' + (error?.message || error), 'error') }
    finally { refreshing = false }
  }, 2000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer); if (tickTimer) clearInterval(tickTimer) })
</script>

<template>
  <div class="wiz-page">
    <div class="page-intro"><div><h2>四步，打通微信消息通道。</h2><p>获取数据库口令与图片密钥，让桥接安全读取你的本地消息。</p></div><span class="local-only"><AppIcon name="ShieldCheck" :size="15" />密钥仅保存在本机</span></div>
    <ol class="wizard-steps" aria-label="取钥进度"><li v-for="(item, i) in steps" :key="item.name" :class="{ current: currentIndex === i, complete: item.state === 'ok' || currentIndex > i }"><span class="step-marker"><AppIcon v-if="item.state === 'ok' || currentIndex > i" name="Check" :size="13" /><template v-else>{{ i + 1 }}</template></span><div><strong>{{ item.name }}</strong><small>{{ item.detail }}</small></div></li></ol>

    <div class="wizard-layout">
      <div class="wizard-main">
        <section class="card wizard-card" :class="{ inactive: currentIndex !== 0 && disclaimer.state !== 'ok' }">
          <div class="section-head"><div class="wizard-title"><span class="section-number">1</span><h3>确认使用边界</h3></div><span :class="chipClass(disclaimer.state)">{{ chipText(disclaimer.state) }}</span></div>
          <div class="terms"><p>本工具会读取并解密<strong>你自己这台电脑上</strong>已登录微信的本地数据库，用来把消息转给机器人。请确认：</p><ul><li>这是<strong>你自己的账号</strong>，或你已获得账号主人的明确同意；</li><li>你了解本工具不提供批量加人、自动营销等功能；</li><li>你自行承担使用它带来的账号风险与合规责任。</li></ul><p>口令使用 Windows DPAPI 加密保存在本机，不能在其他机器或 Windows 用户下使用。</p></div>
          <div class="step-footer"><label class="chk"><input v-model="accepted" type="checkbox" :disabled="disclaimer.state === 'ok'" @change="onAcceptedChange" />我已阅读并同意上述内容</label><button class="btn" :disabled="busy || !accepted || disclaimer.state === 'ok'" @click="accept"><AppIcon :name="disclaimer.state === 'ok' ? 'Check' : 'ArrowRight'" :size="14" />{{ disclaimer.state === 'ok' ? '已确认' : '确认并继续' }}</button></div>
        </section>

        <section class="card wizard-card" :class="{ inactive: current === 'disclaimer' }">
          <div class="section-head"><div class="wizard-title"><span class="section-number">2</span><h3>获取数据库口令</h3></div><span :class="chipClass(dbkey.state)">{{ chipText(dbkey.state) }}</span></div>
          <p class="step-description">口令只在微信重新登录时出现。启动取钥器后，在下方微信画面中重新登录一次。</p>
          <div class="key-status"><div><span>口令文件</span><strong :class="{ ready: dbKey.present }">{{ dbKey.present ? '已就绪' : '尚未获取' }}</strong></div><div v-if="dbKey.present"><span>对应账号</span><strong :class="{ warning: !dbKey.matches_current }">{{ shortWxid(dbKey.wxid) }}</strong></div><div v-if="dbKey.dll_version"><span>取钥时的微信</span><strong>{{ dbKey.dll_version }}</strong></div></div>
          <p v-if="dbKey.present && !dbKey.matches_current" class="account-warning">与当前登录的 {{ shortWxid(dbKey.current_wxid) }} 不一致，请使用机器人账号重新取钥。</p>
          <div v-if="dbKey.error" class="err">{{ dbKey.error }}</div>
          <div class="step-footer"><div class="step-actions"><button class="btn" :disabled="busy || disclaimer.state !== 'ok' || keytoolRunning" @click="startDbkey"><AppIcon name="KeyRound" :size="14" />{{ keytoolRunning ? '取钥器运行中…' : '开始取钥' }}</button><button v-if="keytoolRunning" class="btn btn-outline" :disabled="busy" @click="cancelDbkey">取消</button></div><button class="advanced-toggle" @click="showAdvanced = !showAdvanced">{{ showAdvanced ? '收起高级选项' : '高级选项' }}<AppIcon name="ChevronRight" :size="13" :class="{ rotated: showAdvanced }" /></button></div>
          <div v-if="showAdvanced" class="advanced"><h4>手动指定定位地址</h4><p>自动定位失败时，在配置中填写 <code>wxkey_manual_rva</code>（当前为 <code>{{ manualRva ? '0x' + manualRva.toString(16) : '0（自动）' }}</code>），保存并重启后重试。</p><h4>手动填入口令</h4><p>如果已有口令，可在此粘贴。校验当前账号数据库成功后才会保存。</p><div class="manual-row"><input v-model="manual" class="input" type="password" aria-label="手动数据库口令" placeholder="64 位十六进制，或 32 位口令原文" /><button class="btn btn-outline" :disabled="busy || !manual" @click="saveManual">校验并保存</button></div></div>
          <div v-if="dbkey.log?.length" class="step-log mono"><div v-for="(line, i) in dbkey.log" :key="i">{{ line }}</div></div><p v-else-if="dbkey.message" class="step-message">{{ dbkey.message }}</p>
        </section>

        <section class="card wizard-card" :class="{ inactive: current === 'disclaimer' || current === 'dbkey' }">
          <div class="section-head"><div class="wizard-title"><span class="section-number">3</span><h3>获取图片密钥</h3></div><span :class="chipClass(imagekey.state)">{{ chipText(imagekey.state) }}</span></div>
          <p class="step-description">密钥只在微信解码图片的那几秒存在。开始扫描后，请在下方微信画面里点开一张此前没看过的图片。</p>
          <div v-if="!imagekeyRunning && imagekey.state !== 'ok'" class="scan-guide">
            <p><strong>双击图片后会弹出独立窗口，画面里看不到变化，这是正常的。</strong>实况画面只截取微信主窗口，图片查看器是另一个窗口；扫描在后台进行，键一被解出来就会自动停止。</p>
            <p>如果点了没反应，多半是这张图已经缓存过：换一个没打开过的会话，或找一张更早、没翻过的图片再双击。</p>
          </div>
          <div v-if="imagekeyRunning" class="scan-live" role="status">
            <span class="scan-dot" aria-hidden="true"></span>
            <div class="scan-live-main"><strong>正在后台扫描内存</strong><p>{{ imagekey.message || '等待微信解码一张新图片…' }}</p></div>
            <div class="scan-live-meta"><span class="mono">{{ scanElapsedText }}</span><small>上限 {{ scanLimitMin }} 分钟</small></div>
          </div>
          <div class="key-status"><div><span>图片密钥</span><strong :class="{ ready: imageKey.present }">{{ imageKey.present ? '已就绪' : '尚未获取' }}</strong></div><div v-if="imageKey.present"><span>试解本地图片</span><strong :class="{ warning: imageKey.works === false }">{{ imageKey.works === true ? '通过' : imageKey.works === false ? '无法解码' : '未测试' }}</strong></div><div v-if="imageKey.xor_key !== null && imageKey.xor_key !== undefined"><span>XOR 密钥</span><strong class="mono">0x{{ Number(imageKey.xor_key).toString(16).padStart(2, '0') }}</strong></div></div>
          <div v-if="imageKey.error" class="err">{{ imageKey.error }}</div><p class="hint image-hint">没有图片密钥不影响文字消息，图片将以占位形式转发。</p>
          <div class="step-footer"><div class="step-actions"><button class="btn" :disabled="busy || !dbKey.present || imagekeyRunning" @click="startImagekey"><AppIcon name="Eye" :size="14" />{{ imagekeyRunning ? '正在扫描…' : '开始扫描' }}</button><button v-if="imagekeyRunning" class="btn btn-outline" :disabled="busy" @click="cancelImagekey">取消扫描</button></div></div>
          <div v-if="imagekey.log?.length" class="step-log mono"><div v-for="(line, i) in imagekey.log" :key="i">{{ line }}</div></div><p v-else-if="imagekey.message && !imagekeyRunning" class="step-message">{{ imagekey.message }}</p>
        </section>

        <section class="card wizard-card" :class="{ inactive: !dbKey.present }">
          <div class="section-head"><div class="wizard-title"><span class="section-number">4</span><h3>校验数据库访问</h3></div><span :class="chipClass(verify.state)">{{ chipText(verify.state) }}</span></div>
          <p class="step-description">使用当前口令验证账号数据库。索引库校验失败不影响正文消息收发。</p>
          <div class="step-footer"><div class="step-actions"><button class="btn" :disabled="busy || !dbKey.present || verify.state === 'running'" @click="runVerify"><AppIcon name="ShieldCheck" :size="14" />{{ verify.state === 'running' ? '正在校验…' : '开始校验' }}</button><button class="btn btn-ghost" :disabled="busy" @click="resetDialog = true"><AppIcon name="RotateCcw" :size="14" />重新开始</button></div></div>
          <p v-if="verify.message" class="step-message">{{ verify.message }}</p><div v-if="verify.log?.length" class="step-log mono"><div v-for="(line, i) in verify.log" :key="i">{{ line }}</div></div>
        </section>
        <div v-if="done" class="done-note"><AppIcon name="CircleCheck" :size="22" /><div><strong>取钥与校验已完成</strong><p>返回仪表盘确认收件状态，新消息会自动推送给已连接的 OneBot 客户端。</p></div></div>
      </div>

      <aside v-if="needLive" class="card wizard-live"><div class="section-head"><h3>微信画面</h3><span v-if="shotAt">{{ shotAt }}</span></div><p class="hint">在这里完成退出登录、重新登录或查看图片的操作。</p><div v-if="shotError" class="err">{{ shotError }}</div><div class="shot-wrap"><img v-if="imgUrl" :src="imgUrl" class="shot" alt="隔离桌面上的微信窗口，点击可转发操作" @load="onImgLoad" @click="onClickShot" /><div v-else class="shot-empty"><AppIcon name="Monitor" :size="27" />尚未获取画面</div></div><div class="live-actions"><button class="btn btn-outline btn-sm" :disabled="busy" @click="startWeChat">启动微信</button><button class="btn btn-ghost btn-sm" :disabled="busy" @click="stopDialog = true">结束微信</button><button class="icon-btn" aria-label="刷新微信画面" @click="refreshShot"><AppIcon name="RefreshCw" :size="15" /></button></div><label class="chk auto-live"><input v-model="autoRefresh" type="checkbox" />每 2 秒自动刷新</label><div class="live-foot">点击画面会将操作转发到微信<span v-if="natural.w">{{ natural.w }} × {{ natural.h }}</span></div></aside>
    </div>
    <ConfirmDialog
      :open="resetDialog"
      title="重新开始取钥向导"
      message="重新开始会清空向导的完成状态与步骤日志，已保存的数据库口令和图片密钥不受影响。确定重新开始吗？"
      confirm-text="重新开始"
      :danger="true"
      @cancel="resetDialog = false"
      @confirm="resetWizard"
    />
    <ConfirmDialog
      :open="stopDialog"
      title="结束隔离桌面上的微信"
      message="结束微信会中断正在进行的登录或取钥操作。确定结束吗？"
      confirm-text="结束微信"
      :danger="true"
      @cancel="stopDialog = false"
      @confirm="stopWeChat"
    />
  </div>
</template>

<style scoped>
.wiz-page { display:flex; flex-direction:column; gap:22px; }
.local-only { display:flex; align-items:center; gap:6px; color:var(--success); font-size:11px; white-space:nowrap; }
.wizard-steps { display:grid; grid-template-columns:repeat(4,1fr); padding:0 10px 22px; list-style:none; border-bottom:1px solid var(--border); }
.wizard-steps li { position:relative; display:flex; align-items:center; gap:10px; color:var(--muted); }
.wizard-steps li:not(:last-child)::after { content:''; position:absolute; z-index:0; left:40px; right:12px; top:15px; height:1px; background:var(--border-strong); }
.step-marker { position:relative; z-index:1; display:grid; place-items:center; width:30px; height:30px; flex-shrink:0; border:1px solid var(--border-strong); border-radius:50%; background:var(--canvas); font-size:11px; }
.wizard-steps li > div { position:relative; z-index:1; display:flex; flex-direction:column; padding-right:10px; background:var(--canvas); }
.wizard-steps strong { font-size:11px; font-weight:500; white-space:nowrap; }
.wizard-steps small { margin-top:2px; font-size:9px; white-space:nowrap; }
.wizard-steps li.current { color:var(--primary-deep); }
.wizard-steps li.current .step-marker { border-color:var(--primary); color:var(--primary-ink); background:var(--primary-strong); }
.wizard-steps li.complete .step-marker { border-color:var(--primary); background:var(--primary-soft); color:var(--primary-deep); }
.wizard-layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(260px,360px); gap:20px; align-items:start; }
.wizard-layout:not(:has(.wizard-live)) { grid-template-columns:minmax(0,860px); justify-content:center; }
.wizard-main { display:flex; flex-direction:column; gap:16px; }
.wizard-card { padding:22px; box-shadow:none; transition:opacity .2s; }
.wizard-card.inactive { opacity:.62; }
.wizard-title { display:flex; align-items:center; gap:10px; }
.wizard-title h3 { font-size:14px; font-weight:600; color:var(--text-strong); }
.section-number { display:grid; place-items:center; width:24px; height:24px; border-radius:7px; background:var(--primary-soft); color:var(--primary-deep); font-size:11px; font-weight:600; }
.step-status { padding:3px 8px; border-radius:5px; background:var(--inset); color:var(--muted); font-size:10px; }
.step-status.running { background:var(--warning-soft); color:var(--warning); }
.step-status.ok { background:var(--success-soft); color:var(--success); }
.step-status.failed { background:var(--danger-soft); color:var(--danger); }
.terms { font-size:12px; line-height:1.9; }
.terms strong { font-weight:600; color:var(--text-strong); }
.terms p:last-child { color:var(--muted); font-size:11px; }
.terms ul { margin:8px 0 12px; padding-left:20px; }
.step-footer { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:20px; padding-top:16px; border-top:1px solid var(--border); }
.step-actions { display:flex; gap:8px; flex-wrap:wrap; }
.step-description { font-size:12px; line-height:1.8; color:var(--muted); }
.key-status { display:flex; flex-wrap:wrap; gap:16px 28px; margin-top:17px; padding:13px 15px; background:var(--inset); border-radius:8px; }
.key-status > div { display:flex; flex-direction:column; gap:3px; }
.key-status span { font-size:10px; color:var(--muted); }
.key-status strong { font-size:11px; font-weight:500; }
.key-status strong.ready { color:var(--success); }
.key-status strong.warning, .account-warning { color:var(--warning); }
.account-warning { margin-top:8px; font-size:11px; }
.advanced-toggle { display:flex; align-items:center; gap:4px; min-height:24px; padding:0 4px; border:0; border-radius:4px; background:transparent; color:var(--muted); font-size:11px; cursor:pointer; }
.advanced-toggle:hover { color:var(--text-strong); }
.advanced-toggle .rotated { transform:rotate(90deg); }
.advanced { margin-top:16px; padding:15px; border:1px dashed var(--border-strong); border-radius:8px; }
.advanced h4 { font-size:11px; font-weight:500; }
.advanced h4:not(:first-child) { margin-top:16px; }
.advanced p { margin-top:5px; color:var(--muted); font-size:11px; line-height:1.7; }
.manual-row { display:flex; gap:9px; margin-top:10px; }
.step-log { margin-top:14px; max-height:220px; overflow:auto; padding:12px 14px; border-radius:8px; background:var(--code-bg); color:var(--code-fg); font-size:10px; line-height:1.8; }
.step-message { margin-top:12px; color:var(--muted); font-size:11px; }
.image-hint { margin-top:9px; }
.scan-guide { margin-top:14px; padding:13px 15px; border:1px solid var(--border); border-left:2px solid var(--primary); border-radius:8px; background:var(--inset); }
.scan-guide p { font-size:11px; line-height:1.85; color:var(--muted); }
.scan-guide p + p { margin-top:7px; }
.scan-guide strong { display:inline; font-weight:600; color:var(--text-strong); }
.scan-live { display:flex; align-items:center; gap:12px; margin-top:14px; padding:13px 15px; border:1px solid color-mix(in srgb,var(--primary) 35%,var(--border)); border-radius:8px; background:var(--primary-soft); }
.scan-dot { width:9px; height:9px; flex-shrink:0; border-radius:50%; background:var(--primary-strong); }
.scan-live-main { flex:1; min-width:0; }
.scan-live-main strong { display:block; font-size:12px; font-weight:600; color:var(--primary-deep); }
.scan-live-main p { margin-top:3px; font-size:11px; line-height:1.6; color:var(--text); overflow-wrap:anywhere; }
.scan-live-meta { display:flex; flex-direction:column; align-items:flex-end; gap:2px; flex-shrink:0; text-align:right; }
.scan-live-meta .mono { font-size:11px; color:var(--text-strong); font-variant-numeric:tabular-nums; }
.scan-live-meta small { font-size:9px; color:var(--muted); white-space:nowrap; }
@media(prefers-reduced-motion:no-preference) { .scan-live .scan-dot { animation:scan-pulse 1.6s ease-in-out infinite; } }
@keyframes scan-pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.done-note { display:flex; gap:12px; padding:18px; border:1px solid var(--primary); border-radius:10px; background:var(--primary-soft); color:var(--primary-deep); }
.done-note strong { font-size:13px; }
.done-note p { margin-top:4px; font-size:11px; }
.wizard-live { position:sticky; top:0; padding:16px; box-shadow:none; }
.wizard-live .section-head { margin-bottom:5px; }
.wizard-live h3 { font-size:12px; font-weight:600; }
.wizard-live .section-head > span { font-size:9px; color:var(--muted); }
.shot-wrap { display:flex; align-items:center; justify-content:center; min-height:240px; margin-top:14px; background:var(--inset); border-radius:8px; overflow:hidden; }
.shot { display:block; max-width:100%; max-height:50dvh; cursor:crosshair; }
.shot-empty { display:flex; flex-direction:column; align-items:center; gap:10px; min-height:230px; justify-content:center; color:var(--muted); font-size:11px; }
.live-actions { display:flex; align-items:center; gap:5px; margin-top:12px; }
.live-actions .icon-btn { margin-left:auto; }
.auto-live { margin-top:8px; font-size:10px; }
.live-foot { display:flex; justify-content:space-between; gap:8px; margin-top:10px; padding-top:10px; border-top:1px solid var(--border); color:var(--muted); font-size:9px; }
@media(max-width:1100px) { .wizard-layout { grid-template-columns:1fr; } .wizard-live { position:static; } .shot { max-height:65dvh; } .wizard-steps small { display:none; } }
@media(max-width:767px) {
  .local-only { display:none; }
  .wizard-steps { padding:0 0 18px; }
  .wizard-steps li { gap:5px; }
  .wizard-steps li:not(:last-child)::after { left:30px; right:3px; }
  .step-marker { width:25px; height:25px; font-size:10px; }
  .wizard-steps strong { font-size:9px; }
  .wizard-card { padding:17px; }
  .step-footer { align-items:flex-start; flex-wrap:wrap; }
  .step-footer .chk { font-size:11px; }
  .key-status { gap:14px 22px; }
  .scan-live { align-items:flex-start; flex-wrap:wrap; gap:8px; }
  .scan-live-meta { flex-direction:row; align-items:baseline; gap:8px; width:100%; padding-left:21px; }
  .manual-row { flex-direction:column; }
  .terms { font-size:11px; }
}
</style>
