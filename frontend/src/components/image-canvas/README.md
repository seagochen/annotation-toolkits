# ImageCanvas API

`ImageCanvas` 是检测、分割和深度任务共享的视口与图层组件。任务代码只处理原图像素
坐标和自己的 annotation state，不应读取 DOM 尺寸、复制缩放公式或修改组件内部。

## 坐标与视口

- `imageSize` 是原图宽高，所有任务数据均使用此坐标系。
- `Viewport.scale` 表示 `screen pixels / image pixel`。
- `Viewport.offset` 是原图 `(0, 0)` 在画布屏幕坐标中的位置。
- `imageToScreen`、`screenToImage` 是互逆纯函数。
- `fitViewport`、`panBy`、`zoomAt` 返回新值，不修改输入；`zoomAt` 保持 anchor 下的
  图像点不移动。
- 默认 scale 范围是 `0.05..32`，可以通过 `scaleLimits` 修改。

受控模式传入 `viewport` 和 `onViewportChange`；否则只传 `defaultViewport` 或让组件
自动 fit。测试、story 或固定布局可传 `viewportSize`；正常页面由 `ResizeObserver`
测量容器。

## 图层

`layers` 按数组顺序从下到上渲染。每个 layer 有稳定 `id` 和：

```ts
type ImageCanvasLayer = {
  id: string;
  render(context: CanvasRenderingContext2D, frame: ImageCanvasFrame): void;
  visible?: boolean;
  opacity?: number;
  blendMode?: CSSProperties["mixBlendMode"];
};
```

调用 `render` 前，context 已转换到原图像素坐标；任务代码可直接使用 annotation 的
`x/y/width/height`。每层使用独立 canvas，因此隐藏或改变 opacity/blend mode 不会污染
其他层。render 必须同步且不得保留 context 引用。

- #18 detection：一个 layer 绘制 boxes/handles，pointer event 修改 box state。
- #21 segmentation：polygon 与 raster preview 使用不同 layer，保证顺序明确。
- #22 depth：灰度 depth raster layer 设置所需 blend mode，brush 仍接收图像坐标。

## 输入事件

`interactionMode="pan"` 时左键拖动平移。`interactionMode="tool"` 时组件通过
`onPointerEvent` 发出 `down/move/up/cancel`，同时包含 `image`、`screen`、pointer id/type、
pressure、buttons 和修饰键；按住 Space 拖动临时平移，不向工具发送该段 pointer 序列。
滚轮在指针 anchor 缩放，两种模式都可用。组件使用 pointer capture，工具不需要自行
绑定 window 事件。

## 键盘与冲突

内建快捷键：`+`/`=` 放大、`-` 缩小、`0` 适应窗口、方向键平移。焦点位于 input、
textarea、select、button 或 contenteditable 时不触发画布快捷键。

任务快捷键通过 `shortcuts` 传入。`resolveShortcuts` 会规范化大小写；内建键优先，重复
自定义键保留第一项。所有被忽略的原始键通过 `onShortcutConflict(keys)` 显式报告，任务
可以显示配置错误而不会发生一次按键触发两个动作。

## 最小接入

```tsx
<ImageCanvas
  alt="待标注图像"
  imageSize={{ width: image.width, height: image.height }}
  interactionMode="tool"
  layers={[annotationLayer, selectionLayer]}
  onPointerEvent={tool.handlePointer}
  onViewportChange={setViewport}
  src={image.url}
/>
```

可运行演示位于 `/canvas-demo`，包含静态图、网格层、blend 区域层、pan/tool 模式和实时
图像坐标。
