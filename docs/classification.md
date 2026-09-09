# 图像分类（classification）

## 项目配置

`task_type: classification`，配置文件形状见
[`backend/configs/classification.example.yaml`](../backend/configs/classification.example.yaml)：

```yaml
dataset: ./example-images
mode: single      # single 恰好一个标签；multi 一个或多个标签
labels: [indoor, outdoor]
patterns: ["**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"]
annotations: .annotations/classification.json
```

## 标注数据模型

```json
{ "labels": ["outdoor"] }
```

- `labels` 的每一项必须在配置的 `labels` 列表中，且不允许重复；保存时
  会按配置里 `labels` 的声明顺序重新排序，与提交顺序无关。
- `single` 模式要求恰好一个标签；`multi` 模式要求至少一个标签。
- 同一 item 重复提交相同标签集合视为幂等成功；提交不同结果会被拒绝为
  冲突（HTTP 409）。

## 导出格式

- `format=native`（或 `json`）：原样返回底层 JSON 侧车文件。
- `format=csv`：逐行 `item_id,image_path,labels`（`labels` 是一段 JSON
  数组字符串）。

## 示例

```bash
curl -s -X POST http://127.0.0.1:8000/api/projects/scene-classification/annotations \
  -H 'Content-Type: application/json' \
  -d '{"item_id": "i...", "result": {"labels": ["outdoor"]}}'
```
