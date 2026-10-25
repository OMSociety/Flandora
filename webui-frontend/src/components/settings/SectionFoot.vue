<script setup>
import AppIcon from '../AppIcon.vue'

defineProps({
  saving: { type: Boolean, default: false },
  saved: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '保存' },
  hint: { type: String, default: '部分字段保存后需重启桥接生效' },
})
const emit = defineEmits(['save'])
</script>

<template>
  <div class="section-foot">
    <span class="foot-hint">{{ hint }}</span>
    <button class="btn save-btn" :disabled="disabled || saving" @click="emit('save')">
      <AppIcon :name="saving ? 'LoaderCircle' : saved ? 'Check' : 'CheckCheck'" :size="16" />
      {{ saving ? '正在保存…' : saved ? '已保存' : label }}
    </button>
  </div>
</template>

<style scoped>
.section-foot { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:22px; padding-top:16px; border-top:1px solid var(--border); }
.foot-hint { font-size:10px; color:var(--muted); }
.save-btn { min-width:110px; }
@media(max-width:767px) {
  .section-foot { align-items:stretch; flex-direction:column; }
  .foot-hint { order:2; }
  .save-btn { order:1; width:100%; min-height:44px; }
}
</style>
