# 目标检测（detection）

## 项目配置

`task_type: detection`，配置文件形状见
[`backend/configs/detection.example.yaml`](../backend/configs/detection.example.yaml)：

```yaml
dataset: ./example-images
categories: [person, vehicle]      # 顺序即 COCO category id 的生成顺序
patterns: ["**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"]
annotations: .annotations/detection.json
```

## 标注数据模型

坐标系统一为**原图像素坐标**（左上角为 `(0, 0)`）。提交给
`POST /api/projects/{id}/annotations` 的 `result` 形状：

```json
{
  "image_size": { "width": 1920, "height": 1080 },
  "boxes": [
    { "category": "person", "x": 120, "y": 80, "width": 60, "height": 140 }
  ]
}
```

- 一个 item 的 `image_size` 由首次提交固定下来，之后的重复提交必须与
  已保存的 `image_size`/`boxes` 完全一致（幂等），否则视为覆盖冲突并
  拒绝（`TaskConflictError`，HTTP 409）。
- `box` 越界（超出 `image_size`）、宽高非正、`category` 不在配置的
  `categories` 列表内，一律直接拒绝（HTTP 422），不做静默裁剪或归一化。
- 空 `boxes` 列表是合法提交，表示“这张图确认没有目标”。

## 导出格式

- `format=native`（或 `json`）：原样返回底层 JSON 侧车文件
  （`items`/`history`，schema 版本 1）。
- `format=coco`：写出 `coco.json`，与配置同目录：

  ```json
  {
    "images": [{ "id": 1, "file_name": "a.jpg", "width": 1920, "height": 1080 }],
    "categories": [{ "id": 1, "name": "person" }, { "id": 2, "name": "vehicle" }],
    "annotations": [
      {
        "id": 1, "image_id": 1, "category_id": 1,
        "bbox": [120.0, 80.0, 60.0, 140.0], "area": 8400.0, "iscrowd": 0
      }
    ]
  }
  ```

  `category_id` 按配置里 `categories` 的顺序从 1 开始编号；`image_id`
  与 `annotation.id` 按 item/box 的排序结果确定性生成，两次导出（在
  标注内容不变的前提下）结果逐字节一致。`image_size` 的 `width`/`height`
  会被校验并强制为整数；`bbox`/`area` 保留浮点数，因为 box 坐标本身
  允许亚像素精度。

## 示例

```bash
curl -s http://127.0.0.1:8000/api/projects/scene-detection/queue | jq
curl -s -X POST http://127.0.0.1:8000/api/projects/scene-detection/annotations \
  -H 'Content-Type: application/json' \
  -d '{"item_id": "i...", "result": {"image_size": {"width": 640, "height": 480}, "boxes": []}}'
```

Python 层直接调用 `DetectionTaskType().export(project, ExportRequest(format="coco"))`
可在脚本里复用同一套导出逻辑，无需经过 HTTP。
