import Dashboard from './Dashboard.vue'
import Messages from './Messages.vue'
import Nodes from './Nodes.vue'
import Desktop from './Desktop.vue'
import Wizard from './Wizard.vue'
import Logs from './Logs.vue'
import DevPanel from './DevPanel.vue'
import Config from './Config.vue'

/**
 * 面板页面的唯一登记处：导航、路由解析与页面渲染都从这张表派生。
 *
 * 新增页面只需在 `src/pages/` 写组件，然后在这里加一条；没有第二处要同步。
 * 字段约定：
 * - `icon` 必须是 `components/AppIcon.vue` 白名单里的名字。写错的后果是那个位置
 *   静默显示成警告图标（构建不报错），所以 e2e 用例直接核对这份名单。
 * - `group` 相同的项归入同一个侧栏分组，分组按首次出现的顺序排列；`group: ''`
 *   不显示分组标题，按约定写在数组末尾、落在侧栏最下面。
 * - `menu: false` 表示只按 `#/id` 进入、不进侧栏与导航。
 * - `home: true` 是未知或空 hash 的兜底页。
 * - `id` 同时是路由段（`#/<id>`）、`PAGE_MAP` 的键与 `<component :is>` 的取值依据。
 */
export const PAGES = [
  {
    id: 'dashboard',
    group: '工作台',
    label: '仪表盘',
    icon: 'LayoutDashboard',
    description: '服务状态与收发概览',
    component: Dashboard,
    menu: true,
    home: true,
  },
  {
    id: 'messages',
    group: '工作台',
    label: '消息记录',
    icon: 'MessagesSquare',
    description: '检索实时消息流',
    component: Messages,
    menu: true,
  },
  {
    id: 'nodes',
    group: '连接与诊断',
    label: '节点配置',
    icon: 'Cable',
    description: '增删 OneBot 入口',
    component: Nodes,
    menu: true,
  },
  {
    id: 'desktop',
    group: '连接与诊断',
    label: '微信画面',
    icon: 'Monitor',
    description: '隔离桌面实况与操作',
    component: Desktop,
    menu: true,
  },
  {
    id: 'wizard',
    group: '连接与诊断',
    label: '取钥向导',
    icon: 'KeyRound',
    description: '配置数据库与图片密钥',
    component: Wizard,
    menu: true,
  },
  {
    id: 'logs',
    group: '连接与诊断',
    label: '运行日志',
    icon: 'TerminalSquare',
    description: '实时事件与故障诊断',
    component: Logs,
    menu: true,
  },
  {
    id: 'dev',
    group: '连接与诊断',
    label: '调试面板',
    icon: 'Wrench',
    description: '测试发送与动作调试',
    component: DevPanel,
    menu: true,
  },
  {
    id: 'config',
    group: '',
    label: '系统设置',
    icon: 'Settings2',
    description: '桥接选项、面板与更新',
    component: Config,
    menu: true,
  },
]

export const PAGE_MAP = Object.fromEntries(PAGES.map((page) => [page.id, page]))

/** 侧栏分组：顺序与 `PAGES` 一致，按 `group` 首次出现的位置定组序。 */
export const NAV_GROUPS = PAGES.filter((page) => page.menu !== false).reduce((groups, page) => {
  const group = groups.find((item) => item.label === page.group)
  if (group) group.items.push(page)
  else groups.push({ label: page.group, items: [page] })
  return groups
}, [])

const HOME_ID = (PAGES.find((page) => page.home) || PAGES[0]).id

/** 把 `#/messages?x=1` 这类 hash 解析成页面 id；未知 id 回首页。 */
export function pageFromHash(hash) {
  const id = hash.replace(/^#\/?/, '').split('?')[0]
  return PAGE_MAP[id] ? id : HOME_ID
}
