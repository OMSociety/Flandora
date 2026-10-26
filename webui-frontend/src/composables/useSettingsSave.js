import { ref } from 'vue'
import { put } from '../api'
import { toast } from '../store'

// 设置分区的通用保存逻辑：只 PUT 本分区的字段（后端按合并保存处理）。
// 成功后短暂显示「已保存」，并按后端返回提示哪些字段要重启桥接才生效。
export function useSettingsSave() {
  const saving = ref(false)
  const saved = ref(false)
  let savedTimer = null

  async function save(payload) {
    if (saving.value) return false
    saving.value = true
    saved.value = false
    const res = await put('/api/config', payload)
    saving.value = false
    if (res.ok && res.data && res.data.ok !== false) {
      saved.value = true
      clearTimeout(savedTimer)
      savedTimer = setTimeout(() => { saved.value = false }, 3000)
      toast('配置已保存', 'success')
      const needRestart = Array.isArray(res.data.restart_required) ? res.data.restart_required : []
      if (needRestart.length) toast('部分配置需重启桥接生效：' + needRestart.join('、'), 'info', 6500)
      const ignored = Array.isArray(res.data.ignored_keys) ? res.data.ignored_keys : []
      if (ignored.length) toast('这些配置项没被识别、已忽略：' + ignored.join('、'), 'info', 8000)
      return true
    }
    toast('保存失败：' + (res.data?.error || (res.status ? `HTTP ${res.status}` : '请求失败')), 'error')
    return false
  }

  return { saving, saved, save }
}
