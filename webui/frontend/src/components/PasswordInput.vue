<script setup>
import { ref } from 'vue'

defineProps({
  modelValue: { type: [String, Number], default: '' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const show = ref(false)

function onInput(e) {
  emit('update:modelValue', e.target.value)
}
</script>

<template>
  <div class="pw">
    <input
      :type="show ? 'text' : 'password'"
      class="input"
      :placeholder="placeholder"
      :disabled="disabled"
      :value="modelValue"
      @input="onInput"
      autocomplete="off"
    />
    <button type="button" class="pw-toggle" @click="show = !show" tabindex="-1">
      {{ show ? '🙈' : '👁' }}
    </button>
  </div>
</template>

<style scoped>
.pw {
  position: relative;
  width: 100%;
}
.pw .input {
  padding-right: 38px;
}
.pw-toggle {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  opacity: 0.55;
  padding: 4px;
}
.pw-toggle:hover {
  opacity: 1;
}
</style>
