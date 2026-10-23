<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] }, // [{hour:'13', in, out}]
})

const W = 960
const H = 260
const padL = 34
const padR = 8
const padT = 14
const padB = 34

const plotW = W - padL - padR
const plotH = H - padT - padB
const colW = plotW / 24

const bars = computed(() => {
  const raw = (props.data || [])
    .slice(0, 24)
    .map((d) => ({
      hour: String(d.hour ?? '').padStart(2, '0'),
      inV: Number(d.in) || 0,
      outV: Number(d.out) || 0,
    }))
  while (raw.length < 24) raw.push({ hour: String(raw.length).padStart(2, '0'), inV: 0, outV: 0 })
  const max = Math.max(1, ...raw.flatMap((d) => [d.inV, d.outV]))
  const barW = 11
  const pairW = 26
  const items = raw.map((d, i) => {
    const x0 = padL + i * colW
    const offset = (colW - pairW) / 2
    const hIn = (d.inV / max) * plotH
    const hOut = (d.outV / max) * plotH
    return {
      i,
      hour: d.hour,
      inV: d.inV,
      outV: d.outV,
      inX: x0 + offset,
      outX: x0 + offset + barW + 4,
      inY: padT + plotH - hIn,
      outY: padT + plotH - hOut,
      hIn,
      hOut,
    }
  })
  return { items, max }
})

const labels = computed(() => {
  return bars.value.items
    .filter((b) => b.i % 4 === 0)
    .map((b) => ({ x: padL + b.i * colW + colW / 2, hour: b.hour }))
})

// 网格线（每 1/4 高度）
const grid = computed(() => {
  const lines = []
  for (let g = 0; g <= 4; g++) {
    const y = padT + plotH - (g / 4) * plotH
    lines.push({ y, val: Math.round(bars.value.max * (g / 4)) })
  }
  return lines
})
</script>

<template>
  <div class="chart">
    <div class="legend">
      <span class="lg in">接收</span>
      <span class="lg out">发送</span>
    </div>
    <svg :viewBox="`0 0 ${W} ${H}`" class="chart-svg" preserveAspectRatio="none">
      <line
        v-for="(g, gi) in grid"
        :key="gi"
        :x1="padL"
        :x2="W - padR"
        :y1="g.y"
        :y2="g.y"
        class="grid-line"
      />
      <text
        v-for="(g, gi) in grid"
        :key="'t' + gi"
        :x="padL - 6"
        :y="g.y + 3"
        class="y-lab"
        text-anchor="end"
      >
        {{ g.val }}
      </text>

      <g v-for="b in bars.items" :key="b.i">
        <rect :x="b.inX" :y="b.inY" :width="11" :height="Math.max(0, b.hIn)" class="bar-in" rx="2">
          <title>{{ b.hour }} 时 - 接收 {{ b.inV }}</title>
        </rect>
        <rect :x="b.outX" :y="b.outY" :width="11" :height="Math.max(0, b.hOut)" class="bar-out" rx="2">
          <title>{{ b.hour }} 时 - 发送 {{ b.outV }}</title>
        </rect>
      </g>

      <text
        v-for="l in labels"
        :key="'lab' + l.hour"
        :x="l.x"
        :y="H - 10"
        class="x-lab"
        text-anchor="middle"
      >
        {{ l.hour }}
      </text>
    </svg>
  </div>
</template>

<style scoped>
.chart {
  width: 100%;
}
.legend {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.lg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.lg::before {
  content: '';
  width: 10px;
  height: 10px;
  border-radius: 3px;
  display: inline-block;
}
.lg.in::before {
  background: var(--pink);
}
.lg.out::before {
  background: var(--blue);
}
.chart-svg {
  width: 100%;
  height: auto;
}
.grid-line {
  stroke: #f4e6ea;
  stroke-width: 1;
}
.y-lab {
  font-size: 10px;
  fill: var(--faint);
}
.x-lab {
  font-size: 10px;
  fill: var(--muted);
}
.bar-in {
  fill: var(--pink);
}
.bar-out {
  fill: var(--blue);
}
</style>
