<script setup>
const props = defineProps({
  active: { type: String, required: true },
})
const emit = defineEmits(['change'])

const tabs = [
  { id: 'dashboard', icon: '📊', label: '仪表盘' },
  { id: 'messages', icon: '💬', label: '消息' },
  { id: 'logs', icon: '📜', label: '日志' },
  { id: 'config', icon: '⚙️', label: '配置' },
  { id: 'test', icon: '✈️', label: '发送测试' },
]
</script>

<template>
  <aside class="sidebar">
    <div class="logo">💎</div>
    <div class="logo-text">MaiBot<br />Bridge</div>

    <nav class="nav">
      <button
        v-for="t in tabs"
        :key="t.id"
        class="nav-item"
        :class="{ active: active === t.id }"
        @click="emit('change', t.id)"
      >
        <span class="nav-icon">{{ t.icon }}</span>
        <span class="nav-label">{{ t.label }}</span>
      </button>
    </nav>

    <div class="sidebar-foot">v1.0</div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 150px;
  min-width: 150px;
  background: linear-gradient(180deg, #fce4ec, #f8e8f0);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 22px 0 16px;
  gap: 4px;
  border-right: 1px solid rgba(240, 98, 146, 0.1);
  overflow: hidden;
}
.logo {
  font-size: 30px;
  line-height: 1;
  margin-bottom: 6px;
}
.logo-text {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--pink-deep);
  text-align: center;
  margin-bottom: 22px;
  text-shadow: 0 1px 3px rgba(214, 51, 110, 0.15);
  line-height: 1.4;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  padding: 0 10px;
}
.nav-item {
  width: 100%;
  height: 52px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
  transition: all 0.2s;
  color: #b06c7a;
  border: none;
  background: transparent;
  padding: 0 12px;
  font-family: inherit;
}
.nav-item:hover {
  background: rgba(240, 98, 146, 0.08);
  color: #d4567a;
}
.nav-item.active {
  background: linear-gradient(135deg, #f48fb1, #f06292);
  color: #fff;
  box-shadow: 0 4px 12px rgba(240, 98, 146, 0.25);
}
.nav-icon {
  font-size: 17px;
}
.nav-label {
  font-size: 12px;
  font-weight: 600;
  flex: 1;
  text-align: left;
}
.sidebar-foot {
  margin-top: auto;
  font-size: 10px;
  color: var(--faint);
}
</style>
