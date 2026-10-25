<script setup>
import { ref, onMounted } from 'vue'
import AppIcon from './AppIcon.vue'
import BrandMark from './BrandMark.vue'
import { NAV_GROUPS } from '../navigation'
import { get } from '../api'

defineProps({ active: { type: String, required: true }, collapsed: { type: Boolean, default: false } })
const emit = defineEmits(['change', 'about'])
const version = ref('')
onMounted(async () => {
  const res = await get('/api/status')
  if (res.ok && res.data && res.data.version) version.value = res.data.version
})
</script>

<template>
  <div class="sidebar-inner" :class="{ collapsed }">
    <div class="brand">
      <BrandMark :size="36" />
      <div class="brand-copy">
        <strong>Flandora</strong>
        <span>OneBot v11 微信适配器</span>
      </div>
    </div>

    <nav class="nav" aria-label="控制台导航">
      <section v-for="group in NAV_GROUPS" :key="group.label || 'standalone'" class="nav-group">
        <h2 v-if="group.label">{{ group.label }}</h2>
        <button
          v-for="item in group.items" :key="item.id" type="button"
          class="nav-item" :class="{ active: active === item.id }"
          :aria-current="active === item.id ? 'page' : undefined"
          :aria-label="item.label"
          :title="collapsed ? item.label : undefined"
          @click="emit('change', item.id)"
        >
          <span class="nav-icon"><AppIcon :name="item.icon" :size="19" /></span>
          <span class="nav-copy"><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
        </button>
      </section>
    </nav>

    <div class="sidebar-foot">
      <span class="local-dot"></span>
      <div class="foot-copy"><strong>本地服务</strong><span>Flandora<template v-if="version"> · v{{ version }}</template></span></div>
      <button type="button" class="foot-about" aria-label="查看版本与更新" title="查看版本与更新" @click="emit('about')"><AppIcon name="Info" :size="15" /></button>
    </div>
  </div>
</template>

<style scoped>
.sidebar-inner { display:flex; flex-direction:column; height:100%; width:240px; overflow:hidden; color:var(--sidebar-text); }
/* 折叠后内层必须真的收窄：外层 aside 只有 64px 且 overflow:hidden，
   若内层仍按 240px 排版，图标中心会落在裁剪区外而整排消失。 */
.sidebar-inner.collapsed { width:64px; }
.brand { display:flex; align-items:center; gap:11px; height:76px; flex-shrink:0; padding:0 20px; color:var(--primary); }
.brand svg { flex-shrink:0; }
.brand-copy { display:flex; flex-direction:column; gap:2px; white-space:nowrap; }
.brand-copy strong { font-size:15px; letter-spacing:-.025em; color:var(--text-strong); }
.brand-copy span { font-size:11px; color:var(--muted); }
.nav { flex:1; min-height:0; overflow-y:auto; padding:8px 12px; }
.nav-group { margin:0 0 22px; }
.nav-group h2 { padding:0 10px; margin:0 0 7px; font-size:11px; font-weight:500; color:var(--muted); letter-spacing:.04em; white-space:nowrap; }
.nav-item { position:relative; display:flex; align-items:center; width:100%; height:52px; padding:0; border:0; border-radius:9px; background:transparent; color:var(--muted); font:inherit; text-align:left; cursor:pointer; transition:background .18s ease,color .18s ease; }
.nav-item:hover { background:var(--sidebar-hover); color:var(--text-strong); }
.nav-item.active { background:var(--primary-soft); color:var(--primary-deep); }
.nav-item.active::before { content:''; position:absolute; left:-12px; width:3px; height:22px; border-radius:0 3px 3px 0; background:var(--primary); }
.nav-icon { display:grid; place-items:center; flex-shrink:0; width:48px; }
.nav-item.active .nav-icon { color:var(--primary); }
.nav-copy { display:flex; flex-direction:column; gap:2px; white-space:nowrap; }
.nav-copy strong { font-size:13px; font-weight:600; }
.nav-copy small { font-size:10.5px; color:var(--muted); }
.nav-item.active small { color:var(--primary-deep); opacity:.8; }
.sidebar-foot { display:flex; align-items:center; gap:11px; min-height:69px; margin:0 16px; padding:12px 8px; border-top:1px solid var(--border); }
.local-dot { width:8px; height:8px; margin-left:8px; border-radius:50%; background:var(--primary); box-shadow:0 0 0 4px var(--primary-soft); flex-shrink:0; }
.foot-copy { display:flex; flex-direction:column; gap:2px; white-space:nowrap; }
.foot-copy strong { font-size:12px; font-weight:500; }
.foot-copy span { font-size:10px; color:var(--muted); }
.foot-about { display:grid; place-items:center; width:26px; height:26px; margin-left:auto; margin-right:4px; padding:0; border:0; border-radius:7px; background:transparent; color:var(--muted); cursor:pointer; transition:background .18s ease,color .18s ease; }
.foot-about:hover { background:var(--sidebar-hover); color:var(--primary-deep); }
.collapsed .nav-item { height:46px; justify-content:center; padding:0; transition:background .18s ease,color .18s ease; }
.collapsed .nav-copy, .collapsed .foot-copy, .collapsed .brand-copy { display:none; }
.collapsed .nav-icon { width:auto; }
.collapsed .nav-group { margin-bottom:12px; }
.collapsed .nav-group h2 { height:0; margin:0; overflow:hidden; opacity:0; }
.collapsed .brand { justify-content:center; padding:0; }
.collapsed .nav { padding-inline:8px; }
.collapsed .nav-item.active::before { display:none; }
.collapsed .sidebar-foot { min-height:56px; padding:10px 0; margin-inline:12px; justify-content:center; }
.collapsed .local-dot { margin-left:0; }
.collapsed .foot-about { margin:0; }
@media(max-width:767px) { .sidebar-inner { width:260px; } .brand { height:76px; } .nav { padding-inline:14px; } }
</style>
