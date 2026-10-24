export const NAV_GROUPS = [
  {
    label: '工作台',
    items: [
      { id: 'dashboard', icon: 'LayoutDashboard', label: '仪表盘', description: '服务状态与收发概览' },
      { id: 'messages', icon: 'MessagesSquare', label: '消息记录', description: '检索实时消息流' },
      { id: 'test', icon: 'Send', label: '发送测试', description: '验证微信发送通道' },
    ],
  },
  {
    label: '连接与诊断',
    items: [
      { id: 'desktop', icon: 'Monitor', label: '微信画面', description: '隔离桌面实况与操作' },
      { id: 'logs', icon: 'TerminalSquare', label: '运行日志', description: '实时事件与故障诊断' },
      { id: 'debug', icon: 'Wrench', label: '调试台', description: '直接调用动作与查看事件' },
      { id: 'wizard', icon: 'KeyRound', label: '取钥向导', description: '配置数据库与图片密钥' },
    ],
  },
  {
    label: '系统',
    items: [
      { id: 'config', icon: 'Settings2', label: '桥接配置', description: '管理连接与消息选项' },
    ],
  },
]

export const PAGE_MAP = Object.fromEntries(NAV_GROUPS.flatMap((group) => group.items.map((item) => [item.id, item])))

export function pageFromHash(hash) {
  const id = hash.replace(/^#\/?/, '')
  return PAGE_MAP[id] ? id : 'dashboard'
}
