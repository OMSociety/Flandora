<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { get, post } from '../api'
import { status, refreshStatus, toast } from '../store'
import { formatUptime, fmt, modeLabel, hexHwnd, formatClock } from '../utils/format'
import HourlyBarChart from '../components/HourlyBarChart.vue'
import Modal from '../components/Modal.vue'

const stats = ref({ counters: {}, hourly: [], top_contacts: [] })
const busy = ref(false)

// ===== 微信窗口识别 =====
const winModal = ref(false)
const windows = ref([])
const winLoading = ref(false)
const winAction = ref(null) // 当前处理的 hwnd，用于按钮禁用

const windowInfo = computed(() => status.window)
const isAutoActive = computed(() => !status.window_locked)

async function openWinModal() {
  winModal.value = true
  await loadWindows()
}
async function loadWindows() {
  winLoading.value = true
  const res = await get('/api/windows')
  winLoading.value = false
  if (res.ok && res.data) {
    windows.value = res.data.windows || []
  } else {
    toast('识别窗口失败', 'error')
  }
}
async function flashWin(hwnd) {
  const res = await post('/api/windows/flash', { hwnd })
  if (res.ok && res.data && res.data.ok !== false) {
    toast('请查看任务栏闪烁的微信图标', 'info')
  } else {
    toast('闪烁失败', 'error')
  }
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
  } else {
    toast('绑定失败：' + ((res.data && res.data.error) || '未知错误'), 'error')
  }
}

// ===== 表情学习 =====
const emoji = ref({
  enabled: false, threshold: 0, samples_total: 0, pending: 0, running: false,
  top_codes: [], guide: [], last_distill_ts: 0, last_push_msg: '', recent_samples: [],
})
let pollTimer = null
let pollCancelled = false

const emojiBadge = computed(() => {
  if (!emoji.value.enabled) return { cls: 'badge-gray', text: '已关闭' }
  if (emoji.value.running) return { cls: 'badge-amber', text: '学习中…' }
  return { cls: 'badge-green', text: '观察中' }
})
const emojiProgress = computed(() => {
  const t = Number(emoji.value.threshold) || 0
  const p = Number(emoji.value.pending) || 0
  const pct = t > 0 ? Math.min(100, Math.round((p / t) * 100)) : 0
  const remaining = t > 0 ? Math.max(0, t - p) : 0
  return { pct, remaining, hasThreshold: t > 0 }
})
const topCodes = computed(() => (emoji.value.top_codes || []).slice(0, 10))
const guideList = computed(() => emoji.value.guide || [])
const lastDistill = computed(() => {
  const ts = Number(emoji.value.last_distill_ts) || 0
  return ts > 0 ? formatClock(ts) : '从未'
})
const recentSamples = computed(() => emoji.value.recent_samples || [])

function clearPollTimer() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

async function loadEmoji() {
  const res = await get('/api/emoji-learning')
  if (res.ok && res.data && res.data.ok !== false) {
    emoji.value = Object.assign(
      {
        enabled: false, threshold: 0, samples_total: 0, pending: 0, running: false,
        top_codes: [], guide: [], last_distill_ts: 0, last_push_msg: '', recent_samples: [],
      },
      res.data
    )
  } else {
    toast('加载表情学习数据失败', 'error')
  }
}

async function runDistill() {
  if (emoji.value.running) return
  const res = await post('/api/emoji-learning/run')
  if (res.ok && res.data && res.data.ok !== false) {
    toast(res.data.msg || '已开始学习', 'success')
    startPolling()
  } else {
    toast((res.data && res.data.error) || '启动失败', 'error')
    await loadEmoji()
  }
}

function startPolling() {
  pollCancelled = false
  clearPollTimer()
  const started = Date.now()
  const tick = async () => {
    await loadEmoji()
    if (emoji.value.running) {
      if (Date.now() - started < 60000) {
        pollTimer = setTimeout(tick, 3000)
      } else if (emoji.value.last_push_msg) {
        toast(emoji.value.last_push_msg, 'info')
      }
    } else if (emoji.value.last_push_msg) {
      toast(emoji.value.last_push_msg, 'info')
    }
  }
  pollTimer = setTimeout(tick, 3000)
}

const modeOptions = [
  { value: 'mention', label: '仅@回复' },
  { value: 'all', label: '全部回复' },
  { value: 'batch', label: '批处理' },
]

const bridgeCard = computed(() => {
  if (!status.running) return { text: '未运行', cls: 'gray' }
  if (status.paused) return { text: '已暂停', cls: 'amber' }
  return { text: '运行中', cls: 'green' }
})

const obCard = computed(() => {
  if (status.ob && status.ob.connected) {
    return { text: `${fmt(status.ob.clients)} 客户端`, cls: 'green' }
  }
  return { text: '未连接', cls: 'gray' }
})

const weflowCard = computed(() => {
  if (status.weflow && status.weflow.connected) return { text: '已连接', cls: 'green' }
  return { text: '未连接', cls: 'gray' }
})

const canStart = computed(() => !status.running && !busy.value)
const canStop = computed(() => status.running && !busy.value)
const canPause = computed(() => status.running && !status.paused && !busy.value)
const canResume = computed(() => status.running && status.paused && !busy.value)

async function sendControl(action) {
  if (action === 'restart' && !confirm('确定要重启桥接吗？')) return
  busy.value = true
  const res = await post('/api/control', { action })
  busy.value = false
  if (res.ok && res.data && res.data.ok !== false) {
    toast('操作成功', 'success')
  } else if (res.data && res.data.error) {
    toast('操作失败: ' + res.data.error, 'error')
  } else {
    toast('操作失败', 'error')
  }
  refreshStatus(true)
}

async function changeMode(mode) {
  if (mode === status.group_reply_mode) return
  const res = await post('/api/mode', { mode })
  if (res.ok && res.data) {
    toast(`群聊回复模式已切换为：${modeLabel(mode)}`, 'success')
    refreshStatus(true)
  } else {
    toast('切换模式失败', 'error')
  }
}

async function loadStats() {
  const res = await get('/api/stats')
  if (res.ok && res.data) {
    stats.value = {
      counters: res.data.counters || {},
      hourly: res.data.hourly || [],
      top_contacts: res.data.top_contacts || [],
    }
  }
}

onMounted(() => {
  loadStats()
  loadEmoji()
})

onBeforeUnmount(() => {
  pollCancelled = true
  clearPollTimer()
})
</script>

<template>
  <div class="dashboard">
    <!-- 状态卡片 -->
    <div class="cards">
      <div class="card stat-card">
        <div class="stat-label">桥接状态</div>
        <div class="stat-value" :class="bridgeCard.cls">{{ bridgeCard.text }}</div>
        <div class="stat-sub" v-if="status.version">Bridge {{ status.version }}</div>
      </div>
      <div class="card stat-card">
        <div class="stat-label">MaiBot 连接</div>
        <div class="stat-value" :class="obCard.cls">{{ obCard.text }}</div>
        <div class="stat-sub" v-if="status.ob && status.ob.port">端口 {{ status.ob.port }}</div>
      </div>
      <div class="card stat-card">
        <div class="stat-label">WeFlow 连接</div>
        <div class="stat-value" :class="weflowCard.cls">{{ weflowCard.text }}</div>
        <div class="stat-sub" v-if="status.weflow && status.weflow.base_url">{{ status.weflow.base_url }}</div>
      </div>
      <div class="card stat-card">
        <div class="stat-label">运行时长</div>
        <div class="stat-value">{{ status.running ? formatUptime(status.uptime_sec) : '--' }}</div>
        <div class="stat-sub">发送: {{ status.send_method || '--' }}</div>
      </div>
      <div class="card stat-card">
        <div class="stat-label">当前 PID</div>
        <div class="stat-value mono">{{ status.pid ?? '--' }}</div>
        <div class="stat-sub">端口 {{ status.web_port ?? '--' }}</div>
      </div>
    </div>

    <!-- 微信窗口 -->
    <div class="card win-card">
      <div class="card-title">微信窗口</div>
      <div class="win-body">
        <div class="win-ico">💬</div>
        <div class="win-main">
          <template v-if="windowInfo">
            <div class="win-title">{{ windowInfo.title || '微信' }}</div>
            <div class="win-sub">PID {{ windowInfo.pid ?? '--' }} · 句柄 {{ hexHwnd(windowInfo.hwnd) }}</div>
          </template>
          <template v-else>
            <div class="win-title notfound">未找到</div>
            <div class="win-sub">无法识别当前绑定的微信窗口</div>
          </template>
          <div v-if="!status.window_locked" class="win-auto">自动模式（多开时建议手动绑定）</div>
        </div>
        <button class="btn btn-outline btn-sm" @click="openWinModal">🔍 识别窗口</button>
      </div>
    </div>

    <!-- 表情学习 -->
    <div class="card emoji-card">
      <div class="card-title">
        <span>😊 表情学习</span>
        <span class="spacer"></span>
        <span class="badge" :class="emojiBadge.cls">{{ emojiBadge.text }}</span>
      </div>

      <!-- 学习进度 -->
      <div class="emoji-progress-row">
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: emojiProgress.pct + '%' }"></div>
        </div>
        <span v-if="emojiProgress.hasThreshold" class="progress-text">
          已收集 {{ fmt(emoji.pending) }} / {{ fmt(emoji.threshold) }}，再收集 {{ fmt(emojiProgress.remaining) }} 条样本自动学习
        </span>
        <span v-else class="progress-text">自动学习未开启</span>
      </div>

      <!-- 表情热榜 -->
      <div v-if="topCodes.length" class="emoji-sec">
        <div class="emoji-sec-title">表情热榜</div>
        <div class="chips">
          <span v-for="(c, i) in topCodes" :key="i" class="chip">{{ c.code }} ×{{ fmt(c.count) }}</span>
        </div>
      </div>

      <!-- 最近学到 -->
      <div class="emoji-sec">
        <div class="emoji-sec-title">最近学到</div>
        <div v-if="guideList.length" class="guide-list">
          <div v-for="(g, i) in guideList" :key="i" class="guide-row">
            <span class="guide-sit">{{ g.situation }}</span>
            <span class="guide-arrow">→</span>
            <span class="guide-style">{{ g.style }}</span>
          </div>
        </div>
        <div v-else class="empty-small">还没有学习成果</div>
      </div>

      <!-- 上次学习 -->
      <div class="emoji-sec">
        <div class="emoji-sec-title">上次学习</div>
        <div class="last-learn">
          <span class="ll-time">{{ lastDistill }}</span>
          <span v-if="emoji.last_push_msg" class="ll-msg">{{ emoji.last_push_msg }}</span>
        </div>
      </div>

      <!-- 操作 -->
      <div class="emoji-actions">
        <button class="btn btn-pink btn-sm" :disabled="emoji.running" @click="runDistill">🧠 立即学习</button>
      </div>

      <!-- 最近样本 -->
      <details v-if="recentSamples.length" class="emoji-samples">
        <summary>最近样本（{{ recentSamples.length }}）</summary>
        <div class="sample-list">
          <div v-for="(s, i) in recentSamples" :key="i" class="sample-row">
            <span class="sample-sender">{{ s.sender || '未知' }}：</span>
            <span class="sample-text">{{ s.text }}</span>
          </div>
        </div>
      </details>
    </div>

    <!-- 控制按钮 -->
    <div class="card">
      <div class="card-title">控制</div>
      <div class="row">
        <button class="btn btn-pink" :disabled="!canStart" @click="sendControl('start')">▶ 启动</button>
        <button class="btn btn-red" :disabled="!canStop" @click="sendControl('stop')">■ 停止</button>
        <button class="btn btn-amber" :disabled="busy" @click="sendControl('restart')">🔄 重启</button>
        <button class="btn btn-amber" :disabled="!canPause" @click="sendControl('pause')">⏸ 暂停</button>
        <button class="btn btn-green" :disabled="!canResume" @click="sendControl('resume')">▶ 恢复</button>
      </div>
    </div>

    <!-- 群聊回复模式 -->
    <div class="card">
      <div class="card-title">群聊回复模式</div>
      <div class="seg" role="tablist">
        <button
          v-for="opt in modeOptions"
          :key="opt.value"
          :class="{ active: status.group_reply_mode === opt.value }"
          @click="changeMode(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>
      <div class="hint">当前：{{ modeLabel(status.group_reply_mode) }}。仅@回复：只有提及机器人昵称时回复；全部回复：群内所有消息都参与；批处理：按缓冲批量回复。</div>
    </div>

    <!-- 今日统计 -->
    <div class="card">
      <div class="card-title">今日统计</div>
      <div class="grid-3">
        <div class="mini-stat in">
          <div class="mini-num">{{ fmt(status.counters.recv) }}</div>
          <div class="mini-label">接收</div>
        </div>
        <div class="mini-stat push">
          <div class="mini-num">{{ fmt(status.counters.pushed) }}</div>
          <div class="mini-label">推送</div>
        </div>
        <div class="mini-stat out">
          <div class="mini-num">{{ fmt(status.counters.sent) }}</div>
          <div class="mini-label">发送</div>
        </div>
      </div>
      <div v-if="status.counters.dropped > 0" class="hint warn">⚠️ 丢弃 {{ fmt(status.counters.dropped) }} 条</div>
    </div>

    <!-- 24 小时消息量 -->
    <div class="card">
      <div class="card-title">24 小时消息量</div>
      <HourlyBarChart :data="stats.hourly" />
      <div v-if="!stats.hourly || !stats.hourly.length" class="empty">暂无统计数据</div>
    </div>

    <!-- 活跃联系人 -->
    <div class="card">
      <div class="card-title">Top 联系人</div>
      <div v-if="stats.top_contacts && stats.top_contacts.length" class="contacts">
        <div v-for="(c, i) in stats.top_contacts" :key="i" class="contact-row">
          <span class="rank">{{ i + 1 }}</span>
          <span class="cname">{{ c.contact }}</span>
          <span class="ccount">{{ fmt(c.count) }} 条</span>
        </div>
      </div>
      <div v-else class="empty">暂无联系人记录</div>
    </div>

    <!-- 微信窗口识别 模态框 -->
    <Modal :open="winModal" title="识别微信窗口" @close="winModal = false">
      <div class="win-auto-row">
        <div class="auto-info">
          <span class="auto-dot" :class="isAutoActive ? 'on' : ''"></span>
          <div>
            <div class="auto-title">自动选择</div>
            <div class="auto-sub">
              由发送器自动选择微信窗口
              <span v-if="isAutoActive">（当前生效中）</span>
            </div>
          </div>
        </div>
        <button
          class="btn btn-sm"
          :class="isAutoActive ? 'btn-pink' : 'btn-ghost'"
          :disabled="winAction === 0"
          @click="selectWin(0)"
        >
          设为自动
        </button>
      </div>

      <div v-if="winLoading" class="empty">正在识别窗口...</div>
      <div v-else-if="!windows.length" class="empty">未找到微信窗口</div>
      <div v-else class="win-list">
        <div v-for="w in windows" :key="w.hwnd" class="win-row">
          <div class="win-info">
            <div class="win-title">
              {{ w.title || '微信' }}
              <span v-if="w.current" class="badge badge-pink">当前绑定</span>
              <span v-if="w.saved" class="badge badge-amber">已保存</span>
            </div>
            <div class="win-meta">
              <span>{{ w.process || 'Weixin.exe' }} · PID {{ w.pid ?? '--' }}</span>
              <span class="mono">句柄 {{ hexHwnd(w.hwnd) }}</span>
              <span :class="w.iconic ? 'st-min' : 'st-norm'">{{ w.iconic ? '最小化' : '正常' }}</span>
              <span>启动 {{ formatClock(w.start_time) }}</span>
            </div>
          </div>
          <div class="win-actions">
            <button class="btn btn-ghost btn-sm" @click="flashWin(w.hwnd)">⚡ 闪烁</button>
            <button
              class="btn btn-pink btn-sm"
              :disabled="winAction === w.hwnd"
              @click="selectWin(w.hwnd)"
            >
              绑定此窗口
            </button>
          </div>
        </div>
      </div>

      <div class="win-hint">多开微信时请绑定机器人账号所在的窗口。微信重启后窗口句柄会变化，需要重新绑定。</div>
    </Modal>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
}
.stat-card {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
}
.stat-label {
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
}
.stat-value {
  font-size: 20px;
  font-weight: 700;
}
.stat-value.green { color: var(--green); }
.stat-value.amber { color: var(--amber); }
.stat-value.gray { color: var(--gray); }
.stat-sub {
  font-size: 11px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted);
}
.hint.warn { color: var(--amber); }
.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.mini-stat {
  border-radius: var(--radius);
  padding: 14px;
  text-align: center;
  border: 1px solid var(--border);
}
.mini-stat.in { background: #fce4ec; }
.mini-stat.push { background: #fdf2f5; }
.mini-stat.out { background: #e3f2fd; }
.mini-num {
  font-size: 26px;
  font-weight: 800;
  color: var(--text-strong);
}
.mini-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}
.contacts {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.contact-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 13px;
}
.contact-row:nth-child(odd) { background: var(--pink-tint-2); }
.rank {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--pink-tint);
  color: var(--pink-deep);
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.cname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.ccount {
  color: var(--muted);
  font-size: 12px;
  flex-shrink: 0;
}

/* ===== 微信窗口卡片 ===== */
.win-card {
  display: flex;
  flex-direction: column;
}
.win-body {
  display: flex;
  align-items: center;
  gap: 14px;
}
.win-ico {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--pink-tint);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}
.win-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.win-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.win-title.notfound {
  color: var(--amber);
}
.win-sub {
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.win-auto {
  font-size: 11px;
  color: var(--faint);
}

/* ===== 模态框：窗口列表 ===== */
.win-auto-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--pink-tint-2);
  margin-bottom: 12px;
}
.auto-info {
  display: flex;
  align-items: center;
  gap: 10px;
}
.auto-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--gray);
  flex-shrink: 0;
}
.auto-dot.on {
  background: var(--green);
  box-shadow: 0 0 0 3px rgba(102, 187, 106, 0.2);
}
.auto-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-strong);
}
.auto-sub {
  font-size: 11px;
  color: var(--muted);
}
.win-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.win-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: border-color 0.18s, box-shadow 0.18s;
}
.win-row:hover {
  border-color: var(--pink-soft);
  box-shadow: var(--shadow);
}
.win-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.win-info .win-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.win-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--muted);
  flex-wrap: wrap;
}
.st-min {
  color: var(--amber);
}
.st-norm {
  color: var(--green);
}
.win-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.win-hint {
  margin-top: 14px;
  font-size: 11px;
  color: var(--faint);
  line-height: 1.6;
}

/* ===== 表情学习卡片 ===== */
.emoji-card {
  display: flex;
  flex-direction: column;
}
.emoji-card .card-title {
  justify-content: space-between;
}
.emoji-progress-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.progress-track {
  flex: 1;
  min-width: 140px;
  height: 8px;
  border-radius: 8px;
  background: var(--pink-tint-2);
  border: 1px solid var(--border);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #f48fb1, var(--pink));
  border-radius: 8px;
  transition: width 0.4s ease;
}
.progress-text {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.emoji-sec {
  margin-bottom: 14px;
}
.emoji-sec:last-of-type {
  margin-bottom: 0;
}
.emoji-sec-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  margin-bottom: 7px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.guide-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.guide-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--pink-tint-2);
}
.guide-sit {
  color: var(--text);
  font-weight: 500;
}
.guide-arrow {
  color: var(--pink);
}
.guide-style {
  color: var(--text-strong);
  font-weight: 600;
}
.empty-small {
  color: var(--faint);
  font-size: 12px;
  padding: 6px 0;
}
.last-learn {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.ll-time {
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--text-strong);
}
.ll-msg {
  font-size: 11px;
  color: var(--faint);
}
.emoji-actions {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.emoji-samples {
  margin-top: 12px;
  font-size: 12px;
}
.emoji-samples summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 6px;
}
.sample-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sample-row {
  padding: 5px 8px;
  border-radius: 8px;
  background: var(--pink-tint-2);
  font-size: 12px;
  word-break: break-word;
}
.sample-sender {
  color: var(--pink-deep);
  font-weight: 600;
}
.sample-text {
  color: var(--text);
}
</style>
