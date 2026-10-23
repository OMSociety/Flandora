<script setup>
import { toasts } from '../store'
</script>

<template>
  <div class="toast-wrap">
    <div v-for="t in toasts" :key="t.id" class="toast-item" :class="t.type">
      {{ t.message }}
    </div>
  </div>
</template>
