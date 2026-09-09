# 深度图画笔（depth）

## 项目配置

`task_type: depth`，配置文件形状见
[`backend/configs/depth.example.yaml`](../backend/configs/depth.example.yaml)：

```yaml
dataset: ./example-images
depth_maps: .depth-baseline   # 每张源图对应一张同相对路径/文件名的基线灰度 PNG
annotations: .annotations/depth
```

`depth_maps`（基线深度图目录）与 `annotations`（编辑结果输出目录）都
必须落在 `dataset` 内部且互不重叠，原因和分割任务一样：两者都会被
`patterns` 的图片发现逻辑自动排除，避免把基线图或编辑结果当成新的
待标注源图收进队列。

图片 `foo/bar.jpg` 对应的基线深度图路径是
`<depth_maps>/foo/bar.png`（同相对路径，扩展名固定为 `.png`）。没有
对应基线文件时，前端会从中灰度（128）开始编辑，而不是报错——这允许
项目在还没有跑深度估计流水线之前就先接入本任务类型。

## 标注数据模型：复用分割的整图栅格，不是增量笔画

标注者在浏览器里用画笔提高/降低深度值，视觉上是相对基线的增量编辑，
但**这只是前端的交互方式**：画笔操作全部发生在浏览器端的一张像素
缓冲区（与 #21 分割共用同一个 `raster-buffer.ts` 图元），提交给后端
的仍是**整图替换**的最终栅格，服务端不追踪笔画历史，也不做增量合并：

```json
{
  "image_size": { "width": 640, "height": 480 },
  "pixels": "<base64，width*height 字节，逐行、每像素一个字节，即 0-255 深度值>"
}
```

- 幂等判定同样用像素 sha256；同一 item 提交不同的栅格会被拒绝为冲突。
- 落盘同样通过 `annotation_platform.imaging.encode_gray8_png`，产出
  真实、可被任意工具打开的 8-bit 灰度 PNG。
- 索引 JSON 里的每条记录都带 `baseline_path`（该 item 用的基线路径，
  没有基线则为 `null`），保留“编辑基于哪张基线”的可追溯性。

## 导出格式

只支持 `format=native`：返回 `index.json` 以及每个已编辑 item 的深度图
PNG 路径。深度图不是检测/分割式的结构化标注，没有 COCO 等价物。

## 示例

```bash
curl -s http://127.0.0.1:8000/api/projects/scene-depth/files/.depth-baseline/a.png -o baseline.png
curl -s http://127.0.0.1:8000/api/projects/scene-depth/files/.annotations/depth/i....png -o edited.png
```
