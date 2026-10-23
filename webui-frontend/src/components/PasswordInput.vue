<script setup>
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
defineProps({ modelValue: { type: [String, Number], default: '' }, placeholder: { type: String, default: '' }, disabled: { type: Boolean, default: false }, id: { type: String, default: undefined } })
const emit = defineEmits(['update:modelValue'])
const show = ref(false)
</script>
<template><div class="pw"><input :id="id" :type="show ? 'text' : 'password'" class="input" :placeholder="placeholder" :disabled="disabled" :value="modelValue" @input="emit('update:modelValue', $event.target.value)" autocomplete="off" /><button type="button" class="pw-toggle" @click="show = !show" :aria-label="show ? '隐藏密钥' : '显示密钥'" :title="show ? '隐藏密钥' : '显示密钥'"><AppIcon :name="show ? 'EyeOff' : 'Eye'" :size="16" /></button></div></template>
<style scoped>
.pw { position:relative; width:100%; }
.pw .input { padding-right:40px; }
.pw-toggle { position:absolute; right:3px; top:50%; transform:translateY(-50%); display:grid; place-items:center; width:32px; height:32px; border:0; border-radius:6px; background:transparent; color:var(--muted); cursor:pointer; }
.pw-toggle:hover { background:var(--inset); color:var(--text-strong); }
</style>
