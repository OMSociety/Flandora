export const NAV_GROUPS = [
  {
    label: '工作台',
    items: [
      { id: 'dashboard', icon: 'LayoutDashboard', label: '仪表盘', description: '服务状态与收发概览' },
      { id: 'messages', icon: 'MessagesSquare', label: '消息记录', description: '检索实时消息流' },
    ],
  },
  {
    label: '连接与诊断',
    items: [
      { id: 'nodes', icon: 'Cable', label: '节点配置', description: '增删 OneBot 入口' },
      { id: 'desktop', icon: 'Monitor', label: '微信画面', description: '隔离桌面实况与操作' },
      { id: 'wizard', icon: 'KeyRound', label: '取钥向导', description: '配置数据库与图片密钥' },
      { id: 'logs', icon: 'TerminalSquare', label: '运行日志', description: '实时事件与故障诊断' },
      { id: 'dev', icon: 'Wrench', label: '调试面板', description: '测试发送与动作调试' },
    ],
  },
  {
    label: '',
    items: [
      { id: 'config', icon: 'Settings2', label: '系统设置', description: '桥接选项、面板与更新' },
    ],
  },
]

export const PAGE_MAP = Object.fromEntries(NAV_GROUPS.flatMap((group) => group.items.map((item) => [item.id, item])))

export function pageFromHash(hash) {
  const id = hash.replace(/^#\/?/, '').split('?')[0]
  return PAGE_MAP[id] ? id : 'dashboard'
}
