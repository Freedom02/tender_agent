# Data Agent UI 设计规范

> 版本：v1.1 | 适用于 Data Agent 项目、关联子系统及外部接入系统
>
> 本文档定义了 Data Agent 的视觉设计语言、组件风格与交互范式，
> 供所有关联系统（管理后台、可观测平台、对话界面等）参考，以保持统一的视觉体验。

---

## 1. 技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| 前端框架 | Vue 3（Composition API，`<script setup>`） | ^3.5 |
| 构建工具 | Vite | ^5.4 |
| 语言 | TypeScript | ^5.6 |
| 组件库 | **Element Plus**（全局统一底层组件） | ^2.13 |
| 图标库 | **lucide-vue-next**（全局统一图标库） | ^1.0 |
| 图表 | ECharts 6 + vue-echarts | ^8.0 |
| 图可视化 | @antv/g6, @antv/x6 | v5 / v3 |
| 仪表盘交互 | vuedraggable + interactjs | — |
| Markdown | marked + highlight.js | ^17.0 |
| CSS 策略 | **CSS 自定义属性（CSS Variables）** + 组件 scoped style | — |

> **关键原则：本章系统不使用 Tailwind CSS / Ant Design / Bootstrap，所有样式基于 Element Plus 主题变量体系 + 自定义 CSS 变量。** 其他系统如采用不同技术栈，应提取本文档中的色值、圆角、间距等设计令牌，映射到自身组件体系中。

---

## 2. 色彩系统

系统支持浅色 / 深色双主题，通过 `html.dark` class 切换。所有颜色以 CSS 自定义属性定义，下文给出十六进制参考值。

### 2.1 主题色（Primary）

**紫蓝色系**，用于主操作按钮、链接、聚焦态、品牌标识。

| 令牌 | 浅色模式 | 深色模式 | 用途 |
|---|---|---|---|
| `primary` | `#5B3FFF` | `#7551FF` | 主按钮背景、链接色、选中态 |
| `primary-light-3` | `#7B61FF` | `#9178FF` | hover 态变体 |
| `primary-light-5` | `#A593FF` | `#B5A5FF` | 次要强调 |
| `primary-light-9` | `#F1EFFF` | `#1E1A3A` | 浅底色背景（标签、激活面板） |
| `primary-dark-2` | `#422AFB` | `#5B3FFF` | 按下态加深 |

**主按钮渐变色**：
```
linear-gradient(135deg, var(--el-color-primary), var(--el-color-primary-light-3))
```

### 2.2 功能色（Semantic Colors）

| 语义 | 色值 | 浅底色 | 用途 |
|---|---|---|---|
| **Success** 成功 | `#05CD99` | `#E6FBF5` | 完成状态、成功提示 |
| **Warning** 警告 | `#FFB547` | `#FFF8EC` | 警告、待处理 |
| **Danger** 危险 | `#EE5D50` | `#FEF0EF` | 删除、错误、失败 |
| **Info** 信息 | `#6B7A99` | `#F4F7FE` | 中性信息、次要文本 |

> 深色模式下功能色主色值保持不变，浅底色替换为深色半透明背景。

### 2.3 文本色

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `text-primary` | `#1B2559` | `#E8ECF7` | 标题、正文 |
| `text-regular` | `#2E3A6E` | `#C4CCE8` | 常规文本 |
| `text-secondary` | `#6B7A99` | `#8B94B8` | 辅助说明 |
| `text-tertiary` | `#A3AED0` | `#5E6790` | 占位符、禁用态 |

### 2.4 背景色

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `bg-color-page` | `#F4F7FE` | `#0A0B16` | 页面最底层背景 |
| `bg-color` | `#FFFFFF` | `#111322` | 卡片/面板/表格背景 |
| `bg-color-overlay` | `#FFFFFF` | `#181A2E` | 弹窗/下拉/浮层背景 |
| `fill-color` | `#F4F7FE` | `#1A1C30` | hover 填充色 |
| `fill-color-light` | `#F8FAFF` | `#1F2138` | 按钮默认背景 |
| `fill-color-lighter` | `#FFFFFF` | `#252840` | 卡片内区域分隔 |
| `fill-color-extra-light` | `#FBFCFF` | `#2A2D48` | 隔行变色、微强调 |
| `fill-color-dark` | `#E9EDF7` | `#141524` | 激活/选中态填充 |

### 2.5 边框色

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `border-color` | `#E9EDF7` | `#252840` | 默认边框 |
| `border-color-light` | `#F0F3FB` | `#1F2138` | 轻边框（表格行） |
| `border-color-dark` | `#DCE3F4` | `#363B58` | 强调边框（表头） |

### 2.6 语义色

| 令牌 | 色值 | 说明 |
|---|---|---|
| `on-primary` | `#FFFFFF` | 主色背景上的文字/图标色 |

---

## 3. 阴影系统

### 3.1 层级阴影（浅色模式）

| 令牌 | 值 | 用途 |
|---|---|---|
| `shadow-xs` | `0 2px 8px rgba(112, 144, 176, 0.06)` | 轻阴影（输入框、标签页） |
| `shadow-sm` | `0 12px 34px rgba(112, 144, 176, 0.10)` | 卡片、面板 |
| `shadow-md` | `0 18px 45px rgba(112, 144, 176, 0.16)` | 下拉菜单、Tooltip |
| `shadow-lg` | `0 24px 56px rgba(112, 144, 176, 0.18)` | 模态框 |
| `shadow-xl` | `0 32px 64px rgba(112, 144, 176, 0.22)` | 全屏浮层 |

### 3.2 品牌阴影

| 令牌 | 值 | 用途 |
|---|---|---|
| `shadow-primary` | `0 14px 24px rgba(91, 63, 255, 0.22)` | 主按钮发光 |
| `shadow-primary-hover` | `0 18px 30px rgba(91, 63, 255, 0.28)` | 主按钮 hover |

> 深色模式下阴影颜色替换为 `rgba(0, 0, 0, ...)`，透明度提升以适配暗背景。

---

## 4. 字体与排版

### 4.1 字体系列

```
Sans:  "Inter", -apple-system, BlinkMacSystemFont,
       "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif

Mono:  "JetBrains Mono", "SF Mono", Monaco,
       "Cascadia Code", monospace
```

### 4.2 字号阶梯

| 令牌 | rem | px（基准 16px） | 典型用途 |
|---|---|---|---|
| `text-xs` | `0.6875` | 11px | 状态标签、表格操作按钮 |
| `text-sm` | `0.8125` | 13px | 表格正文、辅助信息、图标按钮 |
| `text-base` | `0.9375` | 15px | 正文（全局 body 默认） |
| `text-lg` | `1.0625` | 17px | 卡片标题、强调文本 |
| `text-xl` | `1.25` | 20px | 对话框标题、面板标题 |
| `text-2xl` | `1.5` | 24px | 一级标题 |
| `text-3xl` | `1.875` | 30px | 页面大标题 |

### 4.3 字重

| 令牌 | 值 | 用途 |
|---|---|---|
| `font-normal` | `400` | 正文 |
| `font-medium` | `500` | 按钮、label、中等强调 |
| `font-semibold` | `600` | 面板标题、表头、strong |
| `font-bold` | `700` | 页面主标题 |

### 4.4 行高

| 令牌 | 值 | 用途 |
|---|---|---|
| `leading-tight` | `1.25` | 标题 |
| `leading-normal` | `1.5` | 表格、紧凑列表 |
| `leading-relaxed` | `1.625` | 正文（全局默认） |
| `leading-loose` | `2` | 长文本 |

### 4.5 全局 Body 默认

```css
body {
  font-family: var(--font-sans);
  font-size: var(--text-base);        /* 15px */
  line-height: var(--leading-relaxed); /* 1.625 */
  color: var(--el-text-color-regular);
  background: var(--el-bg-color-page);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
```

---

## 5. 间距系统

统一使用以下 7 级间距，不允许使用任意魔法数值。

| 令牌 | 值 | 典型场景 |
|---|---|---|
| `spacing-xs` | 4px | 图标-文字间距、标签内边距 |
| `spacing-sm` | 8px | 按钮组间距、小间隙 |
| `spacing-md` | 12px | 工具栏元素间距、面板内边距 |
| `spacing-lg` | 16px | 卡片内边距、表单间距、面板间距 |
| `spacing-xl` | 24px | 弹窗内边距、大区块间距、顶部导航高度 |
| `spacing-2xl` | 32px | 页面大留白 |
| `spacing-3xl` | 48px | 页面级区块分隔 |

---

## 6. 圆角系统

| 令牌 | rem | px | 典型用途 |
|---|---|---|---|
| `radius-sm` | `0.5` | 8px | 标签、小按钮、表格操作按钮 |
| `radius-md` | `0.625` | 10px | 图标按钮、骨架屏、代码块 |
| `radius-lg` | `0.75` | 12px | 表单控件、工具栏、用户卡片、弹窗按钮 |
| `radius-xl` | `0.875` | 14px | 代码块、面板 |
| `radius-2xl` | `1.125` | 18px | 卡片、弹窗、抽屉 |
| `radius-3xl` | `1.5` | 24px | 大型卡片、面板 |
| `radius-full` | `9999px` | 全圆 | 滚动条滑块、圆形头像 |

---

## 7. 过渡动画

| 令牌 | 值 | 用途 |
|---|---|---|
| `transition-fast` | `150ms cubic-bezier(0.4, 0, 0.2, 1)` | 按钮 hover、焦点态、图标变色 |
| `transition-base` | `200ms cubic-bezier(0.4, 0, 0.2, 1)` | 通用过渡 |
| `transition-slow` | `300ms cubic-bezier(0.4, 0, 0.2, 1)` | 侧边栏折叠、面板展开 |
| `transition-spring` | `500ms cubic-bezier(0.34, 1.56, 0.64, 1)` | 弹窗弹出、带弹性的入场 |

### 关键帧动画

| 动画名 | 效果 | 用途 |
|---|---|---|
| `fadeIn` | opacity 0→1 | 通用淡入 |
| `slideUp` | translateY(16px)→0 + fadeIn | 底部上滑入场 |
| `slideDown` | translateY(-16px)→0 + fadeIn | 顶部下滑入场 |
| `scaleIn` | scale(0.95)→1 + fadeIn | 弹窗/卡片弹性入场 |
| `shimmer` | 水平渐变位移 | 骨架屏加载 |
| `pulse` | opacity 1→0.5→1 | 状态指示灯 |
| `bounce` | scale 三拍跳动 | 加载点动画 |
| `spin` | rotate 0→360° | 加载旋转器 |

---

## 8. 按钮规范

### 8.1 通用按钮 `.ui-btn`

用于非 Element Plus 标准按钮的自定义场景。

```css
.ui-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 36px;
  padding: 8px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.4;
  border: 1px solid var(--el-border-color);
  background: transparent;
  color: var(--el-text-color-primary);
  cursor: pointer;
  transition: all var(--transition-fast);
}
```

hover 态：边框变主色、文字变主色。
focus-visible 态：`box-shadow: 0 0 0 3px color-mix(in srgb, var(--el-color-primary) 12%, transparent)`。
disabled 态：`opacity: 0.5; cursor: not-allowed`。

### 8.2 图标按钮 `.ui-icon-btn`

```css
.ui-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border-radius: 10px;
  border: transparent;
  color: var(--el-text-color-secondary);
}
```

hover/active：边框透明、背景 `fill-color-light`、文字变主色。

### 8.3 Element Plus 主按钮

```css
.el-button--primary {
  background: linear-gradient(135deg, var(--el-color-primary),
              var(--el-color-primary-light-3));
  box-shadow: var(--shadow-primary);
}
.el-button--primary:hover {
  box-shadow: var(--shadow-primary-hover);
}
```

---

## 9. 表单控件规范

### 9.1 输入框 / 选择框

- 边框：`1px solid var(--el-border-color)`
- 圆角：`radius-lg`（12px）
- 聚焦态：`box-shadow: 0 0 0 4px color-mix(in srgb, var(--el-color-primary) 8%, transparent)`
- 搜索框默认宽度：240px

### 9.2 表单 Label

```css
.el-form-item__label {
  font-weight: var(--font-medium);
}
```

### 9.3 工具栏 `.toolbar` / `.admin-toolbar`

```css
.toolbar, .admin-toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
  padding: var(--spacing-md);
  background: var(--el-bg-color);
  border-radius: var(--radius-lg);
  border: 1px solid var(--el-border-color);
  flex-wrap: wrap;
}
```

---

## 10. 弹窗 / 对话框规范

### 10.1 对话框 (el-dialog)

```css
.el-dialog {
  border-radius: var(--radius-2xl);        /* 18px */
  box-shadow: var(--shadow-dropdown);
  /* 垂直水平居中 */
}
.el-dialog__header {
  padding: 24px 24px 16px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.el-dialog__title {
  font-size: var(--text-xl);               /* 20px */
  font-weight: var(--font-bold);
  color: var(--el-text-color-primary);
}
.el-dialog__body {
  padding: var(--spacing-xl);              /* 24px */
}
.el-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--spacing-md);
  padding: 16px 24px;
  border-top: 1px solid var(--el-border-color-light);
}
```

### 10.2 对话框宽度级

| Class | 宽度 | 场景 |
|---|---|---|
| `.dialog-width-sm` | 500px | 简单确认、小表单 |
| `.dialog-width-md` | 650px | 标准表单 |
| `.dialog-width-lg` | 800px | 复杂表单、详情展示 |
| `.dialog-width-xl` | 950px | 大表单、多列布局 |

### 10.3 抽屉 (el-drawer)

- 圆角：`radius-2xl`（18px）仅左侧
- 标题栏：24px 内边距 + 底部分隔线
- 底部操作栏：右对齐、按钮间距 12px

---

## 11. 卡片 / 面板规范

### 11.1 管理后台卡片 `.admin-card`

```css
.admin-card {
  background: var(--el-bg-color);
  border-radius: var(--radius-2xl);       /* 18px */
  padding: var(--spacing-lg);             /* 16px */
  box-shadow: var(--shadow-card);
  margin-bottom: var(--spacing-lg);
  border: 1px solid var(--el-border-color);
}
```

### 11.2 对话面板

- 圆角：`radius-3xl`（24px）
- 内边距：16px
- 面板间距：14px
- 阴影：`shadow-card`

---

## 12. 表格规范

所有管理后台表格统一添加 `.admin-table` class。

```css
.el-table.admin-table {
  background: var(--el-bg-color);
  border-radius: var(--radius-lg);         /* 12px */
  border: 1px solid var(--el-border-color);
  box-shadow: var(--shadow-card);
}

/* 表头 */
th { height: 44px; font-weight: 600; font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.04em;
     color: var(--el-text-color-secondary);
     background: var(--el-fill-color-light);
     border-bottom: 1px solid var(--el-border-color-dark); }

/* 数据行 */
td { height: 48px; font-size: 13px;
     border-bottom: 1px solid var(--el-border-color-light); }

/* 末行无边框，hover 填充色变亮，隔行可选条纹 */
```

### 操作列

```css
.operation-cell .el-button {
  border-radius: 8px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
}
/* 链接按钮 hover 显示填充背景 */
```

---

## 13. 状态标签 Badge

四种语义状态标签，统一 class：`.status-badge`

```html
<span class="status-badge status-success">已完成</span>
<span class="status-badge status-warning">待处理</span>
<span class="status-badge status-danger">失败</span>
<span class="status-badge status-info">进行中</span>
```

```css
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);          /* 8px */
  font-size: var(--text-xs);               /* 11px */
}
.status-success { background: --success-light-9; color: --success; }
.status-warning { background: --warning-light-9; color: --warning; }
.status-danger  { background: --danger-light-9;  color: --danger; }
.status-info    { background: --info-light-9;    color: --info; }
```

> 状态圆点指示灯：6-8px 直径圆形，运行中使用 `#5B3FFF` + `pulse` 动画。

---

## 14. 空状态规范

```html
<div style="text-align:center; padding: 48px 0;">
  <div style="width:56px; height:56px; margin:0 auto 16px;
              border-radius:12px; background: var(--el-color-primary-light-9);
              display:flex; align-items:center; justify-content:center;">
    <!-- icon -->
  </div>
  <div style="font-weight:600; color: var(--el-text-color-primary);">标题</div>
  <div style="color: var(--el-text-color-secondary); font-size:13px; margin-top:4px;">
    说明文字
  </div>
</div>
```

---

## 15. 滚动条规范

```css
::-webkit-scrollbar       { width: 7px; height: 7px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--el-border-color-dark);
  border-radius: var(--radius-full);
  border: 2px solid transparent;
  background-clip: padding-box;
  min-height: 40px;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--el-text-color-placeholder);
  background-clip: padding-box;
}
/* Firefox */
* { scrollbar-width: thin;
    scrollbar-color: var(--el-border-color-dark) transparent; }
```

> Element Plus 内部滚动条（表格、下拉、选择器）宽度收窄为 6px，保持相同风格。

---

## 16. Markdown 内容区规范

对话中渲染的 Markdown 内容统一使用 `.markdown-content` class。

| 元素 | 规范 |
|---|---|
| h1 | 20px, 底部分隔线, font-semibold |
| h2 | 18px, font-semibold |
| h3 | 16px |
| h4 | 15px, text-regular |
| 正文 p | 14px, margin 12px 0, line-height 1.6 |
| 行内 code | mono 字体, 10px 圆角, fill-color-light 背景 + 边框 |
| 代码块 pre | fill-color-overlay 背景, 14px 圆角, shadow-xs, 1px 边框 |
| 引用 blockquote | 主色 3px 左边框, extra-light 填充 |
| 表格 | 无边线设计, 仅底部 border, 表头 fill-color 背景 |
| 链接 a | 主色, hover 底部出现 1px 下划线 |

---

## 17. 布局框架

### 17.1 对话布局（/chat）

三栏结构：
- **左栏**：240px 固定宽，品牌标识 + 智能体列表 + 底部用户区
- **中栏**：400px（QA 模式），会话/任务列表
- **右栏**：flex: 1，对话内容/报告/仪表盘

### 17.2 管理后台布局（/admin）

- **顶部导航**：64px 高，返回按钮 + 标题 + 标签页导航 + 工具按钮
- **左侧菜单**：220px → 可折叠至 64px（仅图标），el-menu 组件 + 折叠动画 300ms
- **内容区**：flex: 1，内边距 `spacing-lg`

### 17.3 可观测布局（/observe）

同管理后台布局模式。

### 通用规则

- 根容器 `height: 100vh / 100dvh`, `overflow: hidden`
- 固定面板 `flex-shrink: 0`，内容区 `flex: 1`
- 弹性布局 `display: flex; flex-direction: column`

---

## 18. 标签页导航（Tab Group）

```css
/* 药片形标签组 */
.tab-group {
  display: flex;
  gap: 4px;
}
.tab-item {
  padding: 6px 16px;
  border-radius: var(--radius-full);
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.tab-item.active {
  background: var(--el-color-primary);
  color: var(--on-primary);
}
```

---

## 19. 图标使用规范

| 场景 | 图标库 | 示例 |
|---|---|---|
| 后台管理（Admin） | `@element-plus/icons-vue` | Fold, Expand, Upload, Setting, DataAnalysis |
| 对话界面（Chat） | `lucide-vue-next` | MessageSquare, Brain, Globe, Download, Trash2 |

> 图标尺寸：按钮内 16-18px，独立展示 20-24px，统一 `flex-shrink: 0` 防止压缩。

---

## 20. 主题切换

- **存储 key**：`localStorage` → `"theme-preference"`（值：`"light"` | `"dark"`）
- **默认**：跟随系统 `prefers-color-scheme: dark` 媒体查询
- **切换方式**：在 `<html>` 元素添加/移除 `class="dark"`
- **防闪烁**：在 `index.html` 中内联同步脚本，在 Vue 挂载前读取 localStorage 并设置 class
- **Element Plus 暗色**：引入 `element-plus/theme-chalk/dark/css-vars.css` 配合组件级暗色适配

---

## 21. 国际化

- 库：`vue-i18n` v10
- 支持语言：`zh-CN`、`en`、`ja`、`ar`、`zh-TW`、`fr`、`de`、`it`；实际可用语言集合由发行配置决定
- 存储 key：`localStorage` → `"app-locale"`
- 切换组件：`el-config-provider` 注入 Element Plus 语言包

---

## 22. 面板激活态指示器

选中面板右侧显示 4px 宽的竖条：

```css
.active-item::after {
  content: '';
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 24px;
  border-radius: 2px;
  background: var(--el-color-primary);
}
```

---

## 23. 接入指南

对于需要参考本规范开发的新系统：

1. **复制设计令牌**：将本文档第 2 至第 7 节的 CSS 变量、色值、间距、圆角和阴影映射到目标系统。
2. **选择产品面**：开发前先确定页面属于智能工作台、管理后台、DevTools、仪表盘或登录认证，并遵守第 24 节对应蓝图。
3. **组件状态完整**：按钮、输入、菜单、卡片、表格、弹窗必须覆盖默认、hover、active、focus-visible、disabled、loading、empty、error 状态。
4. **使用 Element Plus**：若采用 Vue 3，可直接使用 Element Plus 并覆盖主题变量；其他技术栈可映射至任意组件库，不要求复制本项目的类名或文件组织。
5. **必须遵循**：色彩体系（主色 `#5B3FFF`）、圆角阶梯、间距阶梯、语义状态颜色和深浅主题支持是品牌一致性的核心。
6. **保持交互一致**：过渡动画采用第 7 节缓动函数，hover/active/focus 行为参考对应组件规范；不可逆操作必须有确认与结果反馈。
7. **验收后交付**：使用第 26 节检查清单完成多主题、多语言和多视口验证后再提交。

---

## 24. 外部系统页面蓝图

本节面向需要复用 Data Agent UI 风格的其他系统。技术栈可不同，但页面结构、密度、尺寸和层级应保持一致。不同产品面不得混用容器风格。

### 24.1 智能工作台

适用于问答、分析任务、报告和智能助手入口。

| 区域 | 规格 |
|---|---|
| 页面外层 | 全视口高度、页面背景、18px 内边距、面板间距 14px |
| 左侧导航 | 固定 240px、24px 圆角，承载品牌、能力列表、用户与快捷入口 |
| 中间列表 | 固定 400px、24px 圆角，承载会话或任务列表，并提供加载和空状态 |
| 右侧工作区 | 占满剩余宽度、24px 圆角，承载欢迎页、聊天、报告或仪表盘 |
| 选中项 | 主色浅底或浮起卡片，右侧使用 4px 主色竖向指示条 |
| 输入区 | 固定内容底部，68px 多行输入；Enter 发送，Shift+Enter 换行 |

同一屏幕只保留一个主操作。编辑、删除、更多等次级动作可放在图标工具栏、更多菜单或卡片 hover 操作中，但关键任务状态不可仅在 hover 时可见。

### 24.2 管理后台

适用于权限、配置、规则、数据源和其他高频 CRUD 页面。

| 区域 | 规格 |
|---|---|
| 顶栏 | 64px 高，24px 左右内边距，内容背景、底边框、轻阴影 |
| 一级导航 | 顶栏中的紧凑标签组，轻填充外框；激活项为主色底、白色文字 |
| 左侧菜单 | 220px，可折叠为 64px，300ms 宽度过渡 |
| 菜单项 | 44px 高，左右 8px 外边距，10px 圆角；激活项有主色浅底和左侧 3px 指示条 |
| 内容区 | 页面背景、24px 内边距、单一主纵向滚动区 |
| 页面主体 | 工具栏 + 表格或表单；仅在列表切换场景使用卡片网格 |

后台优先保证数据扫描、筛选、批量操作和长文本阅读，不使用工作台式的大圆角多栏悬浮容器。

### 24.3 DevTools / 可观测平台

适用于日志、链路诊断、自动化测试、调试与开发工具。

| 区域 | 规格 |
|---|---|
| 顶栏 | 56px 高，使用第 24.6 节的中性灰阶，不使用工作台悬浮面板 |
| 左侧栏 | 220px，可折叠为 64px；折叠后保留 44px 图标操作和 tooltip |
| 菜单项 | 13px、`8px 12px` 内边距、6px 圆角，采用灰阶 hover/active |
| 主区 | 16px 内边距；日志、时间线、结果表格各自只有一个明确滚动区 |
| 辅助工具 | AI 诊断等能力按需作为侧边工具打开，不遮挡主工具的关键状态 |

品牌主色仅用于当前语义、运行状态、关键入口和链接；其余层级通过中性背景、边框和间距表达。

### 24.4 数据仪表盘

| 对象 | 规格 |
|---|---|
| 分组区块 | 内容背景、18px 圆角、边框、卡片阴影，内边距 `20px 20px 16px` |
| 指标网格 | 12 列 Grid、102px 自动行、14px 间隙 |
| 图表网格 | 24 列 Grid、360px 自动行、14px 间隙 |
| 指标卡 | 最小 200px x 90px；标题 13px/600，关键数值 32px/700 |
| 图表卡 | 最小 360px x 260px；包含标题、图表、hover 工具栏与缩放手柄 |
| 数据回退 | 无数据、图表错误、查看数据表、查看查询定义和放大查看必须可达 |

只有具备编辑权限时才启用拖拽和缩放。尺寸改变须按网格列/行跨度保存，不能任意产生不对齐的卡片尺寸。

### 24.5 登录与认证

- 使用全屏产品相关背景媒体或静态图；自动播放媒体必须静音、循环且不影响表单可读性。
- 登录区域位于左侧，宽约 43.75%、最小 540px，使用半透明背景层；768px 以下占满宽度。
- Logo、产品名和副标题置于左上；表单宽度 350px。
- 输入框和提交按钮高度 46px，字段间距 14px；错误状态使用危险浅底、危险边框和明确错误文字。

### 24.6 DevTools 中性灰阶令牌

| 层级 | 浅色 | 深色 |
|---|---:|---:|
| 顶栏 | `#FFFFFF` | `#1A1A1A` |
| 侧栏 | `#F7F7F8` | `#212121` |
| 主区 | `#FFFFFF` | `#2A2A2A` |
| hover | `#F0F0F0` | `#3A3A3A` |
| active | `#E8E8E8` | `#404040` |
| 边框 | `#E5E5E5` | `#3A3A3A` |

---

## 25. 外部接入补充要求

### 25.1 响应式

| 视口 | 必须处理的内容 |
|---|---|
| 1440px 及以上 | 完整桌面布局、正常信息密度和工具栏排列 |
| 1024px | 弹窗最大为视口 90%；工具栏允许换行；顶栏内容不得重叠 |
| 768px | 管理卡片单列；登录区全宽；DevTools 侧栏保持图标宽度；内边距收紧 |
| 375px | 文字、表单、底部操作可读可点；多栏页面必须改为抽屉、切换或单栏，不允许横向溢出 |

固定栏使用 `flex-shrink: 0`，弹性内容区使用 `min-width: 0` 和 `min-height: 0`。长标题、字段名和表格内容必须截断、换行或提供详情入口。

### 25.2 可访问性与状态

- 纯图标按钮必须有 tooltip、`title` 或可读名称；仅装饰性图标应对辅助技术隐藏。
- 所有键盘可达控件必须提供 `focus-visible` 焦点环，建议为主色边框加 3px、12% 主色外环。
- hover 不得是编辑、删除、更多等关键操作的唯一入口。
- 颜色不是唯一状态表达。成功、警告、失败、运行中需搭配文字、标签、图标或形状。
- 图表必须提供数据表或等价文本数据视图；代码和日志必须可复制、可滚动。
- 所有异步操作必须有 loading、success、error、cancel、empty 状态；加载期间防止重复提交。

### 25.3 国际化

- 所有界面文案、按钮、状态、空状态、确认提示和错误提示必须支持国际化。
- 用户输入、业务字段、日志、代码、DSL、SQL 和服务端原始错误不翻译；可为其提供已翻译的辅助说明。
- 按最长语言文案预留空间，避免翻译后按钮、表头和菜单重叠。
- 需要支持从右到左语言时，导航、方向性图标、抽屉和对齐方式应可镜像。

---

## 26. 外部系统交付验收清单

1. 已保留并遵循第 1 节技术栈与第 2 至第 23 节的原有设计令牌、组件及主题规范；非 Vue 系统已完成等价映射。
2. 已实现浅色和深色主题，且文字、背景、边框、浮层、阴影、状态标签和图表随主题同步变化。
3. 品牌主色为 `#5B3FFF`，未引入与其竞争的页面主色或大面积装饰渐变。
4. 页面已选择第 24 节中的一种产品面，未混用工作台、管理后台和 DevTools 的容器风格。
5. 按钮、输入、菜单、卡片、表格、标签和弹窗均具备默认、hover、active、focus-visible、disabled 状态。
6. 异步场景具备加载、成功、失败、取消和空状态；删除、停止、登出等不可逆操作具备二次确认。
7. 长标题、用户名、字段名、表格文本和图表标题在窄容器内不会溢出或遮挡操作。
8. 纯图标操作具有可读名称，键盘可完成核心流程，图表可查看原始数据表。
9. 已验证 1440px、1024px、768px、375px 四个宽度，以及浅色、深色模式。
10. 已验证中文和英文的文案长度；目标系统需要的其他语言不破坏布局。
11. 视觉验收截图至少覆盖空状态、加载态、正常数据态、错误态、弹窗态和移动端关键页面。
