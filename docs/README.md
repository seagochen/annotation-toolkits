# docs 目录

Annotation Toolkits 的设计与开发文档。工程总览与快速上手见仓库根
[README.md](../README.md)；本目录存放系统的详细设计书。

## 文档一览

| 文件 / 目录 | 类别 | 内容 |
|---|---|---|
| [`detailed_design/`](detailed_design/00_概述.md) | 详细设计书 | 按模块拆分的设计文档群。入口为 [`detailed_design/00_概述.md`](detailed_design/00_概述.md) |

## 详细设计书（`detailed_design/`）

以亲文档 `00_概述.md` 为起点、按子系统拆分的设计文档群，本书群为设计信息的唯一
正源（SSoT）。

| 文件 | 内容 |
|---|---|
| [`00_概述.md`](detailed_design/00_概述.md) | 亲文档：参照/追溯、系统全貌、系统要件、移交、本书群构成 |
| [`05_术语表.md`](detailed_design/05_术语表.md) | 项目专有术语与概念定义 |
| [`10_通用设计.md`](detailed_design/10_通用设计.md) | 数据模型、错误处理、安全、性能、实现约束、测试方针、依赖库 |
| [`20_标注平台后端.md`](detailed_design/20_标注平台后端.md) | `backend/annotation_platform/`：任务类型插件协议、内置任务模块、HTTP API |
| [`30_ReID流水线.md`](detailed_design/30_ReID流水线.md) | `backend/reid_annotation_tool/`：ReID 数据抽取、挖掘、冲突检测、定版与训练交接 |
| [`40_React前端.md`](detailed_design/40_React前端.md) | `frontend/src/`：项目导航、共享图像画布、各任务标注页面 |
| [`70_外部接口.md`](detailed_design/70_外部接口.md) | HTTP API 完整规格：端点、字段表、请求示例 |
| [`80_配置参考.md`](detailed_design/80_配置参考.md) | `projects.yaml`、各任务类型配置、环境变量参考 |
| [`90_部署与运维.md`](detailed_design/90_部署与运维.md) | 安装、启动、打包、日常运维 |

## 文档形式方针

- **对代码以参照为主**：类、函数、类型、配置以「文件路径 + 符号名」引用，不转记
  代码本体、数值与默认值；接口以契约（职责、前后条件）级别描述。
- **对其他文档自包含**：与 README 等重复的设计信息收敛进本书群，本书群为 SSoT。
- Markdown 由 Git 管理，版本不写入文件名，以 Git 提交历史 / tag 追踪。
- 图用 mermaid 内联编写（便于 diff，不依赖外部图文件）。
- 不在正文维护文档编号、日期、负责人、密级或修订历史；这些信息由 Git 追踪。
