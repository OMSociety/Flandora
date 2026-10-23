<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { get, post } from '../api'
import { toast } from '../store'
import { useDesktopShot } from '../useDesktopShot'

const status = ref(null)
const busy = ref(false)
const autoRefresh = ref(true)

const {
  imgUrl, lastError, shotAt, natural,
  refresh: refreshShot, onImgLoad, click, clear,
} = useDesktopShot()

let timer = null

const enabled = computed(() => !!(status.value && status.value.enabled))
const running = computed(() => !!(status.value && status.value.running))
const loggedIn = computed(() => !!(status.value && status.value.logged_in))
const state = computed(() => (status.value && status.value.state) || 'absent')

const stateLabel = computed(() => {
  switch (state.value) {
    case 'chat':
      return '已登录，正在使用'
    case 'login':
      return '停在登录界面，等待扫码或手机确认'
    case 'absent':
      return '微信未运行'
    default:
      return '状态未知'
  }
})

async function loadStatus() {
  const res = await get('/api/desktop/status', true)
  if (res.ok && res.data && res.data.ok) {
    status.value = res.data
  } else if (res.data && res.data.error) {
    lastError.value = res.data.error
  }
}

async function onClickShot(e) {
  const err = await click(e)
  if (err) toast(err, 'error')
  setTimeout(refreshShot, 300)
}

async function startWeChat() {
  busy.value = true
  const res = await post('/api/desktop/start', {}, true)
  busy.value = false
  if (res.ok && res.data && res.data.ok) {
    toast('微信已在隔离桌面上启动', 'success')
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
    toast('已结束隔离桌面上的微信', 'success')
    clear()
    await loadStatus()
  } else {
    toast((res.data && res.data.error) || '停止失败', 'error')
  }
}

onMounted(async () => {
  await loadStatus()
  await refreshShot()
  timer = setInterval(async () => {
    if (!autoRefresh.value) return
    await loadStatus()
    await refreshShot()
  }, 2000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="desktop-page">
    <div v-if="!enabled" class="banner">
      隔离桌面模式未开启。开启后微信由本程序在另一张 Windows 桌面上启动，向它投递输入时
      切换的前台只发生在那张桌面上，用户自己的桌面不受打扰，发送通道可以长期开着。
      请在「配置」里把 <code>isolated_desktop</code> 设为 true 并重启。
    </div>

    <div class="card">
      <div class="card-title">状态</div>
      <div class="kv">
        <div class="kv-item">
          <span class="kv-k">模式</span>
          <span class="kv-v">{{ enabled ? '隔离桌面' : '未开启' }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-k">微信</span>
          <span class="kv-v">{{ running ? '运行中' : '未运行' }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-k">登录</span>
          <span class="kv-v" :class="{ warn: state === 'login' }">{{ stateLabel }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-k">桌面</span>
          <span class="kv-v">{{ status ? status.desktop : '-' }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-k">画面尺寸</span>
          <span class="kv-v">
            {{ natural.w ? natural.w + ' × ' + natural.h : '-' }}
          </span>
        </div>
      </div>

      <div v-if="status && status.error" class="err">{{ status.error }}</div>

      <div class="btn-row">
        <button class="btn btn-pink" :disabled="busy" @click="startWeChat()">
          {{ running ? '重新确认启动' : '启动微信' }}
        </button>
        <button class="btn" :disabled="busy || !running" @click="stopWeChat()">
          结束微信
        </button>
        <label class="chk">
          <input type="checkbox" v-model="autoRefresh" />
          每 2 秒自动刷新
        </label>
        <button class="btn btn-ghost" @click="refreshShot()">立即刷新</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        微信画面
        <span v-if="shotAt" class="shot-at">更新于 {{ shotAt }}</span>
      </div>

      <div v-if="lastError" class="err">{{ lastError }}</div>

      <div class="shot-wrap">
        <img
          v-if="imgUrl"
          :src="imgUrl"
          class="shot"
          alt="隔离桌面上的微信窗口画面"
          @load="onImgLoad"
          @click="onClickShot"
        />
        <div v-else class="shot-empty">还没有画面</div>
      </div>

      <div class="hint">
        这张画面就是微信窗口的实况，点击它会转发到微信上——首次登录时在这里点「登录」，
        然后按提示用手机扫码或确认。登录完成后本页会显示已登录状态。
      </div>
    </div>
  </div>
</template>

<style scoped>
.desktop-page {
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
.kv {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 26px;
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
.btn-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
}
.chk {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
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
  max-height: 70vh;
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
.hint {
  font-size: 12px;
  color: var(--faint);
  line-height: 1.7;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
</style>
