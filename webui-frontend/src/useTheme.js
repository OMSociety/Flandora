import { ref, watch } from 'vue'

// 用户手动选过主题就存下来，没存过则跟随系统。
const THEME_KEY = 'flandora-theme'

function readStoredTheme() {
  const current = localStorage.getItem(THEME_KEY)
  return current === 'dark' || current === 'light' ? current : ''
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
