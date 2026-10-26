<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import ConfirmDialog from '../ConfirmDialog.vue'
import SectionFoot from './SectionFoot.vue'
import { useSettingsSave } from '../../composables/useSettingsSave'
import { get, post } from '../../api'
import { toast } from '../../store'

const props = defineProps({
  cfg: { type: Object, required: true },
  active: { type: Boolean, default: true },
})

// 占用多少、盘上还剩哪些文件，只有服务端数得出来，所以整块从 /api/storage 读。
const stats = ref(null)
const loadError = ref('')
const cleanMsg = ref('')
const busy = ref('')

async function loadStorage() {
  const res = await get('/api/storage')
  if (res.ok && res.data && res.data.ok !== false) {
    stats.value = res.data
    loadError.value = ''
  } else {
    loadError.value = res.data?.error || (res.status ? `HTTP ${res.status}` : '无法连接桥接服务')
  }
}
onMounted(loadStorage)
watch(() => props.active, (on) => { if (on) loadStorage() })

function fmtBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function fmtDay(seconds) {
  if (!seconds) return '—'
  const d = new Date(seconds * 1000)
  const pad = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 存档跨度按天说人话：超过两天就不必精确到分钟了
const archiveSpan = computed(() => {
  const m = stats.value?.messages
  if (!m || !m.rows || !m.oldest || !m.newest) return ''
  const days = Math.max(0, Math.round((m.newest - m.oldest) / 86400))
  if (days === 0) return '都集中在同一天'
  return `跨度 ${days} 天（${fmtDay(m.oldest)} 起）`
})

// ---------- 清理 ----------

const pendingClean = ref(null)
const cleaning = ref(false)

// 只有「删了会有代价」的两类才多问一句：清理解密缓存会让收件卡一下，
// 更新暂存里可能躺着已经下载好的更新包。图片缓存与历史日志删了没有代价，直接做。
const CLEAN_CARDS = {
  wx_cache: {
    title: '清理解密的微信数据库',
    message: '删掉之后 Flandora 会立刻重新解密一份。聊天内容、图片和表情本身都在微信里，不会少，但重新解密的这几秒会卡一下。确定吗？',
  },
  updates: {
    title: '清空更新暂存',
    message: '下载好但还没安装的更新包会被删掉，之后再检查更新时重新下载。确定吗？',
  },
}

function askClean(target) {
  const card = CLEAN_CARDS[target]
  if (!card) return runClean(target)
  pendingClean.value = { target, ...card }
}

async function runClean(target) {
  if (busy.value) return
  busy.value = target
  pendingClean.value = null
  const res = await post('/api/storage/clean', { target })
  busy.value = ''
  if (!res.ok || !res.data || res.data.ok === false) {
    cleanMsg.value = '清理失败：' + (res.data?.error || `HTTP ${res.status}`)
    toast(cleanMsg.value, 'error')
    return
  }
  const data = res.data
  cleanMsg.value = `已删除 ${data.removed_files} 个文件，释放 ${fmtBytes(data.freed_bytes)}。`
    + (data.note ? ` ${data.note}` : '')
    + (data.redecrypt_error ? ` 重新解密失败：${data.redecrypt_error}` : '')
  toast(`清理完成，释放 ${fmtBytes(data.freed_bytes)}`, 'success')
  await loadStorage()
}

async function rotateNow() {
  if (busy.value) return
  busy.value = 'rotate_log'
  const res = await post('/api/storage/clean', { target: 'rotate_log' })
  busy.value = ''
  if (!res.ok || !res.data || res.data.ok === false) {
    cleanMsg.value = '轮转失败：' + (res.data?.error || `HTTP ${res.status}`)
    toast(cleanMsg.value, 'error')
    return
  }
  cleanMsg.value = `日志已轮转，新的空文件已就位。${res.data.note || ''}`
  toast('日志已轮转', 'success')
  await loadStorage()
}

// ---------- 日志策略 ----------

const { saving: logSaving, saved: logSaved, save: saveLogConfig } = useSettingsSave()
const logDirty = computed(() => {
  const logs = stats.value?.logs
  if (!logs) return false
  return Number(props.cfg.log_max_mb) !== Number(logs.max_mb)
    || Number(props.cfg.log_backup_count) !== Number(logs.backup_count)
})

async function saveLogPolicy() {
  const maxMb = Number(props.cfg.log_max_mb)
  const backups = Number(props.cfg.log_backup_count)
  if (!(maxMb >= 1 && maxMb <= 1024)) { toast('日志单份上限取 1–1024 MB', 'error'); return }
  if (!(backups >= 1 && backups <= 20)) { toast('日志保留份数取 1–20', 'error'); return }
  if (await saveLogConfig({ log_max_mb: maxMb, log_backup_count: backups })) await loadStorage()
}

// ---------- 消息存档 ----------

const pruneDays = ref(30)
const archiveMsg = ref('')
const confirm = ref('')
function requestPrune() {
  const days = Number(pruneDays.value)
  if (!Number.isFinite(days) || days < 1 || days > 3650) {
    toast('保留天数要在 1 到 3650 之间', 'error')
    return
  }
  confirm.value = 'prune'
}

async function archiveAction(action) {
  if (busy.value) return
  busy.value = action
  confirm.value = ''
  const body = action === 'prune' ? { action, days: Number(pruneDays.value) || 0 } : { action }
  const res = await post('/api/storage/messages', body)
  busy.value = ''
  if (!res.ok || !res.data || res.data.ok === false) {
    archiveMsg.value = '操作失败：' + (res.data?.error || `HTTP ${res.status}`)
    toast(archiveMsg.value, 'error')
    return
  }
  const data = res.data
  if (action === 'prune') {
    archiveMsg.value = `已删掉 ${data.removed_rows} 条 ${data.days} 天前的记录，存档还剩 ${data.rows} 条。`
  } else if (action === 'clear') {
    archiveMsg.value = `存档已清空，共删掉 ${data.removed_rows} 条。`
  } else {
    archiveMsg.value = `数据库已重写，释放 ${fmtBytes(data.freed_bytes)}（${fmtBytes(data.bytes_before)} → ${fmtBytes(data.bytes_after)}）。`
  }
  toast('消息存档已处理', 'success')
  await loadStorage()
}
</script>

<template>
  <div class="storage-section">
    <section class="card settings-section" aria-labelledby="storage-overview-heading">
      <div class="section-head">
        <div>
          <h3 id="storage-overview-heading" class="card-title">占用一览</h3>
          <p class="card-description">数据目录里各类文件各占了多少，只统计，不动任何文件。</p>
        </div>
        <button type="button" class="btn btn-outline btn-sm" :disabled="busy === 'refresh'" @click="loadStorage">
          <AppIcon name="RefreshCw" :size="14" />重新统计
        </button>
      </div>

      <p v-if="loadError" class="load-note">读不到占用情况：{{ loadError }}</p>
      <div v-else-if="!stats" class="load-note">正在统计…</div>
      <template v-else>
        <div class="stat-grid">
          <div class="stat">
            <span class="stat-label">数据目录总量</span>
            <strong class="stat-value">{{ fmtBytes(stats.total_bytes) }}</strong>
            <span class="stat-meta">{{ stats.total_files }} 个文件 · 其它 {{ fmtBytes(stats.other_bytes) }}</span>
          </div>
          <div class="stat">
            <span class="stat-label">日志</span>
            <strong class="stat-value">{{ fmtBytes(stats.logs.bytes) }}</strong>
            <span class="stat-meta">历史 {{ stats.logs.rotated_files }} 份 {{ fmtBytes(stats.logs.rotated_bytes) }} · 上限 {{ stats.logs.max_mb }} MB × {{ stats.logs.backup_count }}</span>
          </div>
          <div class="stat">
            <span class="stat-label">消息存档</span>
            <strong class="stat-value">{{ stats.messages.rows }} 条</strong>
            <span class="stat-meta">{{ fmtBytes(stats.messages.bytes) }} · {{ archiveSpan || '还没有记录' }}</span>
          </div>
          <div class="stat">
            <span class="stat-label">图片缓存</span>
            <strong class="stat-value">{{ fmtBytes(stats.images.bytes) }}</strong>
            <span class="stat-meta">{{ stats.images.files }} 个已解码的图片文件</span>
          </div>
          <div class="stat">
            <span class="stat-label">微信数据缓存</span>
            <strong class="stat-value">{{ fmtBytes(stats.wx_cache.bytes) }}</strong>
            <span class="stat-meta">{{ stats.wx_cache.files }} 个解密出来的库文件</span>
          </div>
          <div class="stat">
            <span class="stat-label">更新暂存</span>
            <strong class="stat-value">{{ fmtBytes(stats.updates.bytes) }}</strong>
            <span class="stat-meta">{{ stats.updates.files }} 个下载好的更新包</span>
          </div>
        </div>
        <p class="path-hint mono">{{ stats.data_dir }}<span v-if="stats.generated_at"> · 统计于 {{ stats.generated_at }}</span></p>
      </template>
    </section>

    <section class="card settings-section" aria-labelledby="storage-log-heading">
      <div class="section-head">
        <div>
          <h3 id="storage-log-heading" class="card-title">日志策略</h3>
          <p class="card-description">写到多大就轮转、留几份旧的。</p>
        </div>
        <AppIcon name="FileText" :size="19" />
      </div>
      <div class="grid-2">
        <div class="field">
          <label for="log-max-mb">单份上限（MB）</label>
          <input id="log-max-mb" v-model.number="cfg.log_max_mb" class="input" type="number" min="1" max="1024" />
        </div>
        <div class="field">
          <label for="log-backup-count">保留份数</label>
          <input id="log-backup-count" v-model.number="cfg.log_backup_count" class="input" type="number" min="1" max="20" />
        </div>
      </div>
      <p class="hint">写满上限就换成 flandora.log.1，旧的依次后退，超出的自动删掉。总占用最多约 {{ Number(cfg.log_max_mb) * (Number(cfg.log_backup_count) + 1) }} MB。改完立刻生效，不必重启。</p>
      <div class="clean-row">
        <div class="clean-text">
          <strong>马上轮转一次</strong>
          <span>不等写满，现在就把当前日志封存一份。</span>
        </div>
        <button type="button" class="btn btn-outline btn-sm" :disabled="busy === 'rotate_log'" @click="rotateNow">
          <AppIcon name="RotateCcw" :size="14" />现在轮转一次
        </button>
      </div>
      <SectionFoot :saving="logSaving" :saved="logSaved" :disabled="!logDirty" label="保存日志策略" hint="保存后立刻生效" @save="saveLogPolicy" />
    </section>

    <section class="card settings-section" aria-labelledby="storage-clean-heading">
      <div class="section-head">
        <div>
          <h3 id="storage-clean-heading" class="card-title">缓存与暂存</h3>
          <p class="card-description">这些都是可以再生的中间产物，删掉只影响下一次读取的快慢。</p>
        </div>
        <AppIcon name="Eraser" :size="19" />
      </div>

      <template v-if="stats">
        <div class="clean-row">
          <div class="clean-text">
            <strong>图片缓存</strong>
            <span>{{ stats.images.files }} 个文件 · {{ fmtBytes(stats.images.bytes) }}，解码后的图片副本</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" :disabled="!stats.images.files || busy === 'images'" @click="askClean('images')">
            <AppIcon name="Trash2" :size="14" />清理图片缓存
          </button>
        </div>
        <div class="clean-row">
          <div class="clean-text">
            <strong>微信数据缓存</strong>
            <span>{{ stats.wx_cache.files }} 个文件 · {{ fmtBytes(stats.wx_cache.bytes) }}，解密出来的副本，清完会立刻重新解密</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" :disabled="!stats.wx_cache.files || busy === 'wx_cache'" @click="askClean('wx_cache')">
            <AppIcon name="Trash2" :size="14" />清理解密缓存
          </button>
        </div>
        <div class="clean-row">
          <div class="clean-text">
            <strong>历史日志</strong>
            <span>{{ stats.logs.rotated_files }} 份 · {{ fmtBytes(stats.logs.rotated_bytes) }}，超出保留份数的会删掉</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" :disabled="!stats.logs.rotated_files || busy === 'old_logs'" @click="askClean('old_logs')">
            <AppIcon name="Trash2" :size="14" />清理历史日志
          </button>
        </div>
        <div class="clean-row">
          <div class="clean-text">
            <strong>更新暂存</strong>
            <span>{{ stats.updates.files }} 个文件 · {{ fmtBytes(stats.updates.bytes) }}，下载好但没安装的更新包</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" :disabled="!stats.updates.files || busy === 'updates'" @click="askClean('updates')">
            <AppIcon name="Trash2" :size="14" />清空更新暂存
          </button>
        </div>
        <p v-if="cleanMsg" class="clean-msg">{{ cleanMsg }}</p>
      </template>
      <p v-else class="load-note">先读一次占用情况才能清理。</p>
    </section>

    <section class="card settings-section" aria-labelledby="storage-archive-heading">
      <div class="section-head">
        <div>
          <h3 id="storage-archive-heading" class="card-title">消息存档</h3>
          <p class="card-description">发给客户端的消息按原样存在这里，撤回与引用要靠它取回原文。</p>
        </div>
        <AppIcon name="MessagesSquare" :size="19" />
      </div>
      <p class="hint">这一段不会自动清理：message_id 一旦发给客户端就是长期契约，静默回收会让老客户端的撤回与引用突然指向不存在的消息。腾空间由你在这里明确按下。</p>
      <template v-if="stats">
        <div class="archive-facts">
          <span>{{ stats.messages.rows }} 条记录</span>
          <span>{{ fmtBytes(stats.messages.bytes) }}</span>
          <span v-if="archiveSpan">{{ archiveSpan }}</span>
        </div>
        <div class="archive-actions">
          <div class="field prune-field">
            <label for="prune-days">只保留最近多少天</label>
            <input id="prune-days" v-model.number="pruneDays" class="input" type="number" min="1" max="3650" />
          </div>
          <button type="button" class="btn btn-outline btn-sm" :disabled="!stats.messages.rows || busy === 'prune'" @click="requestPrune">
            <AppIcon name="Trash2" :size="14" />清理旧记录
          </button>
          <button type="button" class="btn btn-outline btn-sm" :disabled="busy === 'vacuum'" @click="archiveAction('vacuum')">
            <AppIcon name="Eraser" :size="14" />压缩数据库
          </button>
          <button type="button" class="btn btn-outline-danger btn-sm" :disabled="!stats.messages.rows || busy === 'clear'" @click="confirm = 'clear'">
            <AppIcon name="Trash2" :size="14" />清空存档
          </button>
        </div>
        <p class="hint">删掉记录不会让文件立刻变小，还要点一次「压缩数据库」才会把空间还给系统。</p>
        <p v-if="archiveMsg" class="clean-msg">{{ archiveMsg }}</p>
      </template>
      <p v-else class="load-note">先读一次占用情况才能操作存档。</p>
    </section>

    <ConfirmDialog
      :open="!!pendingClean"
      :title="pendingClean ? pendingClean.title : ''"
      :message="pendingClean ? pendingClean.message : ''"
      confirm-text="清理"
      @cancel="pendingClean = null"
      @confirm="runClean(pendingClean.target)"
    />
    <ConfirmDialog
      :open="confirm === 'prune'"
      title="按天数清理消息存档"
      :message="`会删掉 ${pruneDays} 天前的全部记录，删掉的那些 message_id 之后取不回原文。确定吗？`"
      confirm-text="清理"
      @cancel="confirm = ''"
      @confirm="archiveAction('prune')"
    />
    <ConfirmDialog
      :open="confirm === 'clear'"
      title="清空整个消息存档"
      :message="'所有历史记录都会被删掉，客户端的撤回与引用会取不到原文。确定吗？'"
      confirm-text="清空"
      @cancel="confirm = ''"
      @confirm="archiveAction('clear')"
    />
  </div>
</template>

<style scoped>
.storage-section { display:flex; flex-direction:column; gap:16px; }
.settings-section { min-height:0; box-shadow:none; padding:20px; }
.section-head > .app-icon { color:var(--faint); }
.section-head .card-description { margin-bottom:0; }
.load-note { padding:14px 0 4px; font-size:12px; color:var(--muted); }
.stat-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.stat { display:flex; flex-direction:column; gap:4px; padding:12px 14px; border:1px solid var(--border); border-radius:10px; background:var(--inset); }
.stat-label { font-size:11px; color:var(--muted); }
.stat-value { font-size:16px; font-weight:600; color:var(--text-strong); }
.stat-meta { font-size:10px; color:var(--faint); line-height:1.6; }
.path-hint { margin-top:14px; font-size:10px; color:var(--faint); word-break:break-all; }
.clean-row { display:flex; align-items:center; gap:12px 16px; margin-top:12px; padding:12px 14px; border:1px solid var(--border); border-radius:10px; background:var(--inset); }
.clean-text { display:flex; flex-direction:column; gap:3px; min-width:0; }
.clean-text strong { font-size:12px; font-weight:600; color:var(--text-strong); }
.clean-text span { font-size:11px; color:var(--muted); line-height:1.6; }
.clean-row .btn { margin-left:auto; flex:none; }
.clean-msg { margin-top:12px; font-size:11px; color:var(--muted); line-height:1.7; }
.archive-facts { display:flex; flex-wrap:wrap; gap:8px 18px; margin:14px 0; font-size:12px; color:var(--muted); }
.archive-actions { display:flex; flex-wrap:wrap; align-items:flex-end; gap:10px 12px; }
.prune-field { max-width:190px; }
@media(max-width:1100px) { .stat-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media(max-width:767px) {
  .settings-section { padding:17px; }
  .stat-grid { grid-template-columns:minmax(0,1fr); }
  .clean-row { align-items:stretch; flex-direction:column; }
  .clean-row .btn { margin-left:0; min-height:44px; }
  .archive-actions { align-items:stretch; flex-direction:column; }
  .archive-actions .btn { width:100%; min-height:44px; }
  .prune-field { max-width:none; }
}
</style>
