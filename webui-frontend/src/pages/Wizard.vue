<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { get, post } from '../api'
import { toast } from '../store'
import { useDesktopShot } from '../useDesktopShot'

const wiz = ref(null)
const busy = ref(false)
const accepted = ref(false)
const manual = ref('')
const showAdvanced = ref(false)
const autoRefresh = ref(true)

const {
  imgUrl, lastError: shotError, shotAt, natural,
  refresh: refreshShot, onImgLoad, click, clear,
} = useDesktopShot()

let timer = null

function step(name) {
  const steps = (wiz.value && wiz.value.steps) || {}
  return steps[name] || { state: 'idle', message: '', log: [] }
}

const disclaimer = computed(() => step('disclaimer'))
const dbkey = computed(() => step('dbkey'))
const imagekey = computed(() => step('imagekey'))
const verify = computed(() => step('verify'))

const current = computed(() => (wiz.value && wiz.value.current) || 'disclaimer')
const dbKey = computed(() => (wiz.value && wiz.value.db_key) || {})
const imageKey = computed(() => (wiz.value && wiz.value.image_key) || {})
const manualRva = computed(() => (wiz.value && wiz.value.manual_rva) || 0)
const keytoolRunning = computed(() => !!(wiz.value && wiz.value.keytool_running))
const imagekeyRunning = computed(() => !!(wiz.value && wiz.value.imagekey_running))

/** 取微信口令与图片密钥时都得看着画面，这两步才需要实况。 */
const needLive = computed(() => current.value === 'dbkey' || current.value === 'imagekey')
const done = computed(() => current.value === 'done')

const chipClass = (state) => ({
  chip: true,
  'chip-idle': state === 'idle',
  'chip-run': state === 'running',
  'chip-ok': state === 'ok',
  'chip-bad': state === 'failed',
})

const chipText = (state) => ({
  idle: '待办', running: '进行中', ok: '已完成', failed: '未通过',
}[state] || state)

function shortWxid(id) {
  if (!id) return '未知'
  return id.length > 12 ? id.slice(0, 8) + '…' + id.slice(-3) : id
}

async function loadStatus() {
  const res = await get('/api/wizard/status', true)
  if (res.ok && res.data && res.data.ok) {
    wiz.value = res.data
    accepted.value = !!res.data.disclaimer_accepted
  }
}

async function call(path, body, okMsg) {
  busy.value = true
  const res = await post(path, body || {}, true)
  busy.value = false
  if (res.ok && res.data && res.data.ok) {
    if (okMsg) toast(okMsg, 'success')
    await loadStatus()
    return true
  }
  toast((res.data && res.data.error) || `请求失败 (${res.status})`, 'error')
  await loadStatus()
  return false
}

const accept = () => call('/api/wizard/disclaimer', {}, '免责声明已确认')
const startDbkey = () => call('/api/wizard/dbkey/start', {}, '取钥器已启动，请在微信里重新登录')
const cancelDbkey = () => call('/api/wizard/dbkey/cancel')
const startImagekey = () => call('/api/wizard/imagekey/start', {}, '已开始扫描，请点开一张没看过的图片')
const cancelImagekey = () => call('/api/wizard/imagekey/cancel')
const runVerify = () => call('/api/wizard/verify')
const resetWizard = () => call('/api/wizard/reset', {}, '已回到第一步')

async function saveManual() {
  if (!manual.value.trim()) {
    toast('请先填入微信数据库口令', 'error')
    return
  }
  const ok = await call('/api/wizard/dbkey/manual', { passphrase: manual.value })
  if (ok) manual.value = ''
}

async function startWeChat() {
  busy.value = true
  const res = await post('/api/desktop/start', {}, true)
  busy.value = false
  if (res.ok && res.data && res.data.ok) {
    await loadStatus()
    await refreshShot()
  } else {
    toast((res.data && res.data.error) || '启动失败', 'error')
  }
}

async function stopWeChat() {
  busy.value = true
  const res = await post('/api/desktop/stop', {}, true)
  busy.value = false
  if (res.ok && res.data && res.data.ok) {
    clear()
    await loadStatus()
  } else {
    toast((res.data && res.data.error) || '停止失败', 'error')
  }
}

async function onClickShot(e) {
  const err = await click(e)
  if (err) toast(err, 'error')
  setTimeout(refreshShot, 300)
}

onMounted(async () => {
  await loadStatus()
  await refreshShot()
  timer = setInterval(async () => {
    if (!autoRefresh.value) return
    await loadStatus()
    if (needLive.value) await refreshShot()
  }, 2000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="wiz-page">
    <div class="banner">
      这一步要把微信的两把钥匙取到手：<b>数据库口令</b>（读聊天记录用）与<b>图片密钥</b>
      （还原图片用）。两者的共同点是都只在特定瞬间存在内存里，所以必须由你配合做一次
      指定动作——跟着下面四步走即可。
    </div>

    <!-- 第一步：免责声明 -->
    <div class="card">
      <div class="card-title">
        <span class="num">1</span>免责声明
        <span :class="chipClass(disclaimer.state)">{{ chipText(disclaimer.state) }}</span>
      </div>
      <div class="terms">
        <p>本工具会读取并解密<b>你自己这台电脑上</b>已登录微信的本地数据库，用来把消息转给机器人。
          请确认：</p>
        <ul>
          <li>这是<b>你自己的账号</b>，或你已获得账号主人的明确同意；</li>
          <li>你了解本工具<b>不含</b>任何绕过平台规则、批量加人、自动营销之类的功能；</li>
          <li>你自行承担使用它带来的账号风险与合规责任。</li>
        </ul>
        <p class="muted">
          口令取出后用 Windows DPAPI 加密保存在本机，拷到别的机器无效；项目本身也不收集、
          不上传任何数据。详细说明见 README 的「取钥」一节。
        </p>
      </div>
      <label class="chk">
        <input type="checkbox" v-model="accepted" :disabled="disclaimer.state === 'ok'" />
        我已阅读并同意上述内容
      </label>
      <div class="btn-row">
        <button
          class="btn btn-pink"
          :disabled="busy || !accepted || disclaimer.state === 'ok'"
          @click="accept()"
        >
          {{ disclaimer.state === 'ok' ? '已确认' : '确认并继续' }}
        </button>
      </div>
    </div>

    <!-- 第二步：数据库口令 -->
    <div class="card" :class="{ dim: current === 'disclaimer' }">
      <div class="card-title">
        <span class="num">2</span>数据库口令
        <span :class="chipClass(dbkey.state)">{{ chipText(dbkey.state) }}</span>
      </div>

      <div class="kv">
        <div class="kv-item">
          <span class="kv-k">口令文件</span>
          <span class="kv-v">{{ dbKey.present ? '已就绪' : '还没有' }}</span>
        </div>
        <div class="kv-item" v-if="dbKey.present">
          <span class="kv-k">对应账号</span>
          <span class="kv-v" :class="{ warn: !dbKey.matches_current }">
            {{ shortWxid(dbKey.wxid) }}
            <template v-if="!dbKey.matches_current">
              （与当前登录的 {{ shortWxid(dbKey.current_wxid) }} 不一致）
            </template>
          </span>
        </div>
        <div class="kv-item" v-if="dbKey.dll_version">
          <span class="kv-k">取钥时的微信</span>
          <span class="kv-v">{{ dbKey.dll_version }}</span>
        </div>
      </div>

      <div v-if="dbKey.error" class="err">{{ dbKey.error }}</div>

      <div class="hint">
        口令<b>只在微信登录的那一瞬间</b>存在。点下面的按钮后，请在画面里把微信退出登录，
        再重新登录一次；登录完成会自动继续，全程大约一分钟。首次使用需要重启一次微信，
        这是正常现象。
      </div>

      <div class="btn-row">
        <button
          class="btn btn-pink"
          :disabled="busy || disclaimer.state !== 'ok' || keytoolRunning"
          @click="startDbkey()"
        >
          {{ keytoolRunning ? '取钥器运行中…' : '开始取钥' }}
        </button>
        <button class="btn" :disabled="busy || !keytoolRunning" @click="cancelDbkey()">
          取消
        </button>
        <button class="btn btn-ghost" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? '收起高级选项' : '高级选项' }}
        </button>
      </div>

      <div v-if="showAdvanced" class="advanced">
        <div class="adv-title">手动指定定位地址</div>
        <p class="muted">
          自动定位靠识别微信 DLL 里的调用形态，微信改版后可能失手。那时把地址填到配置的
          <code>wxkey_manual_rva</code> 里（当前的值为
          <code>{{ manualRva ? '0x' + manualRva.toString(16) : '0（自动）' }}</code>），保存后重启本程序再试。
          README 里有怎么找到这个地址的说明。
        </p>
        <div class="adv-title">手动填入口令</div>
        <p class="muted">
          如果你已经从别处拿到了口令，直接贴进来。会先拿当前账号的真库验一遍，验过了才存。
        </p>
        <div class="manual-row">
          <input
            class="input"
            type="password"
            v-model="manual"
            placeholder="64 位十六进制，或 32 位口令原文"
          />
          <button class="btn" :disabled="busy || !manual" @click="saveManual()">校验并保存</button>
        </div>
      </div>

      <div v-if="dbkey.log && dbkey.log.length" class="log">
        <div v-for="(line, i) in dbkey.log" :key="i">{{ line }}</div>
      </div>
      <div v-else-if="dbkey.message" class="step-msg">{{ dbkey.message }}</div>
    </div>

    <!-- 第三步：图片密钥 -->
    <div class="card" :class="{ dim: current === 'disclaimer' || current === 'dbkey' }">
      <div class="card-title">
        <span class="num">3</span>图片密钥
        <span :class="chipClass(imagekey.state)">{{ chipText(imagekey.state) }}</span>
      </div>

      <div class="kv">
        <div class="kv-item">
          <span class="kv-k">图片密钥</span>
          <span class="kv-v">{{ imageKey.present ? '已就绪' : '还没有' }}</span>
        </div>
        <div class="kv-item" v-if="imageKey.present">
          <span class="kv-k">试解本地图片</span>
          <span class="kv-v" :class="{ warn: imageKey.works === false }">
            {{ imageKey.works === true ? '通过' : imageKey.works === false ? '解不开' : '未试' }}
          </span>
        </div>
        <div class="kv-item" v-if="imageKey.xor_key !== null && imageKey.xor_key !== undefined">
          <span class="kv-k">XOR 密钥</span>
          <span class="kv-v">0x{{ Number(imageKey.xor_key).toString(16).padStart(2, '0') }}</span>
        </div>
      </div>

      <div v-if="imageKey.error" class="err">{{ imageKey.error }}</div>

      <div class="hint">
        图片密钥<b>只在微信解码图片的那几秒</b>在内存里。点下面的按钮后，请在画面里点开
        <b>一张之前没看过的图片</b>——最好先删掉 <code>cache</code> 目录里对应的缓存，
        这样微信才会真的去解码。没有图片密钥时，文字消息照常收发，图片会退化成占位符。
      </div>

      <div class="btn-row">
        <button
          class="btn btn-pink"
          :disabled="busy || !dbKey.present || imagekeyRunning"
          @click="startImagekey()"
        >
          {{ imagekeyRunning ? '扫描中…' : '开始扫描' }}
        </button>
        <button class="btn" :disabled="busy || !imagekeyRunning" @click="cancelImagekey()">
          取消
        </button>
      </div>

      <div v-if="imagekey.log && imagekey.log.length" class="log">
        <div v-for="(line, i) in imagekey.log" :key="i">{{ line }}</div>
      </div>
      <div v-else-if="imagekey.message" class="step-msg">{{ imagekey.message }}</div>
    </div>

    <!-- 第四步：校验 -->
    <div class="card" :class="{ dim: !dbKey.present }">
      <div class="card-title">
        <span class="num">4</span>校验
        <span :class="chipClass(verify.state)">{{ chipText(verify.state) }}</span>
      </div>
      <div class="hint">
        拿当前口令把账号下的每个数据库都过一遍页 1 的 HMAC。索引库（<code>FTS</code> 那几个）
        即使没通过也不影响正文收发。
      </div>
      <div class="btn-row">
        <button
          class="btn btn-pink"
          :disabled="busy || !dbKey.present || verify.state === 'running'"
          @click="runVerify()"
        >
          {{ verify.state === 'running' ? '校验中…' : '开始校验' }}
        </button>
        <button class="btn btn-ghost" :disabled="busy" @click="resetWizard()">重新开始</button>
      </div>
      <div v-if="verify.message" class="step-msg">{{ verify.message }}</div>
      <div v-if="verify.log && verify.log.length" class="log">
        <div v-for="(line, i) in verify.log" :key="i">{{ line }}</div>
      </div>
    </div>

    <!-- 实况画面：第二、三步要用 -->
    <div v-if="needLive" class="card">
      <div class="card-title">
        微信画面
        <span v-if="shotAt" class="shot-at">更新于 {{ shotAt }}</span>
      </div>

      <div v-if="shotError" class="err">{{ shotError }}</div>

      <div class="btn-row">
        <button class="btn" :disabled="busy" @click="startWeChat()">启动微信</button>
        <button class="btn" :disabled="busy" @click="stopWeChat()">结束微信</button>
        <label class="chk">
          <input type="checkbox" v-model="autoRefresh" />
          每 2 秒自动刷新
        </label>
        <button class="btn btn-ghost" @click="refreshShot()">立即刷新</button>
      </div>

      <div class="shot-wrap">
        <img
          v-if="imgUrl"
          :src="imgUrl"
          class="shot"
          alt="隔离桌面上的微信窗口画面"
          @load="onImgLoad"
          @click="onClickShot"
        />
        <div v-else class="shot-empty">
          还没有画面。先点「启动微信」。
        </div>
      </div>

      <div class="hint">
        画面上的点击会转发到微信上，与直接操作它等效。第二步在这里退出登录再重新登录；
        第三步在这里点开一张没看过的图片。
        <span v-if="natural.w">{{ natural.w }} × {{ natural.h }}</span>
      </div>
    </div>

    <div v-if="done" class="card ok-card">
      两步都走完了。回到「仪表盘」看看收件是否已经在跑；之后新消息会自动转给麦麦。
    </div>
  </div>
</template>

<style scoped>
.wiz-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.banner {
  background: #fff8e1;
  border: 1px solid var(--amber-soft);
  color: #b26a00;
  border-radius: var(--radius);
  padding: 12px 16px;
  font-size: 13px;
  line-height: 1.6;
}
.card.dim {
  opacity: 0.55;
}
.num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: 8px;
  border-radius: 50%;
  background: var(--pink-soft, #ffe4ec);
  color: var(--pink, #e0507a);
  font-size: 12px;
  font-weight: 700;
}
.chip {
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}
.chip-idle {
  background: #eceff1;
  color: #78909c;
}
.chip-run {
  background: #fff3e0;
  color: #e65100;
}
.chip-ok {
  background: #e8f5e9;
  color: #2e7d32;
}
.chip-bad {
  background: #ffebee;
  color: #c62828;
}
.terms {
  font-size: 13px;
  line-height: 1.75;
  color: var(--text);
}
.terms p {
  margin: 0 0 8px;
}
.terms ul {
  margin: 0 0 8px;
  padding-left: 20px;
}
.terms .muted,
.muted {
  color: var(--faint);
  font-size: 12px;
}
.kv {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 26px;
  margin-bottom: 12px;
}
.kv-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.kv-k {
  font-size: 12px;
  color: var(--faint);
}
.kv-v {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.kv-v.warn {
  color: #b26a00;
}
.chk {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  margin-top: 12px;
}
.btn-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
}
.hint {
  font-size: 12px;
  color: var(--faint);
  line-height: 1.7;
  margin-top: 12px;
}
.log {
  margin-top: 12px;
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 8px;
  padding: 10px 12px;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  max-height: 220px;
  overflow: auto;
}
.step-msg {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted);
}
.advanced {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
}
.adv-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 4px;
}
.advanced .adv-title:not(:first-child) {
  margin-top: 14px;
}
.manual-row {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}
.manual-row .input {
  flex: 1;
}
.err {
  margin-top: 10px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  color: #c62828;
  border-radius: var(--radius);
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.6;
}
.shot-at {
  font-size: 11px;
  color: var(--faint);
  font-weight: 400;
  margin-left: 8px;
}
.shot-wrap {
  display: flex;
  justify-content: center;
  padding: 12px 0;
}
.shot {
  max-width: 100%;
  max-height: 60vh;
  border-radius: 10px;
  border: 1px solid var(--border);
  cursor: crosshair;
  background: #1e1e1e;
}
.shot-empty {
  font-size: 13px;
  color: var(--faint);
  padding: 48px 0;
}
.ok-card {
  border-left: 3px solid #2e7d32;
  font-size: 13px;
  line-height: 1.7;
}
code {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}
</style>
