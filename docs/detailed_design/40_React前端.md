# React 前端

**源码**：[`frontend/src/`](../../frontend/src/)

| 目录/文件 | 职责 |
|---|---|
| [`App.tsx`](../../frontend/src/App.tsx) | 左侧导航 + 右侧工作区的外壳，`wouter` 路由表（§2） |
| [`api/client.ts`](../../frontend/src/api/client.ts) | 基于生成的 OpenAPI 类型（`api/schema.d.ts`）的类型安全 API 客户端 |
| [`components/image-canvas/`](../../frontend/src/components/image-canvas/) | 与具体任务无关的共享画布图元（§5） |
| [`components/workspace/`](../../frontend/src/components/workspace/) | 全部任务页共用的工作台布局、页面级快捷键（`useHotkeys`）与类别配色（§6） |
| [`components/shell/`](../../frontend/src/components/shell/) | 侧边导航（`SideNav`）、新建项目的任务类型弹窗、全局项目列表上下文（`ProjectsContext`）、线条图标 |
| [`components/settings/`](../../frontend/src/components/settings/) | 按后端字段描述（`/api/task-types` 的 `fields`）渲染的通用属性表单 |
| [`components/AsyncState.tsx`](../../frontend/src/components/AsyncState.tsx) | 通用加载中/错误态展示组件 |
| [`project-meta.ts`](../../frontend/src/project-meta.ts) | 状态/任务类型的中文名、任务入口路径、由 `summary` 计算进度 |
| [`pages/`](../../frontend/src/pages/) | 首页（新建项目入口）、新建项目属性页、画布 demo |
| [`pages/project/`](../../frontend/src/pages/project/) | 项目 dashboard：概览、导入数据、属性、导出 |
| [`tasks/<type>/`](../../frontend/src/tasks/) | 每种任务类型一个目录，一个 `*ReviewPage.tsx` |
| [`tasks/useImageSize.ts`](../../frontend/src/tasks/useImageSize.ts) | 检测/分割/深度共用：提交前用一张隐藏 `Image` 预探测图片像素尺寸 |

**运行进程**：浏览器；开发时由 Vite 提供，生产构建产物可由后端通过
`ANNOTATION_FRONTEND_DIST` 同源托管（Docker 镜像即如此），也可由任意静态文件服务器
托管，见 [`90_部署与运维.md`](90_部署与运维.md)。

## 1. 职责

把各任务类型的标注交互统一到"读队列 → 在画布/表单里产出一个 `result` → 提交 →
读下一条"这一个循环里，同时让检测/分割/深度三个基于画布的任务类型共享同一套坐标
变换、图层合成和像素编辑图元，不各自维护一份。前端**不**做任何服务端已经做的
校验重复实现——`result` 的合法性以后端 422/409 响应为准，前端校验只是提前拦截
明显无效的输入以改善体验。

## 2. 页面结构与路由

页面左右分割：左侧窄导航（可收起为图标栏，窄屏下固定为图标栏）放"新建项目"按钮和
项目列表，当前项目下展开"概览 / 导入数据 / 标注 / 属性 / 导出"；右侧为工作区。
项目只能在界面中创建和管理（后端工作区模型见 [`80_配置参考.md`](80_配置参考.md)）。

| 路径 | 页面 |
|---|---|
| `/` | 首页：只有"新建项目"入口，项目从导航进入 |
| （弹窗） | 选择标注任务类型，确认后进入 `/new/<type>` |
| `/new/:taskType` | 新建项目属性页：名称 + 该任务类型的字段，创建后进入概览 |
| `/projects/:id` | 概览（dashboard）：进度、下一步操作卡片、任务摘要；ReID 另有流水线动作 |
| `/projects/:id/import` | 导入数据：上传文件/文件夹/ZIP（托管目录，可接受的扩展名来自 `upload_extensions`），或关联服务器目录 |
| `/projects/:id/settings` | 属性：字段表单、直接编辑配置文件、删除项目 |
| `/projects/:id/export` | 导出：按任务类型可用的格式下载 |
| `/projects/:id/classify` 等 | 标注页面（`/classify`、`/caption`、`/spans`、`/detect`、`/segment`、`/depth`、`/review`），全宽布局 |

属性表单不在前端硬编码字段：`SettingsForm` 按 `/api/task-types` 返回的 `fields`
渲染，字段的 `lock`（已有标注后 `append_only`/`locked`）与 `server_only`（只读）
在前端只用于提示和禁用，最终由后端校验。

`task_type` 到路由路径、名称与入口文案的映射只在 `project-meta.ts` 的
`taskEntries` 定义一次：导航、概览页的入口链接和 `App.tsx` 的标注页路由都由它生成。
每种任务的页面组件登记在 `tasks/pages.ts` 的 `taskPages`，它以 `TaskType`
（`taskEntries` 的键）为键，漏登记或多登记都无法通过类型检查。新增任务类型 = 在
`taskEntries` 加一行 + 在 `taskPages` 登记页面。

## 3. 接口一览

前端只消费 [`70_外部接口.md`](70_外部接口.md) 描述的通用端点
（`queue`/`annotations`/`files`/`actions`，以及项目管理的 `task-types`/`settings`/
`config`/`import`/`export`），不存在任务类型专属的 HTTP 端点；
`api/client.ts` 的函数签名对全部任务类型通用，`result`/`summary` 字段类型是
`Record<string, unknown>`。

## 4. 核心流程：一个任务页面的标准循环

```mermaid
flowchart LR
    Load["加载 project + queue<br/>（useEffect）"] --> Item{"queue.items[0]<br/>存在？"}
    Item -- 否 --> Done["渲染“已完成”态"]
    Item -- 是 --> Render["渲染标注界面<br/>（表单或 ImageCanvas）"]
    Render --> Submit["submitAnnotation()"]
    Submit -- 成功 --> Reload["重新 getQueue()"]
    Submit -- 失败 --> ShowError["展示错误 + 重试按钮"]
    Reload --> Item
    ShowError --> Render
```

这个循环只实现一次：`tasks/useTaskQueue.tsx` 的 `useTaskQueue(projectId, { taskType,
wrongType, onLoad })` 并行读取项目与待处理队列、拒绝其他任务类型的项目、在提交成功后
重新读取队列，并持有 `submitting`/`submitError`；`QueueFallback` 渲染加载、错误（可
重试）与"已完成"三种状态；`itemText`、`summaryStrings` 是读取队列项与项目摘要的共享
helper。各任务页只负责自己的标注状态、`onLoad` 时的重置与提交的 `result` 形状。

`ClassificationReviewPage.tsx`/`CaptionReviewPage.tsx` 没有画布；
`DetectionReviewPage.tsx`/`SegmentationReviewPage.tsx`/`DepthReviewPage.tsx` 在
"渲染标注界面"这一步额外挂载 `ImageCanvas` + 对应的画布图元。

## 5. 共享图像画布图元（`components/image-canvas/`）

坐标系、图层合成、指针事件和快捷键的完整契约见该目录自己的
[`README.md`](../../frontend/src/components/image-canvas/README.md)（本文档不重复，
理由见 [`00_概述.md`](00_概述.md) §1.1）。三个画布任务各自复用的图元：

| 图元 | 被谁用 | 关键点 |
|---|---|---|
| `geometry.ts` / `shortcuts.ts` | 全部三个画布任务 | 视口缩放/平移的纯函数，快捷键绑定与冲突检测 |
| `box-tool.ts` | 检测 | 创建/选中/移动/8 向 handle 缩放/删除检测框的纯函数状态机 |
| `polygon-tool.ts` | 分割 | 顶点增删/闭合/撤销的纯状态机，只管矢量顶点 |
| `raster-brush.ts` | 分割 + 深度 | `drawRaster`（把栅格画进图层）与 `useRasterBrush`（画笔指针状态机：按下盖章、拖动连线、抬起结束），两页共用同一份实现 |
| `raster-buffer.ts` | 分割 + 深度 | 可原地绘制的 `Uint8ClampedArray` 像素缓冲区：`stampAt`/`strokeSegment`（画笔）、`fillPolygon`（多边形栅格化）、`toBase64`（提交）、`loadFromImageElement`/`toImageData`（读取已有 PNG、渲染预览） |

`polygon-tool.ts` 产出的多边形闭合后会立即调用 `raster-buffer.ts` 的 `fillPolygon`
栅格化进同一张像素缓冲区——**多边形只是画布上的一种作画方式，不是提交给后端的
第二种数据表示**（后端只接受整图栅格，见 [`20_标注平台后端.md`](20_标注平台后端.md) §5）。
`raster-buffer.ts` 的写操作原地修改数据而不是返回新对象，性能考量见
[`10_通用设计.md`](10_通用设计.md) §4。

深度任务额外需要 `loadFromImageElement` 从已获取的基线深度图水合初始像素缓冲区；
该图片元素必须设置 `crossOrigin = "anonymous"`，否则前后端不同源时画布会被浏览器
标记为"污染"、无法读取像素，见 [`10_通用设计.md`](10_通用设计.md) §3。

## 6. 标注工作台（`components/workspace/`）

所有任务页（含 ReID 审核）都渲染同一个 `TaskWorkspace`：

| 区域 | 内容 |
|---|---|
| 顶栏（工作区内） | 返回项目、任务名、当前文件名、进度条（`已完成 / 总数`）、剩余数（`.queue-count`） |
| 舞台 | 画布、图片或文本，占满工作区剩余高度，页面本身不滚动；文本条目（`media: "text"`）由 `tasks/ItemStage.tsx` 的 `TextDocument` 显示为可在舞台内滚动的文档（保留空白与换行），`text_error` 原样显示 |
| 侧栏 | 类别/工具等面板（`PanelSection`、`OptionList`、`Segmented`、`RangeField`），可折叠的快捷键列表；底部固定 `SubmitBar`（保存按钮、禁用原因、保存失败与重试） |

进度由 `project-meta.ts` 的 `summaryProgress()` 计算：图像任务取 `summary.total`，
ReID 取 `labelled + pending`；已完成数 = 总数 − 当前队列 `total`（队列在每次提交后
刷新，`summary` 只在进入页面时读取一次）。

**页面级快捷键**（`useHotkeys`）挂在 `window` 上，不要求画布聚焦；在文本输入框中
不触发（`allowInText` 的绑定除外），聚焦按钮时不拦截 Enter/空格。`ImageCanvas` 自带的
缩放/平移键（`+`/`-`/`0`/方向键/空格拖动）仍只在画布聚焦时生效。

| 页面 | 快捷键 |
|---|---|
| 全部（ReID 除外） | `Ctrl/⌘ + Enter` 保存并继续（描述页在输入框内也可用） |
| 分类 | `1`–`9` 选择/切换第 N 个标签 |
| 文本片段 | 拖选文本新建片段（当前标签）、`1`–`9` 选择标签（选中片段时改为该标签）、`Del`/`Backspace` 删除选中片段、`Esc` 取消选中 |
| 检测 | `1`–`9` 选择类别（选中框时改为该类别）、`B` 绘制新框、`Del`/`Backspace` 删除选中框、`Esc` 取消绘制/选中 |
| 分割 | `1`–`9` 选择类别、`B` 画笔、`E` 橡皮、`P` 多边形、`[`/`]` 画笔半径、`Enter` 闭合多边形、`Backspace` 撤销最后一点、`Esc` 放弃多边形 |
| 深度 | `1` 提高、`2` 降低、`X` 切换方向、`[`/`]` 画笔半径 |
| ReID | `1` 同一人、`2` 不同人、`3` 不确定（按下即提交） |

**类别配色**：`palette.ts` 的 `categoryColor(index)` 按类别在项目配置中的顺序取色，
检测框、分割掩膜、文本片段与侧栏色块共用，保证画布与图例一致。分割掩膜值 `N` 对应第 `N` 个
类别（`categoryColor(N - 1)`），`0` 为背景。

## 7. 文本片段标注（`components/text-span/`、`tasks/text-span/`）

`text-span.ts` 是纯函数（有单测）：`CodePointIndex` 在 UTF-16 下标与 code point 之间
换算（后端 offset 为 code point）；`selectionToRange()` 把一次 DOM 选区换成去掉首尾
空白的 code point 区间；`segmentText()` 在所有片段边界处切分文本，每段记录覆盖它的
片段，于是重叠/嵌套片段渲染为：最内层片段着底色、每个覆盖片段各加一条彩色下划线
（最多叠 4 条）。`TextSpanReviewPage` 在 `mouseup` 时以"文档开头到选区端点"的
`Range.toString().length` 求 UTF-16 偏移（不依赖段落结构），单击已标注文字选中
最内层片段。
