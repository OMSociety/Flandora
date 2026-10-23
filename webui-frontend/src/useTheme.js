import { ref, watch } from 'vue'

const media = window.matchMedia('(prefers-color-scheme: dark)')
const stored = localStorage.getItem('webot-theme')
const theme = ref(stored === 'dark' || stored === 'light' ? stored : (media.matches ? 'dark' : 'light'))

function applyTheme(value) {
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'dark' ? '#131713' : '#f7f9f7')
}

watch(theme, applyTheme, { immediate: true })

// 跟随系统只作用于用户尚未手动选择过的浏览器；应用是单例，监听随页面生命周期存在。
media.addEventListener('change', (event) => {
  if (!localStorage.getItem('webot-theme')) theme.value = event.matches ? 'dark' : 'light'
})

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem('webot-theme', theme.value)
  }
  return { theme, toggleTheme }
}
