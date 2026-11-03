import json
from pathlib import Path

from app.main import create_app


def test_committed_openapi_json_matches_the_app():
    committed = json.loads((Path(__file__).resolve().parent.parent / "openapi.json").read_text())

    assert committed == create_app().openapi(), "openapi.json is stale: run python -m scripts.export_openapi"
