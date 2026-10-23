<script setup>
import Modal from './Modal.vue'
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'cancel'])
</script>
<template>
  <Modal :open="open" :title="title" :width="420" :dismissible="false" @close="emit('cancel')">
    <p class="confirm-message">{{ message }}</p>
    <div class="confirm-actions">
      <button class="btn btn-outline" @click="emit('cancel')">{{ cancelText }}</button>
      <button class="btn" :class="{ 'btn-danger': danger }" @click="emit('confirm')">{{ confirmText }}</button>
    </div>
  </Modal>
</template>
