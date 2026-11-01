"""Regenerate the public API schema without starting a server."""

import json
from pathlib import Path
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "science"))
from boltzfix.api import create_app

app = create_app(project.parent / "work/schema-store", False)
(project / "web/openapi.json").write_text(json.dumps(app.openapi(), indent=2))
