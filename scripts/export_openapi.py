"""Write openapi.json from the FastAPI app so the spec never drifts from the code.

Usage: python -m scripts.export_openapi
"""

import json
from pathlib import Path

from app.main import create_app

if __name__ == "__main__":
    spec = create_app().openapi()
    path = Path(__file__).resolve().parent.parent / "openapi.json"
    path.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"Wrote {path}")
