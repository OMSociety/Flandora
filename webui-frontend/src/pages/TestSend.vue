<script setup>
import { ref, onMounted } from 'vue'
import { get, post } from '../api'
import { toast } from '../store'
import { kindLabel } from '../utils/format'

const contacts = ref([])
const contact = ref('')
const text = ref('测试消息')
const sending = ref(false)

async function loadContacts() {
  const res = await get('/api/contacts')
  if (res.ok && res.data && Array.isArray(res.data.contacts)) {
    contacts.value = res.data.contacts
  }
}

async function send() {
  if (sending.value) return
  const c = contact.value.trim()
  if (!c) {
    toast('请填写联系人 / 群名', 'error')
    return
  }
  if (!text.value.trim()) {
    toast('消息内容不能为空', 'error')
    return
  }
  sending.value = true
  const res = await post('/api/send', { contact: c, text: text.value })
  sending.value = false

  if (res.ok && res.data && res.data.ok) {
    toast('✅ 测试消息已发送', 'success')
    text.value = ''
  } else {
    const errMsg = (res.data && res.data.error) || (res.status ? `HTTP ${res.status}` : '请求失败')
    toast('❌ 发送失败：' + errMsg, 'error')
  }
}

onMounted(loadContacts)
</script>

<template>
  <div class="test-page">
    <div class="banner">
      ⚠️ 测试发送会通过 UIA 模拟键盘操作微信窗口，发送时微信会被激活到前台抢占焦点。
    </div>

    <div class="card">
      <div class="card-title">向指定联系人发一条测试消息</div>

      <div class="field">
        <label>联系人 / 群名</label>
        <input
          v-model="contact"
          class="input"
          list="contact-list"
          placeholder="如: 文件传输助手 / 群名（群名填 group_name_map 中的真实群名）"
        />
        <datalist id="contact-list">
          <option v-for="c in contacts" :key="c.id" :value="c.name">
            {{ c.name }}（{{ kindLabel(c.kind) }}）
          </option>
        </datalist>
      </div>

      <div class="field" style="margin-top: 12px">
        <label>消息内容</label>
        <textarea v-model="text" class="textarea" rows="3" placeholder="输入要发送的内容..."></textarea>
      </div>

      <div class="send-row">
        <button class="btn btn-pink" :disabled="sending" @click="send()">
          {{ sending ? '发送中...' : '✈ 发送测试' }}
        </button>
        <span class="note">群聊请填写 group_name_map 中配置的真实群名</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.test-page {
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
.send-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.note {
  font-size: 12px;
  color: var(--faint);
}
</style>
