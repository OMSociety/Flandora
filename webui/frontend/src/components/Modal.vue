<script setup>
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
})
const emit = defineEmits(['close'])
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-mask" @click.self="emit('close')">
      <div class="modal-card" role="dialog" aria-modal="true">
        <button class="modal-close" @click="emit('close')" aria-label="关闭">×</button>
        <div class="modal-head">
          <slot name="title">{{ title }}</slot>
        </div>
        <div class="modal-body">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(60, 20, 40, 0.35);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.modal-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  width: 720px;
  max-width: 100%;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}
.modal-close {
  position: absolute;
  top: 12px;
  right: 14px;
  border: none;
  background: transparent;
  font-size: 22px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  width: 30px;
  height: 30px;
  border-radius: 8px;
}
.modal-close:hover {
  background: var(--pink-tint);
  color: var(--pink-deep);
}
.modal-head {
  padding: 18px 22px 12px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pink-deep);
  border-bottom: 1.5px solid var(--pink-tint);
}
.modal-body {
  padding: 16px 22px 20px;
  overflow-y: auto;
}
</style>
