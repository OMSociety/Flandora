<script setup>
import { watch, nextTick, onBeforeUnmount, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { pushOverlay, popOverlay, isTopOverlay } from '../overlayStack'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: Number, default: 680 },
  // 危险确认框不允许用 Escape 或点击遮罩撤销：误触关闭会让用户以为操作已取消。
  dismissible: { type: Boolean, default: true },
})
const emit = defineEmits(['close'])
const dialog = ref(null)
const overlayId = Symbol('modal')
let previousFocus = null

function requestClose() {
  if (props.dismissible) emit('close')
}

function onKeydown(event) {
  if (!isTopOverlay(overlayId)) return
  if (event.key === 'Escape') {
    event.preventDefault()
    requestClose()
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return
  const focusable = [...dialog.value.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

watch(() => props.open, async (open) => {
  if (open) {
    previousFocus = document.activeElement
    pushOverlay(overlayId)
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    dialog.value?.querySelector('button')?.focus()
  } else {
    popOverlay(overlayId)
    document.removeEventListener('keydown', onKeydown)
    previousFocus?.focus()
    previousFocus = null
  }
})

onBeforeUnmount(() => {
  popOverlay(overlayId)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-mask" @click.self="requestClose">
      <div ref="dialog" class="modal-card" role="dialog" aria-modal="true" :aria-label="title" :style="{ width: width + 'px' }">
        <header class="modal-head">
          <h2><slot name="title">{{ title }}</slot></h2>
          <button v-if="dismissible" class="icon-btn" @click="emit('close')" aria-label="关闭对话框"><AppIcon name="X" :size="19" /></button>
        </header>
        <div class="modal-body"><slot /></div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-mask { position:fixed; inset:0; background:rgb(9 18 11 / .44); display:flex; align-items:center; justify-content:center; z-index:1200; padding:24px; }
.modal-card { background:var(--card); border:1px solid var(--border); border-radius:14px; box-shadow:0 24px 70px rgb(0 0 0 / .22); max-width:100%; max-height:86dvh; display:flex; flex-direction:column; overflow:hidden; }
.modal-head { display:flex; align-items:center; justify-content:space-between; gap:15px; padding:16px 20px; border-bottom:1px solid var(--border); }
.modal-head h2 { font-size:15px; font-weight:600; color:var(--text-strong); }
.modal-body { padding:20px; overflow-y:auto; }
@media(max-width:767px) { .modal-mask { padding:14px; } .modal-card { max-height:90dvh; } .modal-head,.modal-body { padding:16px; } }
</style>
