/**
 * 浮层栈：模态框与移动端抽屉共用。
 *
 * 两个浮层可能同时存在（确认框叠在窗口列表上），所以滚动锁定必须计数，
 * Escape 只能由最顶层响应；否则先关掉的那层会恢复页面滚动，或者一次按键关掉两层。
 */
const stack = []
let lockCount = 0

export function pushOverlay(id) {
  if (stack.includes(id)) return
  stack.push(id)
  if (lockCount++ === 0) document.body.style.overflow = 'hidden'
}

export function popOverlay(id) {
  const index = stack.lastIndexOf(id)
  if (index >= 0) stack.splice(index, 1)
  if (lockCount > 0 && --lockCount === 0) document.body.style.overflow = ''
}

export function isTopOverlay(id) {
  return stack[stack.length - 1] === id
}
