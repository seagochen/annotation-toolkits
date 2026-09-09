# 图像分割（segmentation）

## 项目配置

`task_type: segmentation`，配置文件形状见
[`backend/configs/segmentation.example.yaml`](../backend/configs/segmentation.example.yaml)：

```yaml
dataset: ./example-images
categories: [road, building]       # 像素值 1..N；0 恒为背景
patterns: ["**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"]
annotations: .annotations/segmentation   # 目录，不是单个文件
```

`annotations` 是一个**目录**：里面存放 `index.json`（队列/状态/冲突用的
元数据）和每个已标注 item 一张 `<item_id>.png` 掩膜图。这个目录（以及
下面提到的深度任务的 `depth_maps`/`annotations`）会被自动排除在
`patterns` 的图片发现范围之外，不会把自己写出的掩膜文件又当成一张待
标注的新图片收进队列。

## 标注数据模型：整图栅格，不是多边形

前端画布同时提供多边形和画笔两种交互，但**两者都只是客户端的作画方式**
——多边形闭合后会立即栅格化进同一张像素缓冲区，画笔直接在这张缓冲区上
画。提交给后端的永远是**整图替换**的栅格结果，服务端不理解、也不存储
多边形顶点：

```json
{
  "image_size": { "width": 640, "height": 480 },
  "pixels": "<base64，width*height 字节，逐行、每像素一个字节，值即类别索引>"
}
```

- 像素值 `0` = 背景，`1..N` 对应配置 `categories` 里第 `1..N` 个类别。
- 幂等判定用提交像素的 sha256 摘要，不需要重新解码已保存的 PNG；
  同一 item 提交内容不同的栅格会被拒绝为冲突（HTTP 409）。
- 服务端把提交的像素编码成一张真实的灰度 PNG
  （`annotation_platform.imaging.encode_gray8_png`，纯标准库实现，
  运行时不依赖 Pillow/numpy）落盘，可以直接用任何图片查看器打开。

## 导出格式

- `format=native`：返回 `index.json` 以及每个 item 的掩膜 PNG 路径。
- `format=coco`：写出 `coco.json`，`segmentation` 字段是**掩膜文件的
  相对路径引用**，不是完整的 RLE/polygon COCO 编码——这是“COCO 兼容”
  而非“COCO 完整实现”，消费方需要自己解析引用的 PNG 掩膜：

  ```json
  {
    "images": [{ "id": 1, "file_name": "a.jpg", "width": 640, "height": 480 }],
    "categories": [{ "id": 1, "name": "road" }, { "id": 2, "name": "building" }],
    "annotations": [{ "image_id": 1, "segmentation_mask": "<item_id>.png" }]
  }
  ```

## 示例

```bash
curl -s http://127.0.0.1:8000/api/projects/scene-segmentation/files/.annotations/segmentation/i....png -o mask.png
file mask.png   # PNG image data, 640 x 480, 8-bit grayscale
```
