<script setup>
import { computed, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import Modal from '../Modal.vue'
import PasswordInput from '../PasswordInput.vue'
import ToggleSwitch from '../ToggleSwitch.vue'
import { toast } from '../../store'
import { copyText } from '../../utils/clipboard'
import { ROLES, isServerNode, nodeProblem, randomToken, typeMeta } from './nodeMeta'

const props = defineProps({
  open: { type: Boolean, default: false },
  node: { type: Object, default: null },
})
const emit = defineEmits(['close', 'save'])

const form = ref(null)
const meta = computed(() => typeMeta(form.value?.type))
const problem = computed(() => (form.value ? nodeProblem(form.value) : ''))

watch(() => [props.open, props.node], () => {
  form.value = props.open && props.node ? JSON.parse(JSON.stringify(props.node)) : null
}, { immediate: true })

function regenerate() {
  if (!form.value) return
  form.value.access_token = randomToken()
}
async function copyToken() {
  // 明文 HTTP 下面板拿不到 navigator.clipboard，复制失败也只说人话，不把浏览器的报错抛给用户。
  if (await copyText(form.value?.access_token || '')) toast('令牌已复制到剪贴板', 'success')
  else toast('复制失败，请手动选中令牌后复制', 'error')
}
function submit() {
  if (!form.value || problem.value) return
  emit('save', JSON.parse(JSON.stringify(form.value)))
}
</script>

<template>
  <Modal :open="open" :title="`编辑${meta.dialog}`" :width="620" @close="emit('close')">
    <div v-if="form" class="node-form">
      <p class="dialog-hint">{{ meta.hint }}</p>

      <div class="form-group">
        <div class="group-line">
          <div>
            <strong>启用</strong>
            <p class="hint">关闭后保存即可保留配置，但不启动这个入口。</p>
          </div>
          <ToggleSwitch v-model="form.enabled" :label="`启用 ${form.name}`" />
        </div>
      </div>

      <div class="form-group">
        <h4 class="group-title">连接</h4>
        <div class="field">
          <label for="node-name">名称</label>
          <input id="node-name" v-model="form.name" class="input" placeholder="给这个入口起个名字" />
        </div>
        <div v-if="isServerNode(form)" class="grid-3">
          <div class="field">
            <label for="node-host">主机</label>
            <input id="node-host" v-model="form.host" class="input" placeholder="127.0.0.1" />
          </div>
          <div class="field">
            <label for="node-port">端口</label>
            <input id="node-port" v-model.number="form.port" class="input" type="number" min="1" max="65535" />
          </div>
          <div class="field">
            <label for="node-path">路径</label>
            <input id="node-path" v-model="form.path" class="input" placeholder="/" />
          </div>
        </div>
        <div v-else class="field">
          <label for="node-url">目标地址</label>
          <input id="node-url" v-model="form.url" class="input" :placeholder="form.type === 'ws_client' ? 'ws://127.0.0.1:6199/ws' : 'http://127.0.0.1:8080/onebot'" />
          <p class="hint">填机器人框架那一侧的监听地址。</p>
        </div>
      </div>

      <div class="form-group">
        <h4 class="group-title">鉴权</h4>
        <div class="field">
          <label for="node-token">授权 Token</label>
          <div class="token-row">
            <PasswordInput id="node-token" v-model="form.access_token" :placeholder="form.type === 'http_server' ? '必填' : '留空则不校验'" />
            <button type="button" class="icon-btn token-btn" title="随机生成一个令牌" aria-label="重新生成令牌" @click="regenerate"><AppIcon name="RefreshCw" :size="15" /></button>
            <button type="button" class="icon-btn token-btn" title="复制当前令牌" aria-label="复制令牌" @click="copyToken"><AppIcon name="Copy" :size="15" /></button>
          </div>
          <p class="hint">服务端比对 Authorization: Bearer；反向连接会带上这个令牌。</p>
        </div>
      </div>

      <div class="form-group">
        <h4 class="group-title">行为</h4>
        <div class="grid-2">
          <div class="field">
            <label for="node-format">消息格式</label>
            <select id="node-format" v-model="form.message_format" class="input">
              <option value="">跟随全局</option>
              <option value="array">消息段数组（array）</option>
              <option value="string">CQ 码字符串（string）</option>
            </select>
          </div>
          <div class="field">
            <label for="node-role">角色</label>
            <select id="node-role" v-model="form.role" class="input">
              <option v-for="role in ROLES" :key="role.value" :value="role.value">{{ role.label }}</option>
            </select>
          </div>
          <div v-if="form.type === 'ws_client'" class="field">
            <label for="node-reconnect">重连间隔（毫秒）</label>
            <input id="node-reconnect" v-model.number="form.reconnect_interval_ms" class="input" type="number" min="1000" max="600000" />
          </div>
          <div v-else-if="!isServerNode(form)" class="field">
            <label for="node-timeout">超时（毫秒）</label>
            <input id="node-timeout" v-model.number="form.timeout_ms" class="input" type="number" min="100" max="120000" />
          </div>
        </div>
        <p class="hint">同一个入口可同时挂多个，保存后立刻生效：新增的起、删掉的断开、改过的重建。</p>
      </div>

      <div class="dialog-foot">
        <span v-if="problem" class="dialog-problem">{{ problem }}</span>
        <button class="btn btn-outline" @click="emit('close')">取消</button>
        <button class="btn" :disabled="!!problem" @click="submit">保存修改</button>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.node-form { display:flex; flex-direction:column; gap:16px; }
.dialog-hint { margin:-4px 0 0; font-size:12px; color:var(--muted); }
.form-group { display:flex; flex-direction:column; gap:10px; padding:14px; border:1px solid var(--border); border-radius:10px; }
.group-title { font-size:11px; font-weight:600; color:var(--muted); letter-spacing:.04em; }
.group-line { display:flex; align-items:center; justify-content:space-between; gap:14px; }
.group-line strong { font-size:13px; color:var(--text-strong); }
.group-line .hint { margin:3px 0 0; }
.grid-2 { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; }
.grid-3 { display:grid; grid-template-columns:minmax(0, 1.4fr) 96px minmax(0, 1fr); gap:12px; }
.token-row { display:flex; gap:6px; align-items:center; }
.token-row .pw { flex:1; min-width:0; }
.token-btn { width:36px; height:36px; border:1px solid var(--border); }
.dialog-foot { display:flex; align-items:center; justify-content:flex-end; gap:8px; padding-top:4px; }
.dialog-problem { margin-right:auto; font-size:11px; color:var(--danger); }
@media(max-width:767px) {
  .grid-2, .grid-3 { grid-template-columns:1fr; }
  .dialog-foot { flex-wrap:wrap; }
  .dialog-foot .btn { flex:1; min-height:42px; }
}
</style>
