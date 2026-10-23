<script setup>
import { toasts } from '../store'
import AppIcon from './AppIcon.vue'
</script>
<template><div class="toast-wrap" aria-live="polite" aria-atomic="false"><div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.type"><AppIcon :name="t.type === 'success' ? 'CircleCheck' : t.type === 'error' ? 'CircleX' : 'Info'" :size="17" /><span>{{ t.message }}</span></div></div></template>
