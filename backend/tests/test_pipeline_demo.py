from pathlib import Path

from reid_annotation_tool.contract import Pipeline


def test_tracked_ultralytics_demo_satisfies_the_pipeline_contract():
    path = Path(__file__).resolve().parents[1] / "pipeline/pipeline.demo.py"
    pipeline = Pipeline.load(str(path))
    assert pipeline.path == path
    assert set(pipeline.describe()["hooks"]) == {"open_source", "detect_crops"}
