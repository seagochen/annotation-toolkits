# 图像描述（captioning）

## 项目配置

`task_type: captioning`（不是 `caption`——这是 API/`projects.yaml` 里
实际使用的值），配置文件形状见
[`backend/configs/caption.example.yaml`](../backend/configs/caption.example.yaml)：

```yaml
dataset: ./example-images
patterns: ["**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"]
annotations: .annotations/caption.json
```

无需画布，前后端都复用图像分类（#17）的队列/提交基础设施，只是把
标签选择器换成一段自由文本。

## 标注数据模型

```json
{ "caption": "一只猫在窗边睡觉。" }
```

- 提交前统一把 `\r\n`/`\r` 归一化为 `\n`，首尾空白会被裁剪；裁剪后为
  空字符串（纯空白）一律拒绝。
- 允许任意 Unicode 字符与内部换行；长度上限 2000 个字符（裁剪后计数）。
- 同一 item 重复提交相同文本视为幂等成功；提交不同文本会被拒绝为
  冲突（HTTP 409），不会覆盖已保存的描述。

## 导出格式

- `format=native`（或 `json`）：原样返回底层 JSON 侧车文件。
- `format=csv`：逐行 `item_id,image_path,caption`。

## 示例

```bash
curl -s -X POST http://127.0.0.1:8000/api/projects/scene-caption/annotations \
  -H 'Content-Type: application/json' \
  -d '{"item_id": "i...", "result": {"caption": "A cat sleeping by the window."}}'
```
