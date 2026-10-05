"""Declarative property-form specs for every built-in task type.

The web UI renders a project's property form from these specs instead of
knowing each task's config schema, and the workspace validates submitted
values against the same specs before a module ever sees them. The specs only
*describe* config keys; each module's ``load()`` stays the one authority on
what a valid config is, so a spec can never make an invalid config pass.

Keys are dotted paths into the task config YAML (``pipeline.script`` is
``pipeline: {script: ...}``).
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Literal, Mapping

from .file_kinds import KIND_SUFFIXES
from .image_dataset import DEFAULT_PATTERNS
from .text_span_task import DEFAULT_PATTERNS as TEXT_SPAN_PATTERNS

FieldType = Literal["text", "path", "integer", "number", "boolean", "select", "list"]
LockRule = Literal["none", "append_only", "locked"]
ImportMode = Literal["upload", "directory", "managed"]
UploadKind = Literal["image", "text", "json"]

# Keys whose value is a model/checkpoint file the platform (or the trainer and
# evaluator it launches) loads. Torch checkpoints are pickles, so loading one
# executes code; see workspace._check_model_paths for where they may point.
MODEL_PATH_KEYS = (
    "pipeline.detector",
    "pipeline.reid_onnx",
    "mine.reid_onnx",
    "evaluate.checkpoint",
)

# The ReID pipeline adapters shipped inside the package. Anything else in
# `pipeline.script` is a path to a Python file the platform would import and run.
BUNDLED_PIPELINES = ("ultralytics", "tracking_csv")


class ManagementError(RuntimeError):
    """A project-management request was rejected.

    Carries the HTTP status the API reports, so the workspace logic stays free
    of FastAPI while the server still maps every rejection to a stable
    ``{"code", "message"}`` body.
    """

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class FieldOption:
    value: str
    label: str


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: FieldType
    required: bool = False
    default: object = None
    options: tuple[FieldOption, ...] | None = None
    help: str | None = None
    # Applies once the project has any annotation: `append_only` because
    # segmentation mask values and COCO category ids are list positions, so
    # reordering or removing an entry silently relabels existing results;
    # `locked` because the value changes what an existing result means.
    lock: LockRule = "none"
    # Shown read-only and rejected when changed through the API -- see the
    # ReID train/evaluate fields below for why.
    server_only: bool = False
    group: str | None = None


@dataclass(frozen=True)
class ExportFormat:
    format: str
    label: str


@dataclass(frozen=True)
class TaskTypeSpec:
    type: str
    label: str
    description: str
    import_modes: tuple[ImportMode, ...]
    export_formats: tuple[ExportFormat, ...]
    fields: tuple[FieldSpec, ...]
    # Config keys that are not form fields but are just as server-only: the
    # raw config editor must not change them either (see ReID below).
    server_only_keys: tuple[str, ...] = ()
    # What an upload may carry (file_kinds.KIND_SUFFIXES); content is checked
    # against the kind its extension names.
    upload_kinds: tuple[UploadKind, ...] = ("image",)

    def upload_suffixes(self) -> frozenset[str]:
        return frozenset().union(*(KIND_SUFFIXES[kind] for kind in self.upload_kinds))

    def find_field(self, key: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.key == key:
                return spec
        return None


def _patterns(default: tuple[str, ...], help: str | None = None) -> FieldSpec:
    return FieldSpec(
        key="patterns",
        label="文件匹配模式",
        type="list",
        default=list(default),
        help=help or "相对数据集根目录的 glob 模式，用于发现待标注图片；一般保持默认即可。",
        group="高级",
    )


# Classification and captioning read images or UTF-8 text documents: which
# one is decided by `patterns`, never by a separate switch.
TEXT_PATTERNS_HELP = (
    "相对数据集根目录的 glob 模式，用于发现待标注文件。默认只匹配图片；标注文本文档"
    "（UTF-8）时改为 **/*.txt 等。图片扩展名之外的文件一律按文本展示。"
)


NATIVE = ExportFormat("native", "原生 JSON")

TASK_TYPE_SPECS: tuple[TaskTypeSpec, ...] = (
    TaskTypeSpec(
        type="classification",
        label="分类",
        description="为每张图片或每篇文本文档选择一个或多个预定义标签。",
        import_modes=("upload", "directory"),
        upload_kinds=("image", "text"),
        export_formats=(NATIVE, ExportFormat("csv", "CSV")),
        fields=(
            FieldSpec(
                key="labels",
                label="标签",
                type="list",
                required=True,
                help="可选标签列表。开始标注后只能在末尾追加，不能删除或调整顺序。",
                lock="append_only",
            ),
            FieldSpec(
                key="mode",
                label="选择模式",
                type="select",
                required=True,
                default="single",
                options=(
                    FieldOption("single", "单选（每张图恰好一个标签）"),
                    FieldOption("multi", "多选（每张图至少一个标签）"),
                ),
                help="开始标注后不可再修改。",
                lock="locked",
            ),
            _patterns(DEFAULT_PATTERNS, TEXT_PATTERNS_HELP),
        ),
    ),
    TaskTypeSpec(
        type="captioning",
        label="描述 / 文本生成",
        description="为每张图片撰写描述，或为每篇文本写出翻译、摘要等自由文本。",
        import_modes=("upload", "directory"),
        upload_kinds=("image", "text"),
        export_formats=(NATIVE, ExportFormat("csv", "CSV")),
        fields=(_patterns(DEFAULT_PATTERNS, TEXT_PATTERNS_HELP),),
    ),
    TaskTypeSpec(
        type="text_span",
        label="文本片段标注",
        description="在文本中拖选片段并标上标签（实体、关键信息等），片段可以重叠或嵌套。",
        import_modes=("upload", "directory"),
        upload_kinds=("text",),
        export_formats=(NATIVE,),
        fields=(
            FieldSpec(
                key="labels",
                label="标签",
                type="list",
                required=True,
                help="片段可用的标签列表。开始标注后只能在末尾追加，不能删除或调整顺序。",
                lock="append_only",
            ),
            _patterns(
                TEXT_SPAN_PATTERNS,
                "相对数据集根目录的 glob 模式，用于发现待标注的 UTF-8 文本文件。",
            ),
        ),
    ),
    TaskTypeSpec(
        type="detection",
        label="目标检测",
        description="在图片上用矩形框标出每个目标并指定类别。",
        import_modes=("upload", "directory"),
        export_formats=(NATIVE, ExportFormat("coco", "COCO")),
        fields=(
            FieldSpec(
                key="categories",
                label="类别",
                type="list",
                required=True,
                help="目标类别列表，顺序决定 COCO 导出的 category_id。开始标注后只能在末尾追加。",
                lock="append_only",
            ),
            _patterns(DEFAULT_PATTERNS),
        ),
    ),
    TaskTypeSpec(
        type="segmentation",
        label="图像分割",
        description="用画笔或多边形逐像素标出每个类别所占的区域。",
        import_modes=("upload", "directory"),
        export_formats=(NATIVE, ExportFormat("coco", "COCO")),
        fields=(
            FieldSpec(
                key="categories",
                label="类别",
                type="list",
                required=True,
                help="类别列表：第 N 个类别对应掩膜像素值 N（0 恒为背景），最多 254 个。"
                "开始标注后只能在末尾追加。",
                lock="append_only",
            ),
            _patterns(DEFAULT_PATTERNS),
        ),
    ),
    TaskTypeSpec(
        type="polygon",
        label="多边形标注",
        description="用可编辑的多边形勾勒每个区域，可从 COCO 预标开始逐张修正，导出标准 COCO。",
        import_modes=("upload", "directory"),
        upload_kinds=("image", "json"),
        export_formats=(NATIVE, ExportFormat("coco", "COCO")),
        fields=(
            FieldSpec(
                key="categories",
                label="类别",
                type="list",
                required=True,
                help="多边形类别列表，至少 1 个，顺序决定 COCO 导出的 category_id。"
                "开始标注后只能在末尾追加。",
                lock="append_only",
            ),
            FieldSpec(
                key="prelabels",
                label="COCO 预标文件",
                type="text",
                help="可选。数据集内一个标准 COCO 多边形 JSON 的相对路径（可在导入页上传），"
                "例如 prelabels.coco.json；按 images[].file_name 匹配图片、按类别名匹配类别。"
                "平台只读取它，不修改。",
            ),
            _patterns(DEFAULT_PATTERNS),
        ),
    ),
    TaskTypeSpec(
        type="depth",
        label="深度图修正",
        description="在预先生成的灰度深度图上用画笔抬高或压低深度值。",
        import_modes=("upload", "directory"),
        export_formats=(NATIVE,),
        fields=(
            FieldSpec(
                key="depth_maps",
                label="基线深度图目录",
                type="text",
                default=".depth-baseline",
                help="相对数据集根目录；图片 a/b.jpg 的基线深度图应位于 <该目录>/a/b.png。"
                "没有基线时从空白开始绘制。",
            ),
            _patterns(DEFAULT_PATTERNS),
        ),
    ),
    TaskTypeSpec(
        type="reid",
        label="行人重识别",
        description="从录像中抽取目标裁剪、挖掘候选对，并人工判断两张裁剪是否为同一身份。",
        # `directory`: link an already extracted dataset; `managed`: start
        # empty and let the `extract` action fill the project's data dir.
        import_modes=("directory", "managed"),
        export_formats=(NATIVE,),
        fields=(
            FieldSpec(
                key="pipeline.script",
                label="检测跟踪流水线",
                type="select",
                required=True,
                default="ultralytics",
                options=(
                    FieldOption("ultralytics", "ultralytics（内置检测 + 跟踪）"),
                    FieldOption("tracking_csv", "tracking_csv（读取已有跟踪结果 CSV）"),
                ),
                help="抽取阶段由哪个内置流水线负责检测与跟踪。",
                group="流水线",
            ),
            FieldSpec(
                key="pipeline.detector",
                label="检测模型",
                type="path",
                help="ultralytics 检测权重（.pt）的服务器路径；相对路径按配置文件所在目录解析。",
                group="流水线",
            ),
            FieldSpec(
                key="pipeline.reid_onnx",
                label="跟踪用 ReID 模型",
                type="path",
                help="可选的粗 ReID ONNX 模型，只用于收紧跟踪关联，不产生标签。",
                group="流水线",
            ),
            FieldSpec(
                key="pipeline.device",
                label="推理设备",
                type="text",
                default="cuda:0",
                help="例如 cuda:0 或 cpu。",
                group="流水线",
            ),
            FieldSpec(
                key="extract.videos_dir",
                label="录像目录",
                type="path",
                help="待抽取录像所在的服务器目录。",
                group="抽取",
            ),
            FieldSpec(
                key="extract.pattern",
                label="录像匹配模式",
                type="text",
                default="*.mp4",
                help="在录像目录中匹配录像文件的 glob 模式。",
                group="抽取",
            ),
            FieldSpec(
                key="extract.frame_stride",
                label="抽帧间隔",
                type="integer",
                default=1,
                help="每隔多少帧处理一帧；1 表示逐帧处理。",
                group="抽取",
            ),
            FieldSpec(
                key="mine.reid_onnx",
                label="候选挖掘 ReID 模型",
                type="path",
                help="用于给候选对排序的 ReID ONNX 模型。",
                group="候选挖掘",
            ),
            FieldSpec(
                key="mine.min_cosine",
                label="最小余弦相似度",
                type="number",
                default=0.8,
                help="低于该相似度的跨轨迹候选对不进入审核队列。",
                group="候选挖掘",
            ),
            FieldSpec(
                key="mine.per_split",
                label="每个划分的候选数",
                type="integer",
                default=200,
                help="每轮为每个数据划分挖掘的候选对数量上限。",
                group="候选挖掘",
            ),
            FieldSpec(
                key="train.name",
                label="训练运行名",
                type="text",
                default="reviewed",
                help="交给外部训练器的运行名称，决定训练输出目录。",
                group="训练与评估",
            ),
            # The four server-only keys name programs the platform executes
            # (a trainer/evaluator checkout and the Python interpreter that runs
            # it). The HTTP API has no authentication, so letting it edit them
            # would be remote code execution; they can only be changed by
            # editing the config file on the server.
            FieldSpec(
                key="train.trainer",
                label="训练器目录",
                type="path",
                help="外部训练器仓库路径。仅能在服务器上直接编辑配置文件修改。",
                server_only=True,
                group="训练与评估",
            ),
            FieldSpec(
                key="train.python",
                label="训练 Python 解释器",
                type="path",
                help="运行训练器的 Python 解释器。仅能在服务器上直接编辑配置文件修改。",
                server_only=True,
                group="训练与评估",
            ),
            FieldSpec(
                key="evaluate.evaluator",
                label="评估器目录",
                type="path",
                help="外部评估器仓库路径。仅能在服务器上直接编辑配置文件修改。",
                server_only=True,
                group="训练与评估",
            ),
            FieldSpec(
                key="evaluate.python",
                label="评估 Python 解释器",
                type="path",
                help="运行评估器的 Python 解释器。仅能在服务器上直接编辑配置文件修改。",
                server_only=True,
                group="训练与评估",
            ),
            FieldSpec(
                key="evaluate.checkpoint",
                label="评估权重",
                type="path",
                help="待评估的模型权重文件路径。",
                group="训练与评估",
            ),
        ),
        # Not form fields, but the raw config editor must not change them:
        # `extract.out`/`mine.output_dir` redirect where the actions write
        # (anywhere the server can write), and the `base_config`/`set` keys
        # rewrite the external trainer's/evaluator's own config -- including
        # which weights it unpickles. Over an unauthenticated API both are a
        # write-anywhere or code-execution route.
        server_only_keys=(
            "extract.out",
            "mine.output_dir",
            "train.set",
            "train.base_config",
            "evaluate.set",
            "evaluate.base_config",
        ),
    ),
)

_SPECS_BY_TYPE = {spec.type: spec for spec in TASK_TYPE_SPECS}


def task_type_spec(task_type: str) -> TaskTypeSpec | None:
    return _SPECS_BY_TYPE.get(task_type)


def describe_spec(spec: TaskTypeSpec) -> dict:
    return {
        "type": spec.type,
        "label": spec.label,
        "description": spec.description,
        "import_modes": list(spec.import_modes),
        "upload_extensions": sorted(spec.upload_suffixes()) if "upload" in spec.import_modes else [],
        "export_formats": [asdict(item) for item in spec.export_formats],
        "fields": [describe_field(item) for item in spec.fields],
    }


def describe_field(spec: FieldSpec) -> dict:
    value = asdict(spec)
    value["options"] = (
        None if spec.options is None else [asdict(option) for option in spec.options]
    )
    return value


# ------------------------------------------------------------ dotted paths


def get_dotted(document: Mapping, key: str) -> object:
    current: object = document
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def set_dotted(document: dict, key: str, value: object) -> None:
    """Set (or, for ``None``, remove) one key, creating sections as needed."""
    parts = key.split(".")
    current = document
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            if value is None:
                return
            child = {}
            current[part] = child
        current = child
    if value is None:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = value


def current_values(spec: TaskTypeSpec | None, document: Mapping) -> dict[str, object]:
    """Every field's effective value: what the file says, else the default."""
    if spec is None:
        return {}
    values = {}
    for item in spec.fields:
        value = get_dotted(document, item.key)
        values[item.key] = copy.deepcopy(item.default) if _is_unset(value) else value
    return values


def _is_unset(value: object) -> bool:
    return value is None or value == ""


# -------------------------------------------------------------- validation


def coerce_settings(
    spec: TaskTypeSpec | None, settings: Mapping[str, object]
) -> dict[str, object]:
    """Type-check submitted values; ``None`` means "remove from the file"."""
    if spec is None:
        if settings:
            raise ManagementError(
                "unknown_setting", "this task type has no editable form settings"
            )
        return {}
    coerced = {}
    for key, value in settings.items():
        item = spec.find_field(key)
        if item is None:
            raise ManagementError(
                "unknown_setting",
                f"unknown setting {key!r}; known: {[f.key for f in spec.fields]}",
            )
        coerced[key] = _coerce(item, value)
    return coerced


def _coerce(item: FieldSpec, value: object) -> object:
    if item.type in {"text", "path"}:
        if value is None:
            result = None
        elif not isinstance(value, str):
            raise _invalid(item, "must be text")
        else:
            result = value.strip() or None
    elif item.type == "integer":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise _invalid(item, "must be a whole number")
        result = value
    elif item.type == "number":
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise _invalid(item, "must be a finite number")
        result = value
    elif item.type == "boolean":
        if value is not None and not isinstance(value, bool):
            raise _invalid(item, "must be true or false")
        result = value
    elif item.type == "select":
        allowed = [option.value for option in item.options or ()]
        if value is not None and value not in allowed:
            raise _invalid(item, f"must be one of {allowed}")
        result = value
    else:  # list
        if value is None:
            result = None
        elif not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise _invalid(item, "must be a list of text")
        else:
            result = [entry.strip() for entry in value]
            if any(not entry for entry in result):
                raise _invalid(item, "entries must not be empty")
            result = result or None
    if result is None and item.required:
        raise ManagementError("missing_setting", f"setting {item.key!r} ({item.label}) is required")
    return result


def _invalid(item: FieldSpec, problem: str) -> ManagementError:
    return ManagementError("invalid_setting", f"setting {item.key!r} ({item.label}) {problem}")


def initial_values(
    spec: TaskTypeSpec, settings: Mapping[str, object]
) -> dict[str, object]:
    """Values for a new project: defaults overlaid with the submitted settings."""
    coerced = coerce_settings(spec, settings)
    values = {}
    for item in spec.fields:
        if item.key in coerced:
            value = coerced[item.key]
        else:
            value = copy.deepcopy(item.default)
        if item.server_only and not _is_unset(value):
            raise _server_only(item)
        if _is_unset(value) and item.required:
            raise ManagementError(
                "missing_setting", f"setting {item.key!r} ({item.label}) is required"
            )
        values[item.key] = value
    return values


def check_server_only(
    spec: TaskTypeSpec | None, before: Mapping, after: Mapping
) -> None:
    """Reject any change to a server-only key between two config documents."""
    if spec is None:
        return
    for item in spec.fields:
        if item.server_only and _normalized(get_dotted(before, item.key)) != _normalized(
            get_dotted(after, item.key)
        ):
            raise _server_only(item)
    for key in spec.server_only_keys:
        if _normalized(get_dotted(before, key)) != _normalized(get_dotted(after, key)):
            raise ManagementError(
                "server_only_field",
                f"`{key}` can only be changed by editing the config file on the server",
            )


def check_pipeline_script(before: Mapping, after: Mapping) -> None:
    """A raw ReID config may only name a bundled pipeline or keep its current one.

    ``pipeline.script`` can be a path to any Python file, which the extract
    action imports and runs -- the same remote-code-execution hazard as the
    server-only trainer keys.
    """
    old = get_dotted(before, "pipeline.script")
    new = get_dotted(after, "pipeline.script")
    if new != old and new not in BUNDLED_PIPELINES:
        raise ManagementError(
            "server_only_field",
            f"`pipeline.script` may only be set to a bundled pipeline "
            f"{list(BUNDLED_PIPELINES)} over the API; custom scripts must be "
            "configured by editing the file on the server",
        )


def check_locks(
    spec: TaskTypeSpec | None, before: Mapping[str, object], after: Mapping[str, object]
) -> None:
    """Enforce `lock` rules between two effective-value maps of an annotated project."""
    if spec is None:
        return
    for item in spec.fields:
        old = before.get(item.key)
        new = after.get(item.key)
        if item.lock == "locked" and new != old:
            raise ManagementError(
                "setting_locked",
                f"setting {item.key!r} ({item.label}) cannot change once the "
                "project has annotations",
                status=409,
            )
        if item.lock == "append_only" and isinstance(old, list):
            if not isinstance(new, list) or new[: len(old)] != old:
                raise ManagementError(
                    "setting_locked",
                    f"setting {item.key!r} ({item.label}) can only be extended at "
                    f"the end once the project has annotations; it must start with {old}",
                    status=409,
                )


def _normalized(value: object) -> object:
    # Missing, empty and the DEFAULTS' empty list all mean "not configured".
    return None if _is_unset(value) or value == [] else value


def _server_only(item: FieldSpec) -> ManagementError:
    return ManagementError(
        "server_only_field",
        f"setting {item.key!r} ({item.label}) names a program the platform runs and "
        "can only be changed by editing the config file on the server",
    )
