import { onBeforeUnmount, ref } from 'vue'

/**
 * 未保存改动的离开守卫。
 *
 * 状态放在模块级：`App.vue` 的导航拦截与页面组件（目前是节点配置）登记脏值的
 * 是两个组件，必须看到同一份事实，否则一边判断干净、另一边还在编辑。
 * 页面卸载时由 `useDirtyGuard` 自清，避免上一个页面的脏值拦住下一次导航。
 * 浮层栈由确认框自己（`Modal`）管，这里只管「要不要拦」与「用户选了哪边」。
 */

/** 当前页面是否有未保存的改动。 */
export const dirty = ref(false)

/** 确认框是否打开，由 `App.vue` 渲染的 ConfirmDialog 消费。 */
export const leavePromptOpen = ref(false)

let pendingResolve = null
let pendingPromise = null
let unloadBound = false

function onBeforeUnload(event) {
  if (!dirty.value) return
  // 原生提示只负责拦住刷新与关标签页；它的文案改不了，站内导航走确认框。
  event.preventDefault()
  event.returnValue = ''
  return ''
}

function bindUnload() {
  if (unloadBound) return
  window.addEventListener('beforeunload', onBeforeUnload)
  unloadBound = true
}

function unbindUnload() {
  if (!unloadBound) return
  window.removeEventListener('beforeunload', onBeforeUnload)
  unloadBound = false
}

function closePrompt(leave) {
  const resolve = pendingResolve
  pendingResolve = null
  pendingPromise = null
  if (leavePromptOpen.value) {
    leavePromptOpen.value = false
  }
  if (resolve) resolve(leave)
}

/** 登记「当前页面有未保存的改动」。 */
export function markDirty() {
  dirty.value = true
  bindUnload()
}

/**
 * 清除脏值：保存成功或页面卸载时调用。
 * 未决的离开请求按「可以走」结束——此时已经没有会丢的改动了。
 */
export function clearDirty() {
  dirty.value = false
  unbindUnload()
  closePrompt(true)
}

/**
 * 现在能不能离开当前页面。有脏值时弹确认框，返回用户的选择；
 * 重复调用复用同一个待决 Promise，连点导航不会叠出两个确认框。
 */
export function canLeave() {
  if (!dirty.value) return Promise.resolve(true)
  if (pendingPromise) return pendingPromise
  leavePromptOpen.value = true
  pendingPromise = new Promise((resolve) => { pendingResolve = resolve })
  return pendingPromise
}

/** 确认框回调：`true` 放弃改动并离开，`false` 留在本页。 */
export function resolveLeave(leave) {
  closePrompt(leave)
}

/** 页面组件用它登记与清除自己的脏值；组件卸载时自动清掉。 */
export function useDirtyGuard() {
  onBeforeUnmount(clearDirty)
  return { dirty, markDirty, clearDirty, canLeave }
}
