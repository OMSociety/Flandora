<script setup>
import AppIcon from '../AppIcon.vue'

defineProps({
  items: { type: Array, required: true },
  active: { type: String, required: true },
})
const emit = defineEmits(['select'])
</script>

<template>
  <nav class="settings-nav card" aria-label="设置分区">
    <button
      v-for="item in items" :key="item.id" type="button"
      :class="{ active: active === item.id }"
      :aria-current="active === item.id ? 'page' : undefined"
      @click="emit('select', item.id)"
    >
      <AppIcon :name="item.icon" :size="17" />
      <span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
      <AppIcon name="ChevronRight" :size="14" />
    </button>
  </nav>
</template>

<style scoped>
.settings-nav { padding:8px; box-shadow:none; position:sticky; top:0; }
.settings-nav button { display:flex; align-items:center; gap:10px; width:100%; padding:12px 10px; border:0; border-radius:8px; background:transparent; color:var(--muted); text-align:left; cursor:pointer; }
.settings-nav button:hover { background:var(--inset); }
.settings-nav button.active { background:var(--primary-soft); color:var(--primary-deep); }
.settings-nav button > span { display:flex; flex-direction:column; gap:2px; flex:1; min-width:0; }
.settings-nav strong { font-size:12px; font-weight:500; color:var(--text); }
.settings-nav button.active strong { color:var(--primary-deep); }
.settings-nav small { font-size:10px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
@media(max-width:1000px) { .settings-nav small { display:none; } }
@media(max-width:767px) {
  .settings-nav { display:flex; gap:5px; width:100%; overflow-x:auto; position:static; padding:5px; }
  .settings-nav button { width:auto; flex-shrink:0; padding:8px 11px; }
  .settings-nav button > .app-icon:last-child, .settings-nav small { display:none; }
  .settings-nav strong { font-size:11px; }
}
</style>
