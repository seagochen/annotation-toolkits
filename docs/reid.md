# ReID 成对审核（reid）

ReID 是本平台从专用工作台迁移过来的第一个任务类型，也是目前唯一带
完整数据抽取/挖掘/训练交接流水线的任务。它的数据约束、`reid.yaml`
配置分节、流水线接入方式、候选挖掘、逻辑冲突检测、数据定版与训练
交接、模型评估、低精度部署约束，都写在
[`backend/README.md`](../backend/README.md) 里，本文不重复，只补充
其他任务文档统一覆盖的"导出格式"这一节，以及从旧 CLI 迁移过来的用户
需要知道的路径变化。

## 导出格式

`format=native`：返回当前 pairs 产物（`<dataset>/pairs.csv`）。

```bash
curl -s -X POST http://127.0.0.1:8000/api/projects/scene-reid/annotations \
  -H 'Content-Type: application/json' \
  -d '{"item_id": "c1", "result": {"label": "same"}}'
```

`label` 取值 `same` / `different` / `unclear`；同一候选重复提交相同
标签视为幂等成功，提交不同标签会被拒绝为冲突。字段级细节（`review_label`
写回位置、审计轨迹）见 `backend/README.md` 第 3–4 节。

ReID 的其余能力通过 `TaskActionModule` 暴露为可轮询的后台动作
（`extract`/`mine`/`check`/`finalize`/`purge-domain`/`train`），不是
`export()` 的一部分——它们分别在 `backend/README.md` 对应章节和
`POST /api/projects/{id}/actions/{action}` 里说明。

## 旧 CLI 用户的路径迁移

仓库重组（#9）把原来仓库根目录下的一切都移进了 `backend/`：

| 旧路径/命令 | 新路径/命令 |
| --- | --- |
| `python app.py ...` | `python backend/app.py ...`（或 `cd backend && python app.py ...`） |
| `pip install -e .` | `pip install -e backend` |
| `configs/*.yaml`、`pipeline/` | `backend/configs/*.yaml`、`backend/pipeline/` |
| `python app.py serve` + 标准库 HTTP 页面 | 已移除，改用 `uvicorn annotation_platform.server:app --app-dir backend`（见根 [`README.md`](../README.md) 快速开始） |

`reid.yaml` 内部字段的迁移（旧版一长串 CLI flag、`train:` 节里训练器
专属超参的搬迁）已经写在 `backend/README.md` 对应小节，这里不重复。
