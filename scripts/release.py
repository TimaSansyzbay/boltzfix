"""Create a dated, credential-free source snapshot; no publication or deployment."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

project = Path(__file__).resolve().parents[1]
output = project.parent / "outputs"
output.mkdir(exist_ok=True)
ignored = {
    "node_modules",
    ".venv",
    ".eve",
    ".output",
    ".pytest_cache",
    "__pycache__",
    "dist",
    ".boltzfix",
    "test-results",
    "playwright-report",
}
files = []
for file in sorted(project.rglob("*")):
    relative = file.relative_to(project)
    if (
        not file.is_file()
        or file.is_symlink()
        or any(part in ignored or part.endswith(".egg-info") for part in relative.parts)
    ):
        continue
    if file.name.startswith(".env") or file.suffix in {".pyc", ".tsbuildinfo"}:
        continue
    files.append((relative, file))
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
manifest = {
    "created_utc": stamp,
    "scientific_status": "Working local prototype with real hosted predictions, live collective decisions, CPU refinement and report exports; scientific superiority untested",
    "files": {
        str(relative): hashlib.sha256(file.read_bytes()).hexdigest()
        for relative, file in files
    },
}
target = output / f"boltzfix-source-{stamp}.zip"
with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
    for relative, file in files:
        archive.write(file, str(Path("boltzfix") / relative))
    archive.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, indent=2))
print(target)
