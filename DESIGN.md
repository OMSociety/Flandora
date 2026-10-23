---
name: Flandora
description: 微信与 MaiBot 桥接控制台，微信绿 + 白/黑明暗主题的运维界面
colors:
  primary: "#07c160"
  primary-strong: "#0b7f46"
  primary-deep: "#087f45"
  primary-soft: "#e7f7ee"
  text: "#30352f"
  text-strong: "#171b18"
  muted: "#5f6861"
  faint: "#67706a"
  border: "#e4e8e4"
  border-strong: "#d7ded8"
  card: "#ffffff"
  canvas: "#f7f9f7"
  sidebar: "#f0f3f0"
  inset: "#f4f6f4"
  success: "#06783e"
  warning: "#8a5200"
  danger: "#c03434"
  info: "#2a6a96"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "25px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.035em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.55
    letterSpacing: "normal"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.7
    letterSpacing: "normal"
  mono:
    fontFamily: "'Cascadia Code', 'SFMono-Regular', Consolas, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.75
    letterSpacing: "normal"
rounded:
  sm: "8px"
  md: "12px"
  lg: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "30px"
components:
  button-primary:
    backgroundColor: "{colors.primary-strong}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "7px 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "#0a6d3d"
  button-outline:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "5px 11px"
  card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.md}"
    padding: "20px"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text-strong}"
    rounded: "{rounded.sm}"
    height: "38px"
    padding: "9px 11px"
---

# Design System: Flandora

## Overview

**Creative North Star: "微信原生运维台"**

界面沿用微信桌面客户端的色彩语法：中性的浅灰绿工作区、白色内容面、实心微信绿作为唯一强调色。所有强调色都用来标示「当前在哪」与「当前能否操作」，不用来装饰。信息密度偏高，靠近系统设置面板而不是营销页面：卡片只用一层浅阴影与细边框分层，标题与正文的层级靠字号、字重和留白建立，不靠颜色和渐变。

**Key Characteristics:**
- 单一强调色，绿色只出现在选中、主操作、在线状态和进度上
- 冷白内容面叠在浅灰导航底上，靠色调差而非边框分隔区域
- 密集中性的数据行，配合等宽数字对齐
- 明暗两套主题共用同一套语义变量，跟随系统并可手动覆盖

## Colors

微信绿主导、中性灰绿承托的克制配色。

### Primary

- **微信绿** (#07c160)：品牌标记、进度填充、图表接收柱、在线状态点。
- **深微信绿** (#0b7f46)：浅色主题下按钮底色与焦点描边的对比度版本，保证白字达到 4.5:1。
- **深绿文字** (#087f45)：选中项文字与图标、链接、次级强调。

### Neutral

- **正文绿黑** (#30352f) / **强调绿黑** (#171b18)：正文与标题，带极轻微绿调以贴合整体色温。
- **次要文字** (#5f6861) / **弱化文字** (#67706a)：说明文字与辅助标签，在卡片、画布与内嵌底上均达到 4.5:1。
- **细边框** (#e4e8e4) / **强边框** (#d7ded8)：卡片描边与输入框描边。
- **画布** (#f7f9f7) / **导航底** (#f0f3f0) / **内嵌底** (#f4f6f4)：三层中性底色。

### Named Rules

**The One Green Rule.** 每屏只有一个强调色，且只用于表达状态或下一步操作；装饰性着色一律退回中性灰绿。

## Typography

**Display Font:** 系统无衬线栈（含 PingFang SC / Microsoft YaHei）
**Body Font:** 同上
**Label/Mono Font:** Cascadia Code / Consolas

**Character:** 界面文字保持系统 UI 的无个性与高清晰度，让数字与状态成为视觉主体；等宽字体只用于句柄、端口、PID、时间戳等真实数据。

### Hierarchy

- **Display** (650, 25px, 1.3, -0.035em)：页面开头的单句引导语，全站每页只出现一次。
- **Title** (600, 14px)：卡片标题与页头页面名。
- **Body** (400, 13px, 1.55)：正文与表单值。
- **Label** (500, 11px)：字段名、状态徽标、辅助说明。
- **Mono** (400, 11px)：日志、时间、端口、句柄、PID。

## Layout

固定 240px 侧栏加内嵌画布，画布左上圆角 14px 与侧栏分开。内容区居中并限制最大宽度 1440px，页内边距桌面 26/30px、移动 20/16px。栅格以 1fr 自适应为主，仪表盘为 1.75fr 主列加 1fr 侧列，四个状态块等分。断点 1100px 收起说明文字并降为两列，767px 切换为抽屉导航与单列。

## Elevation & Depth

混合方式：导航层与画布层靠色调差分层，不用阴影；卡片用一层极浅的环境阴影（`0 1px 2px` 加长扩散的淡投影）分离边缘，不使用悬浮式重阴影。多数数据卡片显式关闭阴影（`box-shadow:none`），只有需要抢注意力的面板保留。

### Named Rules

**The Flat-Data Rule.** 数据面板与配置分组保持近平，阴影只留给真正浮起的内容，例如模态框与保存栏。

## Shapes

圆角随尺度递进：按钮与输入 8px，卡片 12px，画布与模态框 14px，徽标与标签 5px，圆形只用于状态点与步骤序号。边框统一 1px，聚焦用 2px 描边加 3px 外扩，不用发光。

## Components

### Buttons

- **Shape:** 8px 圆角，最小高度 36px（移动 40px）。
- **Primary:** 深微信绿底、白字，7px 14px 内边距。
- **Hover / Focus:** 底色加深一档；键盘焦点为 2px 深绿描边外扩 3px。
- **Secondary / Ghost:** 白底细边框或纯文字，用于次要与取消动作；危险动作用红描边，删除类用实心红。

### Chips

- **Style:** 浅绿底、深绿字、6px 圆角，带可点击的移除按钮。
- **State:** 移除按钮悬停时转为深绿底白字。

### Cards / Containers

- **Corner Style:** 12px。
- **Background:** 白色内容面，内嵌区域用 #f4f6f4。
- **Shadow Strategy:** 见 Elevation & Depth。
- **Border:** 1px #e4e8e4。
- **Internal Padding:** 20px，移动 16px。

### Inputs / Fields

- **Style:** 白底、1px 强边框、8px 圆角，最小高度 38px（移动 42px）。
- **Focus:** 边框转深绿并加 3px 浅绿光晕。
- **Error / Disabled:** 禁用态改内嵌底色与次要文字；错误以整块红色提示条呈现在对应区域上方。

### Navigation

侧栏按「工作台 / 连接与诊断 / 系统」分组，每项含图标、名称与一句说明。选中态为浅绿实底加左侧 3px 绿条，悬停为浅灰绿。桌面可折叠为 64px 图标栏，移动端改为抽屉并支持 Escape 关闭。

### Confirmation Dialog

破坏性操作（重启、结束微信、重置向导）统一使用应用内模态：白底卡片、标题、说明与右对齐的取消/确认按钮，确认按钮为实心红。模态框限制焦点在内部并响应 Escape。

## Do's and Don'ts

### Do:

- **Do** 用绿色表达「状态」与「下一步」，一屏只有一个主操作。
- **Do** 数值、端口、句柄、时间统一使用等宽数字对齐。
- **Do** 每个空状态给出下一步该做什么。
- **Do** 破坏性操作走应用内确认弹窗，并说明后果。
- **Do** 明暗两套主题都通过语义变量取值，不在组件里写死颜色。

### Don't:

- **Don't** 用渐变文字、彩色左边框或发光阴影表达层级。
- **Don't** 用 emoji 充当图标；图标统一取 Lucide 线条图标，线宽 1.8。
- **Don't** 把强调绿铺在大面积背景上；绿色面积超过一屏的三分之一即视为失衡。
- **Don't** 在浅色主题用 #07c160 作按钮底色承载白字，它达不到 4.5:1；按钮用 #0b7f46。
