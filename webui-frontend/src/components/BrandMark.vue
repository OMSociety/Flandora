<script setup>
defineProps({ size: { type: Number, default: 36 } })
</script>
<template>
  <svg :width="size" :height="size" viewBox="0 0 40 40" fill="none" role="img" aria-label="Flandora">
    <rect width="40" height="40" rx="12" fill="currentColor" />
    <path d="M10 12.5h8.5a5.5 5.5 0 0 1 0 11H13l-3 3v-3a5.5 5.5 0 0 1-2-4.2v-1.8a5 5 0 0 1 2-5Z" fill="white" />
    <path d="M22 16.5h5a6 6 0 0 1 6 6v1a6 6 0 0 1-3 5.2v2.8l-3.5-2.8H23a5.5 5.5 0 0 1-5.5-5.5" fill="white" fill-opacity=".72" />
    <path d="m13 16 2.2 4.5L17.5 16m4 7.5h5m-5 2.5h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
</template>
