<script setup>
import { computed } from 'vue'
import {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Check, CheckCheck,
  ChevronRight, CircleAlert, CircleCheck, CircleX, Clock3, Eye, EyeOff,
  FileText, Info, KeyRound, LayoutDashboard, LogIn, LoaderCircle, Menu, MessageCircle,
  MessagesSquare, Minimize2, Monitor, Moon, MoreHorizontal, Pause, Play,
  PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, RotateCcw, Search, Send, Settings2, ShieldCheck, SlidersHorizontal,
  Square, Sun, TerminalSquare, Trash2, X, Zap,
} from '@lucide/vue'

const icons = {
  Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Check, CheckCheck,
  ChevronRight, CircleAlert, CircleCheck, CircleX, Clock3, Eye, EyeOff,
  FileText, Info, KeyRound, LayoutDashboard, LogIn, LoaderCircle, Menu, MessageCircle,
  MessagesSquare, Minimize2, Monitor, Moon, MoreHorizontal, Pause, Play,
  PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, RotateCcw, Search, Send, Settings2, ShieldCheck, SlidersHorizontal,
  Square, Sun, TerminalSquare, Trash2, X, Zap,
}
const props = defineProps({ name: { type: String, required: true }, size: { type: [Number, String], default: 18 } })
const icon = computed(() => {
  if (!icons[props.name]) console.warn(`[AppIcon] Unknown icon: ${props.name}`)
  return icons[props.name] || CircleAlert
})
</script>

<template>
  <component :is="icon" :size="size" :stroke-width="1.8" aria-hidden="true" class="app-icon" />
</template>
