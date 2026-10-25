<script>
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Blocks, Cable, Check, CheckCheck,
  ChevronRight, CircleAlert, CircleCheck, CircleX, Clock3, Copy, Database, Download, Eraser, Eye, EyeOff,
  FileText, Info, KeyRound, LayoutDashboard, Lock, LogIn, LoaderCircle, Menu, MessageCircle,
  MessagesSquare, Minimize2, Monitor, Moon, MoreHorizontal, Pause, Pencil, Play, Plug,
  PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, RotateCcw, Search, Send, Server, Settings2, ShieldCheck, SlidersHorizontal,
  Square, Sun, TerminalSquare, Trash2, Upload, Wrench, X, Zap,
} from '@lucide/vue'

const icons = {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Blocks, Cable, Check, CheckCheck,
  ChevronRight, CircleAlert, CircleCheck, CircleX, Clock3, Copy, Database, Download, Eraser, Eye, EyeOff,
  FileText, Info, KeyRound, LayoutDashboard, Lock, LogIn, LoaderCircle, Menu, MessageCircle,
  MessagesSquare, Minimize2, Monitor, Moon, MoreHorizontal, Pause, Pencil, Play, Plug,
  PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, RotateCcw, Search, Send, Server, Settings2, ShieldCheck, SlidersHorizontal,
  Square, Sun, TerminalSquare, Trash2, Upload, Wrench, X, Zap,
}

/**
 * 图标白名单。页面登记表（`src/pages/index.js`）里的 icon 必须在这里，
 * 未登记的名字只会让那个位置显示成警告图标，构建与类型都不报错，
 * 因此 e2e 用例拿这份名单核对登记表。
 */
export const ICON_NAMES = new Set(Object.keys(icons))
</script>

<script setup>
import { computed } from 'vue'

const props = defineProps({ name: { type: String, required: true }, size: { type: [Number, String], default: 18 } })
const icon = computed(() => {
  if (!icons[props.name]) console.warn(`[AppIcon] Unknown icon: ${props.name}`)
  return icons[props.name] || icons.CircleAlert
})
</script>

<template>
  <component :is="icon" :size="size" :stroke-width="1.8" aria-hidden="true" class="app-icon" />
</template>
