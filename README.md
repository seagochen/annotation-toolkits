# ReID Annotation Tool

面向监控视频 ReID 数据集的证据驱动工作台：**接入你自己的跟踪结果 → 人工标注 →
逻辑冲突检测 → 重新训练**。

两条底线：

- **本工具不做目标检测、ID 分配和轨迹跟踪。** 这三件事依赖你的模型、你的场景和你的
  硬件，由你写一个流水线脚本负责（见下"接入你自己的流水线"）。本工具从你输出的框开始：
  截取目标、按数据集结构组织、交给人工标注、把答案写回数据集。
- 模型只能排序人工审核候选，不能把相似度直接当作身份真值。

## 数据流

```text
video -> 你的流水线脚本（检测 + 跟踪 + ID 分配）
      -> crop firewall
      -> accepted continuous tracks / rejected crops
      -> automatic same-track positives + covisible negatives
      -> lightweight ReID candidate mining (cross-track / track-purity)
      -> persistent human review in the web app
      -> identity-logic conflict gate
      -> immutable train/val/test pair manifest
      -> retrain with ../mobilenet-yolopose-plugin-pytorch
      -> exported ONNX becomes the next round's mining model
```

核心规则：

- `same` 是可传递的身份图边；
- `different` 是不可违反的约束边；
- `unclear` 永远不进入训练；
- 模型距离只决定审核顺序；
- 默认只有 `train` 可被新标注扩充，`val`/`test` 会逐行校验不变；
- 标注通过临时文件、`fsync`、原子替换保存，刷新网页不会丢失。

## 部署

### 1. 环境要求

- Python ≥ 3.10（`pyproject.toml` 的硬性要求）。
- 依赖分两档，按机器角色选：
  - **只做标注 / 审计 / finalize**（开网页标注、跑 `check`、跑 `finalize`）：
    只需要 `PyYAML`。网页、冲突引擎和配对清单全部是标准库实现，不需要
    opencv / onnxruntime / ultralytics。
  - **要跑 `extract` / `mine`**（读录像、切裁剪、跑 ReID 模型给候选排序）：
    还需要 `numpy` / `opencv-python` / `onnxruntime`；用随包的 `ultralytics`
    参考流水线时再加 `ultralytics`（自带跟踪脚本的话不需要）。

### 2. 获取代码并安装依赖

```bash
git clone <本仓库地址> reid-annotation-tool
cd reid-annotation-tool

# 建议用独立虚拟环境（conda / venv 均可）
conda create -n reid-annotation python=3.11 -y
conda activate reid-annotation

pip install -r requirements.txt        # 完整安装：extract + mine + ultralytics 参考流水线
# 纯标注 / 审计机器只需要：
#   pip install PyYAML
# 开发机 / 需要可编辑安装时：
#   pip install -e '.[extract,ultralytics]'
```

### 3. 初始化配置

```bash
python app.py init          # 写一份 reid.yaml
$EDITOR reid.yaml           # 至少改 dataset: 和 pipeline: 两节；完整示例见 configs/reid.example.yaml
python app.py status        # 数据集现状 + 建议的下一步
```

`app.py` 是唯一入口，取代了旧的 `reid-annotation <子命令> <一长串 flag>`。
路径只在 `reid.yaml` 里写一次，审核轮次（`review/vN/candidates.csv`）自动发现：
当前轮自动作为待审队列，历史轮次自动全部纳入冲突检测——这一条以前靠手敲
`--review`，敲漏一个就会静默改变冲突检测能看到的答案。

### 4. 起标注网页

```bash
python app.py               # 起标注网页（默认动作，= serve）
```

默认监听 `127.0.0.1:8765`（`serve.host` / `serve.port`，见 `reid_annotation_tool/config.py`
的 `DEFAULTS`），只有本机能访问；团队内网共用时把 `reid.yaml` 里的
`serve.host` 改成 `0.0.0.0`（`configs/reid.example.yaml` 就是这样配的），
局域网内其他机器即可访问 `http://<本机IP>:8765/`。

这是基于标准库 `ThreadingHTTPServer` 的单进程服务，面向团队内网标注场景，
不做鉴权，不要直接暴露公网。需要长期后台运行时，用 `tmux` / `systemd` /
`nohup` 之类的进程管理工具包一层即可——本工具自身不做 daemonize。

### 命令一览

| 命令 | 作用 |
| --- | --- |
| `python app.py` | 起标注网页（= `serve`） |
| `python app.py status` | 数据集现状与下一步 |
| `python app.py extract` | 用你的流水线脚本接入录像，产出数据集 |
| `python app.py mine` | 挖掘下一轮审核候选 |
| `python app.py check` | 逻辑冲突检测（有 error 返回 1） |
| `python app.py finalize` | 把已审核结论烤进新的 pairs 清单 |
| `python app.py train` | 桥接外部训练器 |
| `python app.py purge-domain` | 归档跨天 / 跨相机关系 |

单次覆盖配置用 `--set 节.键=值`，例如 `python app.py --set serve.port=9000`。

完整工作流是下面的 1-5 节，按顺序跑一遍：接入流水线 → 抽取数据集 → 挖掘并标注
→ 冲突检测 → 汇总并重新训练。

## 1. 接入你自己的流水线

检测、ID 分配、跟踪由一个普通 Python 文件负责，配置里指名即可
（形式与 `ee-pf-jetson-engine` 的 `pyline` 脚本一致：宿主拥有循环与数据契约，
脚本拥有模型）：

```yaml
pipeline:
  script: ./my_tracker.py     # 也可以是随包附带的参考实现名
  # 本节其余的键属于你的脚本，本工具不做校验
```

脚本必须定义：

```python
from reid_annotation_tool.contract import Observation

def process_frame(image, source, config) -> list[Observation]:
    """image 是 BGR numpy 帧；source 带 name/index/timestamp/width/height/split。
    返回这一帧里你看到的**所有**目标，坐标是原帧像素。"""
```

可选钩子：

| 钩子 | 作用 |
| --- | --- |
| `open_source(source, config)` | 新录像开始，重置你的跟踪器状态 |
| `close_source(source, config)` | 录像结束 |
| `detect_crops(images, config) -> list[list[Observation]]` | 裁剪防火墙第二遍：在成品裁剪上重跑一次检测 |

`Observation.track_id` 是关键：**带 track_id 的框才会成为身份**；不带的框只作为
旁观者用来否决被污染的裁剪。请把这一帧检出的所有目标都返回，否则遮挡会被漏判——
这正是本工具不需要自己拥有检测器也能执行纯净度规则的原因。

脚本路径与 SHA-256 会写进 `manifest.json`：是谁产出的框，属于数据集契约的一部分。

随包附带两个参考实现（`reid_annotation_tool/pipelines/`，可直接抄）：

- **`ultralytics`** —— Ultralytics 检测器 + 内置 IoU 跟踪器，开箱即用；
- **`tracking_csv`** —— 直接读你已有系统导出的跟踪结果，不需要任何模型：
  `source,frame,track_id,x1,y1,x2,y2[,class_id,conf]`。`source` 要和本工具从录像
  路径推出的录像 ID 一致（默认是文件名主干，或 `splits.video_id_regex` 的捕获组）。

## 2. 从录像产出数据集

```bash
python app.py extract
```

录像按 `cam16/2026-08-30/210002.mp4` 分层时，用 `splits.timestamp_source: path`
让时间正则匹配完整路径，并用 `splits.video_id_regex` 的捕获组生成不会跨摄像头碰撞的
录像 ID。需要固定按自然日划分时用 `splits.day_split: [20260828=train, ...]`；
该映射是封闭的，遇到未列出的日期会停止抽取。

轨迹何时结束由**本工具**判定，不由脚本判定：连续 `extract.track_gap` 个已处理帧
没有再出现即视为结束——一条轨迹的"结束"正是它的裁剪构成一组封闭同轨证据的前提。

抽取阶段的证据契约：

| 标签 | 来源 | 说明 |
| --- | --- | --- |
| 正样本 | 同一条连续、几何合理、无遮挡的轨迹 | 由 `pairs.min_positive_gap` 保证时间跨度 |
| 负样本 | 两条轨迹在同一帧被同时观测且 IoU 极低 | 由 `pairs.min_covisible_frames` 证明 |
| 未标注 | 跨轨迹的时间关系 | 交给人工，模型不得代替人工标注 |

### 裁剪几何：透视投影的身体框（唯一算法，没有开关）

检出框标的是**头**，ReID 模型训练在**全身**上。身体矩形不是头框的固定倍数——它取决于
头相对相机 nadir 的位置，所以还原身体需要场景标定。抽取器**始终**裁投影身体框，
不提供"直接裁检出框"的选项：两种几何产出的数据集不可互换，任何在一种几何上标定的
绝对 cosine 门在另一种上都是静默失效的（门看起来没动，实际已经松了）。

标定可配置（默认值是生产环境的实测标定值，换场景必须自己传）：

| `projection:` 键 | 默认 | 含义 |
| --- | --- | --- |
| `projection_space: [W, H]` | `[1200, 538]` | 标定所在的空间；帧分辨率任意，会缩放进出 |
| `seam_x` | `600.0` | 主导 nadir 换边的列 |
| `nadir_left` / `nadir_right` | `[587.7, 455.2]` / `[612.3, 455.2]` | 镜像 nadir 对 |
| `extent_k: [K_MIN, K_REF, K_MAX]` | `[1.0, 2.5, 5.0]` | 头→身体扩张因子的下限 / R_REF 处取值 / 上限 |
| `extent_r_ref` | `250.0` | 扩张因子等于 `K_REF` 时的头–nadir 距离 |

标定值全部写入 `manifest.json` 的 `contract.body_projection`，所以任何一张裁剪都能
从 `identities.csv` 的框列重新还原。`tests/test_projection.py` 用黄金向量把本实现钉在
生产部署的 `surv.identity.state.head_to_body` 上，两边公式一旦漂移即刻失败。

裁剪防火墙（可用 `crops.no_crop_firewall: true` 关闭）：

1. 逐帧检查**投影身体框**与其他目标身体框的 IoU / 包含率，被遮挡即丢弃该轨迹
   （判纯净度必须按身体框：两个人的头可以隔得很远，而其中一个的身体裁剪把另一个整个吞进去）；
   这一步用的是脚本返回的**全部**框，包括没有 track_id 的旁观者；
2. 检查运动是否合理（位移、面积突变）——这一步用脚本**原样返回的框**，它是实测量，
   投影身体框会继承投影在 seam 附近的敏感性，把正常走动误判为异常；
3. 对完成的裁剪再跑一次检测（脚本的 `detect_crops` 钩子），剔除仍含第二个目标的裁剪，
   超过 `crops.reject_track_multi_crops` 张即丢弃整条轨迹。**是否算污染由本工具判定**，
   脚本只负责给出检测结果——这条规则不能随流水线变化。脚本没有 `detect_crops` 时
   本步跳过，并在 `manifest.json` 里写明"该阶段不可用"，而不是假装裁剪通过了检查。

⚠️ **`crops.max_neighbour_iou` / `max_neighbour_contained` 的默认值（0.10 / 0.35）是按检出框
调的，对身体框偏紧**。同一段录像 570 次观测实测：头框几何拒 17.5%，身体框几何在同样阈值下
拒 45.3%；要回到 ~18% 需要放到约 `0.40 / 0.65`。且轨迹级拒绝是黏性的（任何一帧不合格
整条丢），所以身体框几何下这一门会成为主要过滤器。按场景重新标定后再大批量抽取。

划分按**视频**分配（`splits.split_ratios`，或 `splits.split` 强制），因此同一个目标
不会同时出现在 train 和 val。

输出（`dataset:` 目录下）：

```text
<out>/images/<split>/<person_id>/NN.jpg
<out>/identities.csv     img_path,person_id,split,video,track_id,class_id,timestamp,frame,x1..y2,conf,crop_w,crop_h,blur,brightness,over_exposed,under_exposed
<out>/tracks.csv         person_id,split,video,track_id,class_id,start,end,frames,crops,recovered,status,reason
<out>/covisibility.csv   person_id1,person_id2,video,split,frames,first_timestamp
<out>/pairs.csv          img1,img2,label,split,evidence,person_id1,person_id2,gap_sec
<out>/manifest.json      契约、流水线脚本与其 SHA-256、每个划分的统计
```

## 3. 挖掘候选并在网页上标注

```bash
python app.py mine        # 自动写到下一轮 review/vN，并沿用所有历史轮次的答案
python app.py             # 起网页；当前轮自动作为待审队列
```

挖掘两类问题（模型只提问，不回答）：

- `cross_track`：两条轨迹看起来很像但从未被证明相关 —— 是同一个目标吗？
- `track_purity`：一条轨迹内部的裁剪互相不像 —— 跟踪是否中途换了目标？
  标 `different` 表示轨迹被污染，`finalize` 时整条轨迹连同其配对一起丢弃。

`candidate_id` 由内容哈希生成，因此换模型重新挖掘时，已标注的答案会自动保留
（`report.json` 中的 `preserved_labels`）。

网页工作台（打开 `http://host:port/`）：

- 左侧队列支持按类型 / 划分 / 状态 / ID 过滤；
- 中间是两条轨迹的完整画廊，蓝框是模型给出的证据裁剪，点击放大；
- 快捷键：`1/S` 同一目标，`2/D` 不同目标，`3/U` 无法判断，`J/K` 上下切换，
  `N` 写备注，`Z` 放大，`Esc` 关闭；
- “冲突”页实时列出身份逻辑冲突，点开一条冲突链会展开链上每条 same 边的裁剪与出处，
  每条边下方直接给出 **相同 / 不同 / 不确定** 按钮：已有判定的边会高亮对应按钮并写明出处
  （当前轮候选 / `pairs.csv` 行号 / 历史轮次），改判就地生效并原地刷新整条链；
- 多条 `transitive_negative` 的 same 路径相交时，页面会将它们合并成一个冲突组：
  顶部关系图以实线表示 same、红色虚线表示 proven-different，并保留每一对 different 端点；
  下方只展示一次去重后的 same 边，点击图中实线可跳到对应复核卡，避免重复梳理同一段链；
- 改判已烤进 `pairs.csv` 的旧人判会先归档留痕（`pairs.overturned.csv`、
  `revisions.json`，一次性备份 `pairs.before_web_revision.csv`），新判定作为当前轮候选入队；
  抽取阶段的物理证据（共现、同一连续轨迹）不可从网页推翻，判定照样记录但冲突会继续报出；
- 每次标注都原子写回 CSV，冲突检测在后台线程刷新，不阻塞标注节奏。

## 4. 逻辑冲突检测

```bash
python app.py check                 # 有 error 时退出码为 1
python app.py check --strict        # warning 也返回 1，适合 CI
python app.py check --json > conflicts.json
```

| 冲突类型 | 含义 |
| --- | --- |
| `transitive_negative` | 一条 same 链把被 different 约束的两个身份连了起来 |
| `direct_contradiction` | 同一对关系同时被标成 same 和 different |
| `covisible_merge` | 同帧共现（因此必然不同）的两条轨迹被合并 |
| `temporal_overlap` | 合并后的身份在同一视频里同时存在于两个位置 |
| `split_leakage` | 一个身份跨越 train/val/test，或同一张图出现在两个划分 |
| `split_mismatch` | 配对所属划分与其裁剪的划分不一致 |
| `self_negative` | 轨迹被标成与自己不同（应改用 track_purity 候选） |
| `unknown_identity` / `missing_image` | 清单引用了不存在的身份或文件 |
| `contaminated_track`（warning） | 已判定为混合轨迹但仍带着正样本，finalize 会丢弃 |

每条冲突都会给出**证人**：`base:<evidence>` 表示来自抽取阶段的固定证据
（需重新抽取才能改变），其余是可在网页上直接改的 `candidate_id`。

`covisible_merge` 与 `transitive_negative` 是互补的：`pairs.csv` 里的负样本按
`--negative-ratio` 采样，未被采样的共现关系仍然是物理事实，因此单独检查。

## 5. 汇总标注并重新训练

```bash
python app.py finalize    # -> pairs.reviewed-<当前轮>.csv 及其 report
python app.py train       # 训练器参数全部在 reid.yaml 的 train: 节
```

`train` 在启动训练前会：

1. 跑一遍冲突检测，有 error 就拒绝训练（`train.allow_conflicts: true` 可强制覆盖）；
2. 校验 `train` / `val` 两个划分都同时含有正负样本，否则训练器无法计算 ROC-AUC；
3. 生成训练器配置到 `<root>/training/<name>/config.yaml`（`train.set` 可覆盖训练器
   配置里的任意字段，`train.base_config` 可从现成 YAML 出发，`--dry-run` 只生成不训练）；
4. 训练结束后写 `training-manifest.json`：pairs 的 sha256、冲突门禁结果、
   训练器 git revision、产出权重与 ONNX 的 sha256 —— 任何一个 checkpoint
   都能追溯回产生它的标注。

导出的 ONNX 可以直接写回 `mine.reid_onnx`，形成闭环。

## 配置文件

`reid.yaml`（`python app.py init` 生成，完整示例见 `configs/reid.example.yaml`）
按"谁拥有这个决定"分节：

| 节 | 归谁 |
| --- | --- |
| `dataset` | 数据集根目录，所有阶段由它推导路径 |
| `pipeline` | **你的**检测 / 跟踪脚本及其参数（本工具不校验这一节的键） |
| `extract` / `splits` | 用哪些录像、抽多密、时间与划分怎么读 |
| `projection` | 相机标定（头→身体裁剪几何） |
| `crops` / `pairs` | 什么样的裁剪可信、什么样的两张裁剪算证据 |
| `mine` / `serve` / `train` | 候选排序、网页、外部训练器 |

除 `pipeline` 外，所有节的键都对照 `reid_annotation_tool/config.py` 的 `DEFAULTS`
校验：**写错一个键会直接报错，而不是被静默忽略** —— 一个被无视的阈值笔误正是那种
会安静产出错误数据集的错误。默认值就是本工具历史上的 CLI 默认值。

单次覆盖：`python app.py extract --set splits.split=val --set crops.min_blur=40`。

> 迁移：旧的 `reid-annotation <子命令> --一长串-flag` 与 `run --stage` 适配器已移除。
> 检测/跟踪相关的旧 flag（`--detector`、`--class-id`、`--imgsz`、`--conf`、
> `--max-age`、`--appearance-weight`、`--reid-onnx` 等 21 个）现在属于 `pipeline:` 节；
> 其余按上表归入对应的节。`run --stage` 的用途已被流水线脚本契约取代。

## 低精度约束

候选默认只从 `train` 挖掘，因为 `val` / `test` 是不可扩充的受保护划分。
如需单独审计受保护划分，可显式设置 `mine.splits`；这些答案会参与冲突检测，
但 finalize 会跳过它们，保证 `val` / `test` 清单逐行不变。

候选挖掘和部署模型应使用轻量骨干。`mine.reid_preprocess: letterbox-bgr`（默认）
与 Jetson engine 的 GPU 预处理逐位一致（letterbox 灰 114、`/255`、BGR、
不做 ImageNet 归一化），因此这里排序用的像素就是部署时模型看到的像素；
第三方按常规训练的模型请改用 `imagenet-rgb`。

正式模型必须分别保存 FP16/INT8 的指标，不允许仅用 FP32 结果代替 Jetson 验收。
INT8 必须使用独立的目标域校准图片清单。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`tests/test_ingest.py` 端到端跑通"别人的跟踪数据进、数据集出"这条主路径，
全程不需要检测器、不需要 ReID 模型、不需要 GPU —— 模型是用户的，模型不在
不应该妨碍工作台工作。

## License

Apache License 2.0，见 [LICENSE](LICENSE)。
