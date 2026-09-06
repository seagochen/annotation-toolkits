# ReID Annotation Tool

这是 Annotation Toolkits 当前 ReID 后端的详细使用文档。平台概览和目录说明见仓库根目录
的 `README.md`。以下命令如无特别说明，均可在仓库根目录使用 `python backend/app.py`
执行；进入 `backend/` 后可继续使用简写 `python app.py`。

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
      -> persistent human review in the React annotation workspace
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
- 标注通过临时文件、`fsync`、原子替换保存，刷新工作台不会丢失。

## 部署

### 1. 环境要求

- Python ≥ 3.10（`pyproject.toml` 的硬性要求）。
- 依赖分两档，按机器角色选：
  - **只做标注 / 审计 / finalize**（启动平台、跑 `check`、跑 `finalize`）：
    只需要基础安装。冲突引擎和配对清单不需要 opencv / onnxruntime / ultralytics。
  - **要跑 `extract` / `mine`**（读录像、切裁剪、跑 ReID 模型给候选排序）：
    还需要 `numpy` / `opencv-python` / `onnxruntime`；用随包的 `ultralytics`
    参考流水线时再加 `ultralytics`（自带跟踪脚本的话不需要）。

### 2. 获取代码并安装依赖

```bash
git clone <本仓库地址> annotation-toolkits
cd annotation-toolkits/backend

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

### 4. 启动统一标注平台

在根目录按 `README.md` 启动 FastAPI 与 React，然后在项目详情页进入审核或运行
`extract`、`mine`、`check`、`finalize`、`purge-domain`、`train`。任务在后台执行，
日志和结果持久化到 `<dataset>/.jobs/`；整个平台进程同一时间只运行一个动作。

旧 `python app.py serve` 和标准库 HTTP 页面已经移除。旧配置中的 `serve:` 节仍可读取，
但会被忽略；监听地址改由 Uvicorn 启动参数控制。

### 命令一览

| 命令 | 作用 |
| --- | --- |
| `python app.py` | 数据集现状与下一步（= `status`） |
| `python app.py status` | 数据集现状与下一步 |
| `python app.py extract` | 用你的流水线脚本接入录像，产出数据集 |
| `python app.py mine` | 挖掘下一轮审核候选 |
| `python app.py check` | 逻辑冲突检测（有 error 返回 1） |
| `python app.py finalize` | 把已审核结论烤进新的 pairs 清单 |
| `python app.py train` | 把数据集交给外部训练器（本工具不训练模型） |
| `python app.py evaluate` | 把 checkpoint 交给外部评估器（本工具不计算指标） |
| `python app.py purge-domain` | 归档跨天 / 跨相机关系 |

单次覆盖配置用 `--set 节.键=值`，例如
`python app.py extract --set crops.min_blur=40`。

完整工作流是下面的 1-6 节，按顺序跑一遍：接入流水线 → 抽取数据集 → 挖掘并标注
→ 冲突检测 → 汇总并重新训练 → 评估训练产出。

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

### 例子：Ultralytics 检测 + supervision（Roboflow）ByteTrack 跟踪

随包的 `ultralytics` 用的是本工具内置的 IoU 跟踪器。下面这个例子把跟踪换成
`supervision` 的 ByteTrack，顺便展示一个自己写的脚本完整长什么样。

以最常见的行人 ReID 为例，整条链路是四步：

1. **检测** —— 用 Ultralytics 的 YOLO 在每一帧里框出人；
2. **跟踪** —— 把逐帧的框串成轨迹，每条轨迹一个 `track_id`。这里用 Roboflow 的
   `supervision` 库里的 ByteTrack；换成 SORT / DeepSORT / BoT-SORT、或 Ultralytics
   自带的 `model.track()` 都一样，本工具只关心你最后交出的 `(框, track_id)`；
3. **导入** —— 把上面两步包成一个 `process_frame`，`python app.py extract` 就会拿它
   跑完所有录像：切裁剪、判纯净度、建数据集；
4. **标注** —— `python app.py mine` 挖候选，在 React 项目页面开始标。

装依赖：

```bash
pip install ultralytics supervision
```

`my_tracker.py`（完整可跑，要换模型只改中间那几行）：

```python
"""Ultralytics 检测 + supervision（Roboflow）ByteTrack 跟踪。"""

from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO

from reid_annotation_tool.contract import Observation
from reid_annotation_tool.geometry import iou

_state: dict = {}


def _setup(config: dict) -> dict:
    """模型只加载一次。config 就是 reid.yaml 里 pipeline: 节除 script 外的所有键。"""
    if "model" not in _state:
        _state["model"] = YOLO(config["weights"])
        _state["classes"] = list(config.get("class_ids", [0]))   # COCO 的 0 是 person
        _state["conf"] = float(config.get("conf", 0.25))
        _state["imgsz"] = int(config.get("imgsz", 640))
        _state["device"] = str(config.get("device", "cpu"))
    return _state


def open_source(source, config: dict) -> None:
    """每段录像换一个新 tracker —— track_id 绝不能跨录像复用。"""
    _setup(config)
    _state["tracker"] = sv.ByteTrack(frame_rate=max(1, round(source.fps)))


def process_frame(image: np.ndarray, source, config: dict) -> list[Observation]:
    state = _setup(config)
    if "tracker" not in state:
        open_source(source, config)

    # 1. 检测。image 是 BGR 原帧，返回的坐标就是原帧像素，不需要任何换算。
    result = state["model"].predict(image, imgsz=state["imgsz"], conf=state["conf"],
                                    classes=state["classes"], device=state["device"],
                                    verbose=False)[0]
    detections = sv.Detections.from_ultralytics(result)

    # 2. 跟踪。ByteTrack 只吐出它认账的那部分框，每个带 tracker_id；带 track_id
    #    的框才会成为身份。
    tracked = state["tracker"].update_with_detections(detections)
    found = [Observation(box, track_id=int(track_id), class_id=int(class_id),
                         confidence=float(conf))
             for box, track_id, class_id, conf
             in zip(tracked.xyxy, tracked.tracker_id, tracked.class_id,
                    tracked.confidence)]

    # 3. 旁观者。没被跟踪上的检出框也要交回去：它们不会成为身份，但会否决被自己
    #    污染的裁剪 —— 少交一个，遮挡就漏判了。
    identities = [item.box for item in found]
    found += [Observation(box, class_id=int(class_id), confidence=float(conf))
              for box, class_id, conf
              in zip(detections.xyxy, detections.class_id, detections.confidence)
              if all(iou(box, kept) < 0.7 for kept in identities)]
    return found
```

配上 `reid.yaml`：

```yaml
pipeline:
  script: ./my_tracker.py
  weights: /abs/path/to/yolo11m.pt   # 以下这些键全属于你的脚本，本工具不校验
  class_ids: [0]
  conf: 0.3
  device: cuda:0
  imgsz: 1280

projection:
  extent_k: [1.0, 1.0, 1.0]          # 检出框已经是全身，关掉头→身体投影；见下一节
```

> `pipeline:` 是唯一的开放节，本工具不解析它里面的相对路径（只有随包适配器的
> `detector` / `reid_onnx` / `file` 这几个已知键会按项目文件解析）。自定义脚本请写
> 绝对路径，或者自己在脚本里解析。

然后跑：

```bash
python app.py extract     # 全部录像 -> images/ + identities.csv + pairs.csv + manifest.json
python app.py mine        # 挖候选 -> review/v1/candidates.csv
# 在 Annotation Toolkits React 工作台打开项目开始标注
```

三个容易踩的点：

- **旁观者必须交回来。** ByteTrack 之类的跟踪器只会吐出确认的轨迹，被它丢掉的低分
  检测在本工具眼里仍然有用 —— 它们是判断遮挡的唯一依据。脚本里标了 `# 3.` 的那段
  就是干这个的：
  凡是没被某条轨迹认领的检出框，都作为不带 `track_id` 的 `Observation` 交回。
- **轨迹何时结束由本工具判定**，不由脚本判定（连续 `extract.track_gap` 个已处理帧
  没再出现即视为结束），所以脚本不需要、也不应该自己发"轨迹结束"的信号。
- **每段录像重建跟踪器**，`open_source` 钩子就是为此存在的。`person_id` 里已经带了
  录像前缀，所以 `track_id` 跨录像重复本身不是问题；问题是上一段录像遗留的轨迹状态
  会把新录像开头的框错误地续到旧轨迹上。同一段录像内部跟踪器回收 id 时本工具会加
  代号后缀（`..._t00007g2`），保证两个目标不会共用一个 `person_id`。

### 命名模型：`models:` 节

多个脚本、多次训练/评估经常要指向同一份权重。与其在 `pipeline:`、`train.base_config`、
自己的代码里各抄一遍路径，可以在 `models:` 节里给它起个名字：

```yaml
models:
  head_detector:
    path: ./weights/yolo11n.pt   # 相对路径按本文件所在目录解析
    framework: pytorch           # 目前只支持 pytorch（.pt），默认值
    device: cpu
```

随包的 `ultralytics` 参考流水线已经支持把 `pipeline.detector` 写成注册表里的名字
而不是字面路径：

```yaml
pipeline:
  script: ultralytics
  detector: head_detector        # 等价于直接写 ./weights/yolo11n.pt
```

自己写脚本的话，`process_frame` 声明第四个参数就能拿到这个注册表（`open_source` /
`detect_crops` 同理声明多一个参数即可）；不声明的脚本完全不受影响，本工具不会塞给它：

```python
def process_frame(image, source, config, registry) -> list[Observation]:
    weights = registry.resolve_path("head_detector")   # 已解析好的绝对路径
```

`models` 和 `pipeline` 一样是开放节（本工具不知道你的模型需要哪些参数），但每条
条目的 `path` / `framework` / `device` / `kind` 四个键会被校验，写错会直接报错。

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

### 裁剪几何：裁哪个框由你决定，但一个数据集只能有一种几何

裁剪框只有一条代码路径 —— `projection:` 节的投影 —— 但这条路径**做什么完全由参数
决定**，所以本工具并不规定你必须怎么裁。两种典型情形：

**情形 A：你的框已经是全身**（最常见，行人检测 + SORT / ByteTrack / DeepSORT）

```yaml
projection:
  extent_k: [1.0, 1.0, 1.0]     # 扩张因子恒为 1，投影退化成恒等变换
```

裁剪就是你脚本返回的框本身，一个像素都不动。想统一留一点边，用
`crops.crop_margin`（默认 `0.0`，相对边长，比如 `0.08`）。

**情形 B：你的框标的是头**（本工具服务的生产部署就是这种）

ReID 模型训练在全身上，而身体矩形不是头框的固定倍数 —— 它取决于头相对相机 nadir 的
位置，所以从头还原身体需要场景标定：

| `projection:` 键 | 默认 | 含义 |
| --- | --- | --- |
| `projection_space: [W, H]` | `[1200, 538]` | 标定所在的空间；帧分辨率任意，会缩放进出 |
| `seam_x` | `600.0` | 主导 nadir 换边的列 |
| `nadir_left` / `nadir_right` | `[587.7, 455.2]` / `[612.3, 455.2]` | 镜像 nadir 对 |
| `extent_k: [K_MIN, K_REF, K_MAX]` | `[1.0, 2.5, 5.0]` | 头→身体扩张因子的下限 / R_REF 处取值 / 上限 |
| `extent_r_ref` | `250.0` | 扩张因子等于 `K_REF` 时的头–nadir 距离 |

默认值是那套部署的实测标定值，**换场景必须自己重新测**：照抄默认值只会得到系统性
错位的裁剪。`tests/test_projection.py` 用黄金向量把本实现钉在生产部署的
`surv.identity.state.head_to_body` 上，两边公式一旦漂移即刻失败。

不管走 A 还是 B，标定值全部写进 `manifest.json` 的 `contract.body_projection`，所以
任何一张裁剪都能从 `identities.csv` 的框列重新还原。

⚠️ **几何是 per-dataset 的，不是 per-video 的开关。** 两种几何产出的裁剪不可互换：
任何在一种几何上标定的绝对 cosine 门，在另一种上都是静默失效的（门看起来没动，
实际已经松了）。换几何等于重新抽取整个数据集，不要在同一份数据里混用。

裁剪防火墙（可用 `crops.no_crop_firewall: true` 关闭）：

1. 逐帧检查**最终裁剪框**与其他目标裁剪框的 IoU / 包含率，被遮挡即丢弃该轨迹
   （判纯净度必须按最终裁剪框：情形 B 下两个人的头可以隔得很远，而其中一个的身体
   裁剪把另一个整个吞进去）；这一步用的是脚本返回的**全部**框，包括没有 track_id
   的旁观者；
2. 检查运动是否合理（位移、面积突变）—— 这一步用脚本**原样返回的框**，它是实测量，
   投影后的框会继承投影在 seam 附近的敏感性，把正常走动误判为异常；
3. 对完成的裁剪再跑一次检测（脚本的 `detect_crops` 钩子），剔除仍含第二个目标的裁剪，
   超过 `crops.reject_track_multi_crops` 张即丢弃整条轨迹。**是否算污染由本工具判定**，
   脚本只负责给出检测结果——这条规则不能随流水线变化。脚本没有 `detect_crops` 时
   本步跳过，并在 `manifest.json` 里写明"该阶段不可用"，而不是假装裁剪通过了检查。

⚠️ **走情形 B 时先重标 `crops.max_neighbour_iou` / `max_neighbour_contained`。**
这两个默认值（0.10 / 0.35）是按检出框调的，对投影身体框偏紧：同一段录像 570 次观测
实测，头框几何拒 17.5%，身体框几何在同样阈值下拒 45.3%，要回到 ~18% 需放到约
`0.40 / 0.65`。且轨迹级拒绝是黏性的（任何一帧不合格整条丢），所以这一门会成为身体框
几何下的主要过滤器。按场景重新标定后再大批量抽取。情形 A 用默认值即可。

划分按**视频**分配（`splits.split_ratios`，或 `splits.split` 强制），因此同一个目标
不会同时出现在 train 和 val。

抽取阶段的输出（`dataset:` 目录下；标注和训练还会往里写东西，完整目录结构见
第 5 节"数据集长什么样"）：

```text
<out>/images/<split>/<person_id>/NN.jpg
<out>/identities.csv     img_path,person_id,split,video,track_id,class_id,timestamp,frame,x1..y2,conf,crop_w,crop_h,blur,brightness,over_exposed,under_exposed
<out>/tracks.csv         person_id,split,video,track_id,class_id,start,end,frames,crops,recovered,status,reason
<out>/covisibility.csv   person_id1,person_id2,video,split,frames,first_timestamp
<out>/pairs.csv          img1,img2,label,split,evidence,person_id1,person_id2,gap_sec
<out>/manifest.json      契约、流水线脚本与其 SHA-256、每个划分的统计
```

## 3. 挖掘候选并在 React 工作台标注

```bash
python app.py mine        # 自动写到下一轮 review/vN，并沿用所有历史轮次的答案
# 在 React 项目页面打开当前待审队列
```

挖掘两类问题（模型只提问，不回答）：

- `cross_track`：两条轨迹看起来很像但从未被证明相关 —— 是同一个目标吗？
- `track_purity`：一条轨迹内部的裁剪互相不像 —— 跟踪是否中途换了目标？
  标 `different` 表示轨迹被污染，`finalize` 时整条轨迹连同其配对一起丢弃。

`candidate_id` 由内容哈希生成，因此换模型重新挖掘时，已标注的答案会自动保留
（`report.json` 中的 `preserved_labels`）。

React 工作台：

- 显示双方轨迹证据画廊，支持 `same`、`different`、`unclear` 和审核备注；
- 快捷键 `1`、`2`、`3` 对应三种判定；
- 保存失败会保留当前候选并允许幂等重试；不同判定不会静默覆盖已保存结果；
- 每次标注都原子写回现有 `candidates.csv`，CLI 可直接读取；
- 冲突详情与关系修订目前通过 `python app.py check` 和后续平台能力处理。

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
（需重新抽取才能改变），其余关系可通过对应的 `candidate_id` 追溯到审核轮次。

`covisible_merge` 与 `transitive_negative` 是互补的：`pairs.csv` 里的负样本按
`--negative-ratio` 采样，未被采样的共现关系仍然是物理事实，因此单独检查。

## 5. 汇总标注并重新训练

```bash
python app.py finalize    # -> pairs.reviewed-<当前轮>.csv 及其 report
python app.py train       # 交给 reid.yaml 的 train.trainer 指定的训练器
```

`finalize` 把人判烤进一份新的配对清单：基线 `pairs.csv` 的每一行照抄，标成 `same` /
`different` 的候选作为新行追加（`evidence` 记为 `reviewed_model_mined_*`），被判定为
污染的轨迹连同它的全部配对一起删除。`val` / `test` 是受保护划分，逐行不变，变了直接
报错。新清单的名字和 sha256 写进 `pairs.current.json`，`train` 默认就取它。

### 数据集长什么样

一轮走完之后，`dataset:` 目录下是这些东西：

```text
<root>/
├── images/<split>/<person_id>/NN.jpg   裁剪本体，<split> 是 train / val / test
├── identities.csv                      每张裁剪一行：是谁、来自哪一帧的哪个框、质量指标
├── tracks.csv                          每条轨迹一行：起止时间、帧数、留下几张裁剪、被拒就写原因
├── covisibility.csv                    同帧共现过的身份对（物理证据：必然不是同一个人）
├── pairs.csv                           抽取阶段的基线配对清单
├── manifest.json                       契约：流水线脚本及其 SHA-256、裁剪几何标定、各划分统计
├── review/v1/candidates.csv            第 N 轮候选 + 人工答案（review_label / review_notes）
├── review/v1/report.json
├── pairs.reviewed-v1.csv               finalize 产出，可直接拿去训练的清单
├── pairs.reviewed-v1.report.json
├── pairs.current.json                  指向当前该用哪份清单（含 sha256）
└── training/<name>/
    ├── config.yaml                     本工具生成、交给你训练器的配置
    ├── training-manifest.json           这次训练吃了哪份清单（sha256）、门禁结果、产出权重
    └── runs/                           训练器自己的输出目录
```

`identities.csv` / `tracks.csv` / `covisibility.csv` 的列名见第 2 节末尾。

`person_id` 形如 `cam16_d20260830_v210002-9f2c1ab3d4_t00007`：域（相机 + 自然日）+
录像 + 轨迹号。跨天跨相机不会碰撞，也能直接按域筛选（`purge-domain` 就靠这个前缀）。

配对清单（`pairs.csv` / `pairs.reviewed-*.csv`）的列：

| 列 | 含义 |
| --- | --- |
| `img1` / `img2` | 两张裁剪，相对 `<root>` 的路径 |
| `label` | `1` 同一目标，`0` 不同目标 —— 只有这两个值，未决的行不会出现在这里 |
| `split` | `train` / `val` / `test` |
| `evidence` | 这一行凭什么成立：同一条连续轨迹、同帧共现、还是 `reviewed_*`（人判） |
| `person_id1` / `person_id2` | 两张裁剪各自的身份 |
| `gap_sec` | 两张裁剪的时间间隔 |

### 要不要为自己的模型改数据结构

**不用改本工具的输出。** 两条路选一条：

**路 1：用 `python app.py train` 这个桥。** 本工具自己不训练模型 —— 它生成配置、跑一遍
门禁、然后调用你的训练器。你的训练器需要满足：

- `train.trainer` 指向的目录下有 `scripts/train.py`，接受 `--config <yaml>`；
- 从那份配置里读 `data.reid_root`（数据集根目录）和 `data.reid_csv`（相对它的清单
  文件名），自己去读 CSV；
- 把权重写到 `<root>/training/<name>/runs/<tasks>/<name>/weights/*.pt`（目录名以
  `<name>` 开头就行，训练器爱加时间戳后缀也认），本工具按 `best_reid.pt` →
  `best.pt` → 第一个 `.pt` 的顺序挑出最好的那个记进 manifest；
- 想让 `train.export: true` 生效，再提供 `scripts/export_onnx.py --weights <pt>`。

那份 `config.yaml` 就是**你训练器自己的 YAML**，本工具照抄一遍，只补上它独有的
四个键：`data.reid_root`、`data.reid_csv`、`output.project`、`output.name`。

```yaml
train:
  trainer: /path/to/my-trainer      # 目录下要有 scripts/train.py
  python: /path/to/env/bin/python
  name: scene_reviewed_v1
  base_config: ./my-trainer-reid.yaml   # 你训练器的配置，原样透传
  set:                                  # 单次覆盖它的任意字段，本工具不校验
    - model.backbone=osnet_x0_25
    - train.reid_epochs=300
```

⚠️ **backbone / lr / epochs / batch_size 这类键不属于 `train:` 节。** 它们是训练器的
超参，不是数据集的属性，所以本工具既不定义它们的默认值也不校验它们 —— 全部走
`base_config` + `set`。旧配置里带着这些键会直接报错，并告诉你该往哪搬：

```text
unknown `train` keys ['backbone', 'reid_epochs', 'reid_lr']; known: [...]
  ['backbone', 'reid_epochs', 'reid_lr'] belong to your trainer, not to the
  dataset: put them in the YAML named by `train.base_config`, or pass them
  with `train.set: ["section.key=value"]`
```

反过来，那四个键**不能**用 `set` 覆盖：一个训练在别处数据上、却记录成这份清单的
run，会让整条追溯链变成谎话，所以本工具直接拒绝。

**路 2：完全不用这个桥，自己读 CSV。** 清单就是稳定契约，转成你要的格式几行就够：

```python
import pandas as pd

pairs = pd.read_csv("datasets/reid/pairs.reviewed-v1.csv")
train_pairs = pairs[pairs.split == "train"][["img1", "img2", "label"]]
```

要 Market-1501 那种"一个身份一个目录"的分类式结构也很省事 —— `images/` 已经按
`person_id` 分好目录了。但这里有个坑必须先知道：

> ⚠️ **`person_id` 是轨迹级的，不是人级的。** 同一个人在不同录像里、或在跟踪断开
> 前后，会拿到不同的 `person_id`；人工标注的 `same` 判定正是把它们连起来的那条边。
> 所以要用身份分类损失（ID loss / ArcFace 之类）训练，必须先对清单里 `label==1` 的边
> 做一次并查集闭包，用连通分量当类别，**不能直接把 `person_id` 当类别** —— 否则同一
> 个人会被当成好几个类互相推开，正好训反。

```python
from pathlib import Path
from reid_annotation_tool.core import build_constraints, read_csv

root = Path("datasets/reid")
graph, _, conflicts = build_constraints(read_csv(root / "pairs.reviewed-v1.csv"), [])
assert not conflicts, conflicts          # 有冲突说明标注自相矛盾，先复核审核结果
label_of = {row["img_path"]: graph.find(row["person_id"])
            for row in read_csv(root / "identities.csv")}
```

`graph.find(person_id)` 返回连通分量的代表元，把它当类别即可；从未被合并过的身份
返回自己，所以单例也能正常处理。

`train` 在启动训练前会：

1. 跑一遍冲突检测，有 error 就拒绝训练（`train.allow_conflicts: true` 可强制覆盖）；
2. 校验 `train` / `val` 两个划分都同时含有正负样本，否则训练器无法计算 ROC-AUC；
3. 生成训练器配置到 `<root>/training/<name>/config.yaml`（= `train.base_config`
   加上那四个键，`train.set` 可覆盖其余任意字段，`--dry-run` 只生成不训练）；
4. 训练结束后写 `training-manifest.json`：pairs 的 sha256、冲突门禁结果、
   训练器 git revision、产出权重与 ONNX 的 sha256 —— 任何一个 checkpoint
   都能追溯回产生它的标注。

导出的 ONNX 可以直接写回 `mine.reid_onnx`，形成闭环。

## 6. 评估已训练的 checkpoint

`train` 的镜像：本工具不计算任何指标，只负责把 checkpoint 和受保护的测试集配对清单
交给外部评估器，并记录喂给它的到底是什么。

```yaml
evaluate:
  evaluator: /path/to/my-evaluator   # 目录下要有 scripts/evaluate.py
  python: /path/to/env/bin/python
  checkpoint: ./training/deploy/mobilenetv3-reid.pt
  name: mobilenetv3_v1
  split: test                        # 默认就是 test；val 也可以，显式给
  base_config: ./my-evaluator-reid.yaml
```

```bash
python app.py evaluate
```

你的评估器需要满足和训练器同一套约定：

- `evaluator` 目录下有 `scripts/evaluate.py`，接受 `--config <yaml>`；
- 从配置里读 `data.reid_root` / `data.reid_csv`（测试集配对清单）和
  `checkpoint.path`，自己去读 CSV、加载 checkpoint、跑推理；
- 算完指标后写到配置里 `output.metrics_path` 指的那个 JSON 文件——本工具不猜测
  文件名，直接告诉评估器该写在哪。

那份 `config.yaml` 同样是**你评估器自己的 YAML**，本工具照抄一遍，只补上它独有的
六个键：`data.reid_root`、`data.reid_csv`、`checkpoint.path`、`output.project`、
`output.name`、`output.metrics_path`。⚠️ 这六个键同样不属于 `evaluate:` 节本身，也
**不能**用 `evaluate.set` 覆盖——道理与 `train.set` 一样：一次评估如果能被悄悄指向
别的数据集或别的 checkpoint，整条追溯链就是假的。

`evaluate` 在跑之前会：

1. 跑一遍冲突检测，有 error 就拒绝（`evaluate.allow_conflicts: true` 可强制覆盖）；
2. 校验 `evaluate.split`（默认 `test`）同时含有正负样本，否则算不出 ROC-AUC；
3. 生成评估器配置到 `<root>/evaluation/<name>/config.yaml`；
4. 跑完写 `eval-manifest.json`：pairs 的 sha256、checkpoint 的 sha256、冲突门禁结果、
   评估器 git revision，以及 `output.metrics_path` 里的内容（原样嵌入 `metrics` 字段）——
   任何一次评估都能追溯回它测的是哪个 checkpoint、用的哪份人工标注。

## 配置文件

平台可通过 `projects.yaml` 列出多个本地项目；注册表只保存平台元数据和项目配置路径，
数据集根目录及任务参数仍由各自的 `reid.yaml` 管理：

```yaml
projects:
  - id: scene-reid
    name: Scene ReID
    task_type: reid
    config: ./scene/reid.yaml
```

注册表路径按 `projects.yaml` 所在目录解析。也可以传入一个目录，将其中每个
`*.yaml`/`*.yml` 视为单独的项目条目。Python 层使用
`ProjectRegistry.load(path).list_projects()` 和 `load_project(id)`；统一 HTTP 接口使用
`GET /api/projects` 和 `GET /api/projects/{id}`。完整示例见
`configs/projects.example.yaml`。

### 任务类型插件

平台通过 `annotation_platform.task_types.TaskTypeModule` 隔离不同标注任务。模块首先用
`load(config_path)` 将自己的配置装入通用 `TaskProject`，再实现四项任务能力：

| 方法 | 通用语义 |
| --- | --- |
| `queue(project, request)` | 按分页和任务自有过滤条件返回待处理项 |
| `submit(project, submission)` | 为一个队列项原子保存结构化标注结果 |
| `status(project)` | 返回通用状态名和任务自有详情 |
| `export(project, request)` | 返回指定格式的本地产物及元数据 |

请求与结果分别使用 `QueueRequest` / `QueuePage`、`Submission` /
`SubmissionResult`、`TaskStatus`、`ExportRequest` / `ExportResult`，平台层不解释标签、
框、掩码等任务专属字段。错误统一继承 `TaskTypeError`：非法实现、重复名称、未知类型和
操作失败分别有稳定的异常类型及 `code`。

`TaskTypeRegistry` 在注册时要求小写唯一的 `type_name`，并检查上述五个方法是否可调用。
内置注册表当前只有 `reid`；其适配器复用现有 `Store` 的队列筛选与原子 CSV 写入、
`Project.summary()` 状态和当前 pairs 产物，没有复制 ReID 领域规则。新增任务类型时，先实现
该协议，再注入项目注册表或 FastAPI 的 `create_app(task_types=...)`。

`reid.yaml`（`python app.py init` 生成，完整示例见 `configs/reid.example.yaml`）
按"谁拥有这个决定"分节：

| 节 | 归谁 |
| --- | --- |
| `dataset` | 数据集根目录，所有阶段由它推导路径 |
| `pipeline` | **你的**检测 / 跟踪脚本及其参数（本工具不校验这一节的键） |
| `extract` / `splits` | 用哪些录像、抽多密、时间与划分怎么读 |
| `projection` | 相机标定（头→身体裁剪几何） |
| `crops` / `pairs` | 什么样的裁剪可信、什么样的两张裁剪算证据 |
| `mine` / `train` / `evaluate` | 候选排序、交给外部训练器/评估器（只有接口，没有超参或指标定义） |
| `models` | 给权重文件起名字，供 `pipeline:` 或你自己的脚本按名字引用 |

除 `pipeline` / `models` 外，所有节的键都对照 `reid_annotation_tool/config.py` 的
`DEFAULTS` 校验：**写错一个键会直接报错，而不是被静默忽略** —— 一个被无视的阈值
笔误正是那种会安静产出错误数据集的错误。默认值就是本工具历史上的 CLI 默认值。
`models` 本身开放（条目形状因人而异），但每条条目内部的四个键仍会校验，见上一节。

单次覆盖：`python app.py extract --set splits.split=val --set crops.min_blur=40`。

> 迁移：旧的 `reid-annotation <子命令> --一长串-flag` 与 `run --stage` 适配器已移除。
> 检测/跟踪相关的旧 flag（`--detector`、`--class-id`、`--imgsz`、`--conf`、
> `--max-age`、`--appearance-weight`、`--reid-onnx` 等 21 个）现在属于 `pipeline:` 节；
> 其余按上表归入对应的节。`run --stage` 的用途已被流水线脚本契约取代。
>
> 迁移：`train:` 节里 19 个属于训练器的键（`backbone`、`pretrained_path`、`objective`、
> `reid_dim`、`num_classes`、`img_size`、`no_pretrained`、`reid_epochs`、`cls_epochs`、
> `reid_lr`、`cls_lr`、`backbone_lr`、`batch_size`、`workers`、`patience`、`data_cls`、
> `csv_cls`、`device`、`seed`）已移出本工具，改由 `train.base_config` + `train.set`
> 透传。带着它们加载配置会报错，并指出该往哪搬。本工具自己不训练模型，也就不该拥有
> 别人模型的超参 —— 相应地，实现这一交接的模块从 `train.py` 改名为 `handoff.py`。

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

## License

Apache License 2.0，见 [LICENSE](../LICENSE)。
