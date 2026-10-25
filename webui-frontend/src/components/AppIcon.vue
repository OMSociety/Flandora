<script setup>
import { computed } from 'vue'
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
const props = defineProps({ name: { type: String, required: true }, size: { type: [Number, String], default: 18 } })
const icon = computed(() => {
  if (!icons[props.name]) console.warn(`[AppIcon] Unknown icon: ${props.name}`)
  return icons[props.name] || CircleAlert
})
</script>

<template>
  <component :is="icon" :size="size" :stroke-width="1.8" aria-hidden="true" class="app-icon" />
</template>
