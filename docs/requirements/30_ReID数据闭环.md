# ReID 数据闭环需求

参与者为本地流水线操作者和标注者。需求范围沿用[ReID 操作手册](../../backend/README.md)，具体行为由下列源码及[流水线详细设计](../detailed_design/30_ReID流水线.md)佐证。本次整理既有基线，不新增训练质量指标。

## FR-008 数据抽取与候选审核

**确认情况**：现有文档基线及当前实现行为。操作者配置可用流水线、输入数据和所需模型后，抽取可审核数据并挖掘候选；标注者查看候选图像，提交同一人、不同人或不确定判定及备注。相似度只用于辅助排序，不能直接作为身份真值。无审核轮次或未知候选的提交被拒绝。

**依据**：[extract.py](../../backend/reid_annotation_tool/extract.py)、[mine.py](../../backend/reid_annotation_tool/mine.py) 与 [ReIDTaskType.submit](../../backend/annotation_platform/reid_task.py)。**验收**：[AC-008](90_验收标准.md#ac-008)。

## FR-009 关系检查与数据定版

**确认情况**：现有文档基线及当前实现行为。已有候选和人工关系后，检查身份等价关系与互斥关系的矛盾，以及配置所约束的身份域；操作者应能据报告核对问题。违反定版条件时不能生成被视为可训练的有效结果；修正后可重新检查并定版。跨天、跨相机是否允许关系以项目域配置为准。

**依据**：[conflicts.py](../../backend/reid_annotation_tool/conflicts.py)、[domain.py](../../backend/reid_annotation_tool/domain.py) 与 [app.py](../../backend/reid_annotation_tool/app.py) 阶段入口。**验收**：[AC-009](90_验收标准.md#ac-009)。

## FR-010 训练交接

**确认情况**：现有文档基线及当前实现行为。操作者具备符合交接约束的数据集与外部训练工程时，可生成训练交接配置并调用外部训练器，保留数据来源及运行信息。未解决标签、矛盾数据或缺失训练器应使交接失败，不能以成功训练报告代替错误。平台不承诺模型精度；CLI 的评估能力也不代表通用 Web 动作已暴露评估入口。

**依据**：[handoff.py](../../backend/reid_annotation_tool/handoff.py) `train`、`validate_pairs`，及 [test_handoff.py](../../backend/tests/test_handoff.py)。**验收**：[AC-010](90_验收标准.md#ac-010)。

## FR-011 后台动作观察与失败处理

**确认情况**：当前实现行为。平台支持的动作由任务能力列表决定，操作者可启动动作并查询状态、日志、结果或错误。同一后端进程已有动作执行时，新动作应得到忙碌反馈，不形成自动排队承诺。进程重启后中断记录应显示失败，不自动恢复执行。

**依据**：[ReIDTaskType](../../backend/annotation_platform/reid_task.py) `action_names`、`start_action`；[JobRunner](../../backend/reid_annotation_tool/jobs.py)。**验收**：[AC-011](90_验收标准.md#ac-011)。
