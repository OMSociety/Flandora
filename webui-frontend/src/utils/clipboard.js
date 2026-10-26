// 复制一段文本到剪贴板。
//
// 面板可能以明文 HTTP 提供（管理界面里把绑定地址放开到 0.0.0.0 就是这种情形），
// 而浏览器只在安全上下文里给 navigator.clipboard：非回环的 http 页面拿到的是
// undefined，直接 await 会抛 TypeError，把浏览器内部的话术端到用户面前。
// 所以这里先试标准接口，再退回到老办法，两条都不行就交给调用方提示「请手动选中」。
export async function copyText(text) {
  const value = String(text ?? '')
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // 没给权限或没有用户手势：再试一次老办法，仍失败就算失败。
    }
  }
  return execCommandCopy(value)
}

function execCommandCopy(value) {
  const area = document.createElement('textarea')
  area.value = value
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  area.style.opacity = '0'
  document.body.appendChild(area)
  // 选中会清掉用户原本的选择，复制完还回去。
  const selection = document.getSelection()
  const saved = selection && selection.rangeCount ? selection.getRangeAt(0) : null
  let ok = false
  try {
    area.select()
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(area)
  if (saved && selection) {
    selection.removeAllRanges()
    selection.addRange(saved)
  }
  return ok
}
