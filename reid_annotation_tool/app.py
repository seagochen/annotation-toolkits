"""The one entry point: every stage of the workbench, driven by one project file.

``python app.py`` with no arguments opens the annotation web app on whatever
this project's dataset currently holds. Every other stage is one word. There
are no per-stage flag walls any more: paths are stated once under ``dataset:``
and derived from there, and tuning lives in named sections of ``reid.yaml``
(see config.py), so a stage takes only what genuinely varies per invocation.

    python app.py                # 起标注网页（默认）
    python app.py status         # 数据集现状与建议的下一步
    python app.py init           # 写一份 reid.yaml 模板
    python app.py extract        # 用你自己的跟踪脚本接入录像，产出数据集
    python app.py mine           # 挖掘下一轮审核候选
    python app.py check          # 逻辑冲突检测
    python app.py finalize       # 把已审核结论烤进新的 pairs 清单
    python app.py train          # 桥接外部训练器

Anything in the file can still be overridden for one run with
``--set section.key=value``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as project_config
from .config import ConfigError, Project

TEMPLATE = '''# ReID 标注工作台项目文件。相对路径都相对本文件所在目录。
# 每个键的含义与默认值见 reid_annotation_tool/config.py 的 DEFAULTS。

dataset: ./reid-dataset          # 数据集根目录（所有阶段共用）

# 检测 / 跟踪 / ID 分配由你自己的脚本负责，本工具只接收它输出的框。
# script 可以是一个 .py 路径，也可以是随包附带的参考实现名：
#   ultralytics    —— Ultralytics 检测器 + 内置 IoU 跟踪器（开箱即用）
#   tracking_csv   —— 直接读你已有系统导出的跟踪结果 CSV
pipeline:
  script: tracking_csv
  file: ./tracks.csv

extract:
  videos_dir: ./recording
  pattern: "*.mp4"
  frame_stride: 1

splits:
  split_ratios: [0.7, 0.15, 0.15]
  # timestamp_regex: 'seg-(\\d{8})-(\\d{6})\\.mp4'

# 相机标定（头→身体投影）。换场景必须重新测量，默认值只对一台相机成立。
# projection:
#   nadir_left: [587.7, 455.2]
#   nadir_right: [612.3, 455.2]

mine:
  reid_onnx: ./rough-reid.onnx

serve:
  host: 127.0.0.1
  port: 8000
'''

STAGES = ("serve", "status", "init", "extract", "mine", "check", "finalize",
          "train", "purge-domain")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        prog="reid-annotation", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    value.add_argument("stage", nargs="?", default="serve", choices=STAGES,
                       help="默认 serve（启动标注网页）")
    value.add_argument("--config", type=Path, help="项目文件（默认向上查找 reid.yaml）")
    value.add_argument("--set", action="append", default=[], dest="overrides",
                       metavar="SECTION.KEY=VALUE", help="本次运行覆盖配置中的一个键")
    value.add_argument("--port", type=int, help="serve: 覆盖端口")
    value.add_argument("--apply", action="store_true", help="purge-domain: 真正写入")
    value.add_argument("--dry-run", action="store_true", help="train: 只生成配置不训练")
    value.add_argument("--strict", action="store_true", help="check: 警告也算失败")
    value.add_argument("--json", action="store_true", help="check: 输出完整 JSON")
    return value


def open_project(args) -> Project:
    project = project_config.load(project_config.find(args.config))
    for override in args.overrides:
        project_config.apply_override(project.sections, override)
    return project


# --------------------------------------------------------------- stages

def stage_init(args) -> int:
    target = Path(args.config) if args.config else Path("reid.yaml")
    if target.exists():
        print(f"{target} 已存在，未覆盖。")
        return 1
    target.write_text(TEMPLATE, encoding="utf-8")
    print(f"已写入 {target}。改好 dataset / pipeline 两节后即可 `python app.py status`。")
    return 0


def stage_status(project: Project) -> int:
    value = project.summary()
    print(f"ReID 工作台 · {value['dataset']}")
    print(f"  配置    {value['config']}")
    if not value["exists"]:
        print("  数据集目录尚不存在 —— 先跑 `python app.py extract`。")
        return 0
    print(f"  轨迹 {value['tracks']}   裁剪 {value['identities']}   "
          f"pairs {value['pairs']}")
    if value["live_round"]:
        print(f"  当前轮 {value['live_round']}   已标注 {value['labelled']}   "
              f"待审核 {value['pending']}")
    print(f"  历史轮次 {', '.join(value['rounds']) or '（无）'}")
    print(f"下一步:  python app.py {next_step(value)}")
    return 0


def next_step(value: dict) -> str:
    """What this dataset is actually waiting for, in one word."""
    if not value["identities"]:
        return "extract"
    if not value["live_round"]:
        return "mine"
    if value["pending"]:
        return "serve"
    return "check"


def stage_serve(project: Project, args) -> int:
    from .server import serve

    live = project.live_round()
    if live is None:
        print("还没有审核轮次。先跑 `python app.py mine` 生成候选。")
        return 1
    settings = project.stage("serve")
    port = args.port or settings.port
    rounds = project.rounds()
    print(f"候选  {live.relative_to(project.dataset)}（自动发现）")
    print(f"历史  {', '.join(path.parent.name for path in rounds)}（全部纳入冲突检测）")
    serve(project.dataset, live, settings.host, port, project.base_pairs, rounds)
    return 0


def stage_extract(project: Project, args) -> int:
    from .extract import extract, parse_day_splits

    values = project.stage("extract")
    script, pipeline_config = project.pipeline()
    videos = [project.resolve(path) for path in values.video]
    if values.videos_dir:
        videos += sorted(project.resolve(values.videos_dir).glob(values.pattern))
    if not videos:
        raise SystemExit("extract: 没有匹配到任何录像（检查 extract.videos_dir / pattern）")
    values.videos = videos
    # The dataset root is where a dataset goes; saying it twice is how the two
    # drift apart.
    values.out = project.resolve(values.out) if values.out else project.dataset
    values.pipeline, values.pipeline_config = script, pipeline_config
    values.split_ratios = dict(zip(("train", "val", "test"), values.split_ratios))
    values.frame_stride = max(1, int(values.frame_stride))
    try:
        values.day_splits = parse_day_splits(values.day_split)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    extract(values)
    return 0


def stage_mine(project: Project, args) -> int:
    from .mine import mine

    values = project.stage("mine")
    if not values.reid_onnx:
        raise SystemExit("mine: 需要 mine.reid_onnx（只用于给候选排序，永远不产生标签）")
    values.reid_onnx = project.resolve(values.reid_onnx)
    values.base_pairs = "pairs.csv"
    values.output_dir = values.output_dir or project.next_round()
    values.reviews = [str(path.relative_to(project.dataset)) for path in project.rounds()]
    print(f"新一轮 -> {values.output_dir}"
          f"（沿用 {len(values.reviews)} 轮已有答案）")
    mine(project.dataset, values)
    return 0


def stage_check(project: Project, args) -> int:
    from . import conflicts as engine

    value = engine.report(project.dataset, project.base_pairs, project.rounds())
    print(json.dumps(value, ensure_ascii=False, indent=2) if args.json
          else engine.main_text(value))
    return int(value["errors"] > 0 or (args.strict and value["warnings"] > 0))


def stage_finalize(project: Project, args) -> int:
    from .core import atomic_write_json, finalize_reviews

    live = project.live_round()
    if live is None:
        raise SystemExit("finalize: 还没有任何审核轮次")
    tag = live.parent.name
    output = project.dataset / f"pairs.reviewed-{tag}.csv"
    report = project.dataset / f"pairs.reviewed-{tag}.report.json"
    result = finalize_reviews(project.dataset, project.base_pairs, project.rounds(),
                              output, report, frozenset({"train"}))
    atomic_write_json(project.dataset / "pairs.current.json", {
        "schema": 1, "pairs": output.name, "sha256": result["output_sha256"],
        "review_round": tag,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"清单 -> {output}")
    return 0


def latest_pairs(project: Project) -> str:
    """The newest baked manifest, or the raw one when nothing is baked yet."""
    pointer = project.dataset / "pairs.current.json"
    if pointer.is_file():
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            selected = (project.dataset / value["pairs"]).resolve()
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ConfigError(f"invalid current pairs pointer: {pointer}: {error}") from error
        root = project.dataset.resolve()
        if selected.parent != root or not selected.is_file():
            raise ConfigError(f"current pairs pointer names an invalid file: {selected}")
        from .core import sha256
        if value.get("sha256") and sha256(selected) != value["sha256"]:
            raise ConfigError(f"current reviewed pairs changed after finalize: {selected}")
        return selected.name
    baked = list(project.dataset.glob("pairs.reviewed-*.csv"))
    newest = max(baked, key=lambda path: path.stat().st_mtime_ns, default=None)
    return newest.name if newest else "pairs.csv"


def stage_train(project: Project, args) -> int:
    from .train import train

    values = project.stage("train")
    values.pairs = values.pairs or latest_pairs(project)
    values.review = [str(path.relative_to(project.dataset)) for path in project.rounds()]
    values.trainer = project.resolve(values.trainer) if values.trainer else Path(".")
    values.python = values.python or sys.executable
    values.base_config = project.resolve(values.base_config) if values.base_config else None
    values.dry_run = args.dry_run or values.dry_run
    print(f"pairs -> {values.pairs}")
    manifest = train(project.dataset, values)
    return int(manifest.get("exit_code", 0) or 0)


def stage_purge(project: Project, args) -> int:
    from .domain import purge

    rounds = project.rounds()
    if not rounds:
        raise SystemExit("purge-domain: 还没有任何审核轮次")
    result = purge(project.dataset, rounds, project.base_pairs, args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("（试运行；加 --apply 才会写入）")
    return 0


DISPATCH = {
    "serve": stage_serve, "status": lambda project, args: stage_status(project),
    "extract": stage_extract, "mine": stage_mine, "check": stage_check,
    "finalize": stage_finalize, "train": stage_train, "purge-domain": stage_purge,
}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.stage == "init":
        return stage_init(args)
    try:
        project = open_project(args)
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    if args.stage == "serve":
        stage_status(project)
        print()
    return DISPATCH[args.stage](project, args)


if __name__ == "__main__":
    raise SystemExit(main())
