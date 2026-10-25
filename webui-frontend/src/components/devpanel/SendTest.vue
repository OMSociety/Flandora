<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, post } from '../../api'
import { status, toast } from '../../store'
import { kindLabel } from '../../utils/format'
import AppIcon from '../AppIcon.vue'
const contacts = ref([])
const contact = ref('')
const text = ref('测试消息')
const sending = ref(false)
const characterCount = computed(() => [...text.value].length)
const canSend = computed(() => contact.value.trim() && text.value.trim() && !sending.value)
async function loadContacts() {
  const res = await get('/api/contacts')
  if (res.ok && res.data && Array.isArray(res.data.contacts)) contacts.value = res.data.contacts
  else toast('联系人列表加载失败，仍可手动输入联系人', 'error')
}
async function send() {
  if (!contact.value.trim()) { toast('请填写联系人或群名', 'error'); return }
  if (!text.value.trim()) { toast('消息内容不能为空', 'error'); return }
  if (!status.running) { toast('桥接服务当前未运行，发送可能失败', 'info') }
  if (sending.value) return
  sending.value = true
  const res = await post('/api/send', { contact: contact.value.trim(), text: text.value })
  sending.value = false
  if (res.ok && res.data?.ok) { toast('测试消息已发送', 'success'); text.value = '' }
  else toast('发送失败：' + (res.data?.error || (res.status ? `HTTP ${res.status}` : '请求失败')), 'error')
}
onMounted(loadContacts)
</script>
<template>
  <div class="send-panel">
    <div class="test-layout">
      <section class="card compose-card"><div class="section-head"><h3 class="card-title">测试消息</h3><span class="badge" :class="status.running ? 'badge-green' : 'badge-gray'">{{ status.running ? '发送通道已就绪' : '服务未运行' }}</span></div><div class="field"><label for="test-contact">联系人 / 群名</label><div class="contact-input"><AppIcon name="MessageCircle" :size="16" /><input id="test-contact" v-model="contact" list="contact-list" placeholder="例如：文件传输助手" /></div><datalist id="contact-list"><option v-for="c in contacts" :key="c.id" :value="c.name" :label="`${c.name}（${kindLabel(c.kind)}）`"></option></datalist><p class="hint">输入已有联系人，或配置中映射的真实群名。</p></div><div class="field message-field"><div class="message-label"><label for="test-message">消息内容</label><span>{{ characterCount }} 字</span></div><textarea id="test-message" v-model="text" class="textarea" rows="6" placeholder="输入要发送的内容" @keydown.ctrl.enter="send"></textarea></div><div class="send-footer"><span>按 Ctrl + Enter 发送</span><button class="btn" :disabled="!canSend" @click="send"><AppIcon :name="sending ? 'LoaderCircle' : 'Send'" :size="15" />{{ sending ? '正在发送…' : '发送测试消息' }}</button></div></section>
      <aside class="test-aside"><section class="card path-card"><h3 class="card-title">消息将经过</h3><div class="path-step"><span><AppIcon name="Send" :size="16" /></span><div><strong>Flandora</strong><p>接收测试消息</p></div></div><div class="path-line"></div><div class="path-step"><span><AppIcon name="Monitor" :size="16" /></span><div><strong>微信客户端</strong><p>UI 自动化投递</p></div></div><div class="path-line"></div><div class="path-step"><span><AppIcon name="MessageCircle" :size="16" /></span><div><strong>目标会话</strong><p>联系人或群聊</p></div></div></section><div class="banner"><AppIcon name="CircleAlert" :size="17" /><span>非隔离桌面模式下，发送会激活微信窗口并短暂抢占前台。发送期间请避免操作键盘。</span></div><section class="card tips-card"><h3>发送失败？</h3><ul><li>在仪表盘确认桥接服务已启动</li><li>检查微信窗口是否已正确绑定</li><li>群聊需填写「群名映射」中的真实群名</li></ul></section></aside>
    </div>
  </div>
</template>
<style scoped>
.send-panel { display:flex; flex-direction:column; gap:16px; }
.test-layout { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(250px,1fr); gap:16px; align-items:start; max-width:1100px; }
.compose-card { padding:20px; }
.field + .field { margin-top:22px; }
.contact-input { display:flex; align-items:center; gap:9px; border:1px solid var(--border-strong); border-radius:8px; padding:0 11px; color:var(--muted); }
.contact-input:focus-within { border-color:var(--primary); box-shadow:0 0 0 3px var(--primary-soft); }
.contact-input input { flex:1; min-width:0; min-height:40px; border:0; outline:0; background:transparent; color:var(--text-strong); font-size:12px; }
.field > .hint { margin-top:7px; }
.message-label { display:flex; justify-content:space-between; margin-bottom:7px; font-size:12px; }
.message-label span { font-size:10px; color:var(--muted); }
.message-field .textarea { min-height:160px; }
.send-footer { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:20px; padding-top:18px; border-top:1px solid var(--border); }
.send-footer > span { color:var(--muted); font-size:10px; }
.test-aside { display:flex; flex-direction:column; gap:14px; }
.path-card, .tips-card { box-shadow:none; }
.path-step { display:flex; align-items:center; gap:11px; }
.path-step > span { display:grid; place-items:center; width:32px; height:32px; border-radius:9px; color:var(--primary-deep); background:var(--primary-soft); }
.path-step strong { font-size:12px; font-weight:500; }
.path-step p { margin-top:2px; font-size:10px; color:var(--muted); }
.path-line { height:20px; margin-left:15px; border-left:1px dashed var(--border-strong); }
.tips-card h3 { font-size:12px; font-weight:500; margin-bottom:10px; }
.tips-card ul { padding-left:17px; color:var(--muted); font-size:11px; line-height:2; }
@media(max-width:950px) { .test-layout { grid-template-columns:1fr; } .test-aside { display:grid; grid-template-columns:1fr 1fr; } .test-aside .banner { grid-column:1/-1; } }
@media(max-width:767px) { .compose-card { padding:17px; } .test-aside { display:flex; } .send-footer > span { display:none; } .send-footer .btn { width:100%; } }
</style>
