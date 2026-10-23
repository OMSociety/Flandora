import { ref, watch } from 'vue'

// 键名带项目旧名后缀的是上一个版本的键：读一次迁移过来，保住用户已选的主题。
const THEME_KEY = 'flandora-theme'
const LEGACY_THEME_KEY = 'webot-theme'

function readStoredTheme() {
  const current = localStorage.getItem(THEME_KEY)
  if (current === 'dark' || current === 'light') return current
  const legacy = localStorage.getItem(LEGACY_THEME_KEY)
  if (legacy === 'dark' || legacy === 'light') {
    localStorage.setItem(THEME_KEY, legacy)
    localStorage.removeItem(LEGACY_THEME_KEY)
    return legacy
  }
  return ''
}

const media = window.matchMedia('(prefers-color-scheme: dark)')
const stored = readStoredTheme()
const theme = ref(stored || (media.matches ? 'dark' : 'light'))

function applyTheme(value) {
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'dark' ? '#131713' : '#f7f9f7')
}

watch(theme, applyTheme, { immediate: true })

// 跟随系统只作用于用户尚未手动选择过的浏览器；应用是单例，监听随页面生命周期存在。
media.addEventListener('change', (event) => {
  if (!localStorage.getItem(THEME_KEY)) theme.value = event.matches ? 'dark' : 'light'
})

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem(THEME_KEY, theme.value)
  }
  return { theme, toggleTheme }
}
