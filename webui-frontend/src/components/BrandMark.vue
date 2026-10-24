<script setup>
defineProps({ size: { type: Number, default: 36 } })
</script>
<template>
  <svg :width="size" :height="size" viewBox="0 0 40 40" fill="none" role="img" aria-label="Flandora">
    <rect width="40" height="40" rx="12" fill="currentColor" />
    <rect x="8.25" y="10.75" width="15.5" height="11.5" rx="5.2" fill="white" />
    <path d="M13.45 22.25 10.25 25.35 10.25 21.15Z" fill="white" />
    <g opacity=".72">
      <rect x="16.75" y="14.75" width="15" height="11.5" rx="5.2" fill="white" />
      <path d="M26.55 26.25 28.75 29.25 28.75 25.75Z" fill="white" />
    </g>
  </svg>
</template>
