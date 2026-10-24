<script setup>
import { ref, onMounted } from 'vue'
import BrandMark from '../components/BrandMark.vue'
import AppIcon from '../components/AppIcon.vue'
import { post } from '../api'

const emit = defineEmits(['logged-in'])
const password = ref('')
const error = ref('')
const pending = ref(false)
const inputEl = ref(null)

async function submit() {
  if (pending.value || !password.value) return
  pending.value = true
  error.value = ''
  const res = await post('/api/login', { password: password.value }, true)
  pending.value = false
  if (res.ok) {
    password.value = ''
    emit('logged-in')
    return
  }
  error.value = (res.data && res.data.error) || `登录失败（${res.status || '网络不通'}）`
  inputEl.value?.select()
}

onMounted(() => inputEl.value?.focus())
</script>

<template>
  <div class="login-shell">
    <form class="login-card" @submit.prevent="submit">
      <div class="login-brand"><BrandMark :size="38" /><span>Flandora</span></div>
      <h1>进入控制台</h1>
      <p class="login-sub">这个面板设了登录密码。密码只在这台机器上校验，浏览器只拿到一张会话 Cookie。</p>
      <label for="login-password">密码</label>
      <input
        id="login-password"
        ref="inputEl"
        v-model="password"
        type="password"
        class="input"
        autocomplete="current-password"
        :aria-invalid="error ? 'true' : 'false'"
        aria-describedby="login-error"
      />
      <p id="login-error" class="login-error" role="alert">{{ error }}</p>
      <button type="submit" class="btn login-submit" :disabled="pending || !password">
        <AppIcon name="LogIn" :size="15" />{{ pending ? '正在校验…' : '进入控制台' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login-shell { display:grid; place-items:center; min-height:100vh; padding:24px; background:var(--bg); }
.login-card { width:100%; max-width:376px; padding:28px; border:1px solid var(--border); border-radius:var(--radius); background:var(--card); box-shadow:var(--shadow); }
.login-brand { display:flex; align-items:center; gap:9px; margin-bottom:20px; font-size:14px; font-weight:600; color:var(--text-strong); letter-spacing:.2px; }
.login-card h1 { margin:0 0 8px; font-size:20px; font-weight:600; color:var(--text-strong); }
.login-sub { margin:0 0 20px; font-size:12.5px; line-height:1.65; color:var(--muted); }
.login-card label { display:block; margin-bottom:6px; font-size:12px; color:var(--text); }
.login-error { min-height:18px; margin:8px 0 2px; font-size:12px; color:var(--danger); }
.login-submit { width:100%; margin-top:6px; }
</style>
