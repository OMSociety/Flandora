import { ref, onBeforeUnmount } from 'vue'

/**
 * 隔离桌面上的微信画面。
 *
 * 取一帧要把屏幕切到微信所在的桌面再切回（见后端 `screenshot_png`），所以这里
 * 不做轮询，只在打开页面与用户点刷新时调用 `refresh()`。点击按图片的显示尺寸
 * 与原始像素比例换算成客户区坐标再转发。浏览器会一直留着上一张图的 objectURL，
 * 换图时要显式释放。
 */
export function useDesktopShot() {
  const imgUrl = ref('')
  const lastError = ref('')
  const shotAt = ref('')
  const natural = ref({ w: 0, h: 0 })

  let currentUrl = ''
  let inflight = false

  function revoke() {
    if (currentUrl) {
      URL.revokeObjectURL(currentUrl)
      currentUrl = ''
    }
  }

  function clear() {
    revoke()
    imgUrl.value = ''
    natural.value = { w: 0, h: 0 }
  }

  async function refresh() {
    if (inflight) return
    inflight = true
    try {
      const res = await fetch('/api/desktop/screenshot?t=' + Date.now(), {
        cache: 'no-store',
      })
      if (!res.ok) {
        let msg = `取画面失败 (${res.status})`
        try {
          const j = await res.json()
          if (j && j.error) msg = j.error
        } catch (e) {
          /* 响应不是 JSON，用默认文案 */
        }
        lastError.value = msg
        clear()
        return
      }
      const blob = await res.blob()
      // 后端异常时可能返回 JSON 或 HTML 错误页，直接当图会渲染成破图占位。
      if (!blob.type.startsWith('image/')) {
        lastError.value = '取画面失败：响应不是图片'
        clear()
        return
      }
      const url = URL.createObjectURL(blob)
      revoke()
      currentUrl = url
      imgUrl.value = url
      lastError.value = ''
      shotAt.value = new Date().toLocaleTimeString()
    } catch (e) {
      lastError.value = '取画面失败: ' + (e.message || e)
    } finally {
      inflight = false
    }
  }

  function onImgLoad(e) {
    natural.value = { w: e.target.naturalWidth, h: e.target.naturalHeight }
  }

  /** 把点击转发到微信窗口。返回错误文案，成功时返回空串。 */
  async function click(e) {
    const img = e.target
    if (!img.naturalWidth) return ''
    const rect = img.getBoundingClientRect()
    const scale = img.naturalWidth / rect.width
    const x = Math.round((e.clientX - rect.left) * scale)
    const y = Math.round((e.clientY - rect.top) * scale)
    const res = await fetch('/api/desktop/click', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x, y }),
    })
    if (res.ok) return ''
    let msg = `点击失败 (${res.status})`
    try {
      const j = await res.json()
      if (j && j.error) msg = j.error
    } catch (err) {
      /* 响应不是 JSON，用默认文案 */
    }
    return msg
  }

  onBeforeUnmount(revoke)

  return { imgUrl, lastError, shotAt, natural, refresh, onImgLoad, click, clear }
}
