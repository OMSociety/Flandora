<script setup>
import { computed } from 'vue'
const props = defineProps({ data: { type: Array, default: () => [] } })
const W = 960, H = 220, padL = 30, padR = 5, padT = 12, padB = 30
const plotW = W - padL - padR, plotH = H - padT - padB, colW = plotW / 24
const bars = computed(() => {
  const raw = (props.data || []).slice(0, 24).map((d) => ({ hour: String(d.hour ?? '').padStart(2, '0'), inV: Number(d.in) || 0, outV: Number(d.out) || 0 }))
  while (raw.length < 24) raw.push({ hour: String(raw.length).padStart(2, '0'), inV: 0, outV: 0 })
  const max = Math.max(1, ...raw.flatMap((d) => [d.inV, d.outV]))
  const items = raw.map((d, i) => {
    const x0 = padL + i * colW, offset = (colW - 19) / 2, hIn = (d.inV / max) * plotH, hOut = (d.outV / max) * plotH
    return { i, ...d, inX: x0 + offset, outX: x0 + offset + 11, inY: padT + plotH - hIn, outY: padT + plotH - hOut, hIn, hOut }
  })
  return { items, max }
})
const labels = computed(() => bars.value.items.filter((b) => b.i % 4 === 0).map((b) => ({ x: padL + b.i * colW + colW / 2, hour: b.hour })))
const grid = computed(() => Array.from({ length: 5 }, (_, g) => ({ y: padT + plotH - (g / 4) * plotH, val: Math.round(bars.value.max * (g / 4)) })))
</script>
<template><div class="chart"><div class="legend"><span class="in">接收</span><span class="out">发送</span></div><svg :viewBox="`0 0 ${W} ${H}`" class="chart-svg" role="img" aria-label="24 小时接收与发送消息数量柱状图"><line v-for="(g, i) in grid" :key="i" :x1="padL" :x2="W - padR" :y1="g.y" :y2="g.y" class="grid-line" /><text v-for="(g, i) in grid" :key="'v' + i" :x="padL - 7" :y="g.y + 3" class="axis-label" text-anchor="end">{{ g.val }}</text><g v-for="b in bars.items" :key="b.i"><rect :x="b.inX" :y="b.inY" width="8" :height="Math.max(0, b.hIn)" class="bar-in" rx="2"><title>{{ b.hour }} 时 · 接收 {{ b.inV }}</title></rect><rect :x="b.outX" :y="b.outY" width="8" :height="Math.max(0, b.hOut)" class="bar-out" rx="2"><title>{{ b.hour }} 时 · 发送 {{ b.outV }}</title></rect></g><text v-for="l in labels" :key="'h' + l.hour" :x="l.x" :y="H - 9" class="axis-label" text-anchor="middle">{{ l.hour }}:00</text></svg></div></template>
<style scoped>
.chart { width:100%; }
.legend { display:flex; justify-content:flex-end; gap:14px; font-size:10px; color:var(--muted); }
.legend span { display:flex; align-items:center; gap:5px; }
.legend span::before { content:''; width:6px; height:6px; border-radius:2px; background:var(--primary-strong); }
.legend .out::before { background:var(--border-strong); }
.chart-svg { width:100%; height:auto; margin-top:5px; overflow:visible; aspect-ratio:960/220; }
.grid-line { stroke:var(--border); stroke-width:1; }
.axis-label { font-size:9px; fill:var(--muted); font-family:var(--font); }
.bar-in { fill:var(--primary-strong); }
.bar-out { fill:var(--border-strong); }
</style>
