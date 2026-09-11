# docs 目录

Annotation Toolkits 的设计与开发文档。工程总览与快速上手见仓库根
[README.md](../README.md)；本目录存放系统的需求分析、总体设计和详细设计书。

## 文档一览

| 文件 / 目录 | 类别 | 内容 |
|---|---|---|
| [`requirements/`](requirements/00_概述.md) | 需求分析书 | 产品目标、角色场景、功能与非功能需求、验收标准 |
| [`overall_design/`](overall_design/00_概述.md) | 总体设计书 | 系统架构、子系统职责、数据接口、质量属性与部署演进 |
| [`detailed_design/`](detailed_design/00_概述.md) | 详细设计书 | 按模块拆分的设计文档群。入口为 [`detailed_design/00_概述.md`](detailed_design/00_概述.md) |

## 需求分析书（`requirements/`）

以[概述](requirements/00_概述.md)为入口，按业务能力定义需求；复用已有术语表。

| 文档 | 内容 |
|---|---|
| [角色与场景](requirements/10_角色与场景.md) | 使用者职责与主要流程 |
| [项目与图像标注](requirements/20_项目与图像标注.md) | 项目、五种图像任务及提交保护 |
| [ReID数据闭环](requirements/30_ReID数据闭环.md) | 抽取、审核、检查、定版与交接 |
| [数据与外部交互](requirements/70_数据与外部交互.md) | 数据交付及外部责任 |
| [非功能需求](requirements/80_非功能需求.md) | 质量约束与待确定目标 |
| [验收标准](requirements/90_验收标准.md) | 需求映射与可观察通过条件 |

## 总体设计书（`overall_design/`）

以[概述](overall_design/00_概述.md)为入口，说明现有架构及其适用边界。

| 文档 | 内容 |
|---|---|
| [系统架构](overall_design/10_系统架构.md) | 分层、依赖方向与设计取舍 |
| [标注平台与前端](overall_design/20_标注平台与前端.md) | 交互、任务分发与扩展边界 |
| [ReID子系统](overall_design/30_ReID子系统.md) | 领域闭环与异步执行 |
| [数据与接口](overall_design/70_数据与接口.md) | 数据归属、提交时序与一致性 |
| [质量属性](overall_design/80_质量属性.md) | 设计措施及验证限制 |
| [部署与演进](overall_design/90_部署与演进.md) | 拓扑、故障域、备份恢复与演进条件 |

## 详细设计书（`detailed_design/`）

以亲文档 `00_概述.md` 为起点、按子系统拆分的设计文档群，本书群为模块契约与实现设计的唯一
正源（SSoT），需求与架构决策引用对应上位书群。

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
- **按范围维护唯一正源**：需求、架构与模块契约分别在所属书群展开，跨层通过链接追溯。
- Markdown 由 Git 管理，版本不写入文件名，以 Git 提交历史 / tag 追踪。
- 图用 mermaid 内联编写（便于 diff，不依赖外部图文件）。
- 不在正文维护文档编号、日期、负责人、密级或修订历史；这些信息由 Git 追踪。
