<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const on = computed(() => props.modelValue !== false)
</script>

<template>
  <button
    type="button"
    class="switch"
    role="switch"
    :aria-checked="on ? 'true' : 'false'"
    :aria-label="label || undefined"
    :title="label || undefined"
    :disabled="disabled"
    @click="emit('update:modelValue', !on)"
  >
    <span class="knob"></span>
  </button>
</template>

<style scoped>
.switch { position:relative; width:38px; height:22px; flex-shrink:0; padding:0; border:1px solid var(--border); border-radius:999px; background:var(--inset); cursor:pointer; transition:background .16s, border-color .16s; }
.switch .knob { position:absolute; top:2px; left:2px; width:16px; height:16px; border-radius:50%; background:var(--card); box-shadow:0 1px 3px rgb(0 0 0 / .2); transition:transform .16s; }
.switch[aria-checked="true"] { background:var(--primary-strong); border-color:var(--primary-strong); }
.switch[aria-checked="true"] .knob { transform:translateX(16px); }
.switch:disabled { opacity:.45; cursor:not-allowed; }
</style>
