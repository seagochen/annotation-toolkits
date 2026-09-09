import { useMemo, useState } from "react";
import { Link } from "wouter";

import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
} from "../components/image-canvas";

export function CanvasDemoPage() {
  const [mode, setMode] = useState<"pan" | "tool">("pan");
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [point, setPoint] = useState({ x: 0, y: 0 });
  const layers = useMemo<readonly ImageCanvasLayer[]>(
    () => [
      {
        id: "grid",
        opacity: 0.38,
        render(context) {
          context.strokeStyle = "#ffffff";
          context.lineWidth = 2;
          for (let x = 0; x <= 1200; x += 100) {
            context.beginPath();
            context.moveTo(x, 0);
            context.lineTo(x, 800);
            context.stroke();
          }
          for (let y = 0; y <= 800; y += 100) {
            context.beginPath();
            context.moveTo(0, y);
            context.lineTo(1200, y);
            context.stroke();
          }
        },
      },
      {
        id: "regions",
        visible: showHeatmap,
        opacity: 0.7,
        blendMode: "screen",
        render(context) {
          context.fillStyle = "rgba(240, 75, 55, 0.6)";
          context.fillRect(450, 280, 210, 420);
          context.fillStyle = "rgba(40, 125, 230, 0.55)";
          context.beginPath();
          context.arc(950, 160, 115, 0, Math.PI * 2);
          context.fill();
        },
      },
    ],
    [showHeatmap],
  );

  function trackPointer(event: ImageCanvasPointerEvent) {
    setPoint(event.image);
  }

  return (
    <section className="canvas-demo-page">
      <Link className="back-link" to="/">← 返回项目列表</Link>
      <div className="canvas-demo-heading">
        <div>
          <p className="eyebrow">Shared primitive demo</p>
          <h1>图像画布</h1>
          <p>原图像素坐标、独立 overlay layers、缩放、平移与工具事件的组合演示。</p>
        </div>
        <div className="canvas-demo-toolbar">
          <button className={mode === "pan" ? "selected" : ""} onClick={() => setMode("pan")} type="button">平移模式</button>
          <button className={mode === "tool" ? "selected" : ""} onClick={() => setMode("tool")} type="button">工具事件模式</button>
          <label>
            <input checked={showHeatmap} onChange={(event) => setShowHeatmap(event.target.checked)} type="checkbox" />
            合成区域层
          </label>
        </div>
      </div>
      <div className="canvas-demo-stage">
        <ImageCanvas
          alt="共享图像画布演示"
          imageSize={{ width: 1200, height: 800 }}
          interactionMode={mode}
          layers={layers}
          onPointerEvent={trackPointer}
          src="/canvas-demo.svg"
        />
      </div>
      <div className="canvas-demo-meta">
        <span>图像坐标 x={point.x.toFixed(2)}, y={point.y.toFixed(2)}</span>
        <span>滚轮或 +/− 缩放 · 方向键平移 · 0 适应 · 工具模式按 Space 拖动</span>
      </div>
    </section>
  );
}
