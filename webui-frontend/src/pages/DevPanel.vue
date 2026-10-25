<script setup>
import { reactive, ref, onMounted } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ActionDebug from '../components/devpanel/ActionDebug.vue'
import SendTest from '../components/devpanel/SendTest.vue'

const TABS = [
  { id: 'debug', label: '动作调试', icon: 'Wrench' },
  { id: 'send', label: '发送测试', icon: 'Send' },
]

const activeTab = ref('debug')
// 先只挂当前标签，切过去再挂：没打开的标签不必先拉它的数据。
const visited = reactive({ debug: false, send: false })

function activate(id) {
  if (!TABS.some((t) => t.id === id)) return
  activeTab.value = id
  visited[id] = true
}

function selectTab(id) {
  activate(id)
  history.replaceState(null, '', `#/dev?tab=${id}`)
}

onMounted(() => {
  const tab = new URLSearchParams((window.location.hash.split('?')[1] || '')).get('tab')
  activate(tab || 'debug')
})
</script>

<template>
  <div class="dev-page">
    <div class="panel-tabs" role="tablist" aria-label="调试面板">
      <button
        v-for="tab in TABS" :key="tab.id" :id="`devtab-${tab.id}`" type="button" role="tab"
        :aria-selected="activeTab === tab.id ? 'true' : 'false'"
        :aria-controls="`devpanel-${tab.id}`"
        :class="{ active: activeTab === tab.id }"
        @click="selectTab(tab.id)"
      >
        <AppIcon :name="tab.icon" :size="15" />{{ tab.label }}
      </button>
    </div>
    <div v-if="visited.debug" v-show="activeTab === 'debug'" id="devpanel-debug" role="tabpanel" aria-labelledby="devtab-debug">
      <ActionDebug />
    </div>
    <div v-if="visited.send" v-show="activeTab === 'send'" id="devpanel-send" role="tabpanel" aria-labelledby="devtab-send">
      <SendTest />
    </div>
  </div>
</template>

<style scoped>
.dev-page { display:flex; flex-direction:column; gap:16px; }
.panel-tabs { display:flex; gap:4px; margin:2px 0 2px; padding-bottom:2px; overflow-x:auto; border-bottom:1px solid var(--border); }
.panel-tabs button { display:inline-flex; align-items:center; gap:6px; flex-shrink:0; padding:8px 12px; min-height:34px; border:0; border-bottom:2px solid transparent; background:transparent; color:var(--muted); font-size:12px; font-family:inherit; cursor:pointer; }
.panel-tabs button:hover { color:var(--text-strong); }
.panel-tabs button.active { color:var(--primary-deep); border-bottom-color:var(--primary); }
</style>
