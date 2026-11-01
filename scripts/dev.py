#!/usr/bin/env python3
"""Start the private local stack with optional owner-only local credentials."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from local_credentials import (
    credential_path,
    load_credentials,
    service_environment,
    load_runtime,
)

project = Path(__file__).resolve().parents[1]
workspace = project.parent
python = Path(
    os.getenv("BOLTZFIX_PYTHON", str(workspace / "work/physics-env/bin/python"))
)
if not python.exists():
    python = project / "science/.venv/bin/python"
if not python.exists():
    sys.exit("Create the Python environment first; see README.md.")
node = os.getenv("BOLTZFIX_NODE") or shutil.which("node")
bundled = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
)
if (
    not node
    or int(
        subprocess.check_output(
            [node, "-p", "process.versions.node.split('.')[0]"], text=True
        )
    )
    < 24
) and bundled.exists():
    node = str(bundled)
if (
    not node
    or int(
        subprocess.check_output(
            [node, "-p", "process.versions.node.split('.')[0]"], text=True
        )
    )
    < 24
):
    sys.exit("Node.js 24+ is required. Set BOLTZFIX_NODE to its executable.")
env = os.environ.copy()
for key, value in load_credentials(credential_path(project)).items():
    env.setdefault(key, value)
for key, value in load_runtime(
    workspace / "work/private/boltzfix-runtime.json"
).items():
    env.setdefault(key, value)
env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
env["PYTHONPATH"] = str(project / "science") + os.pathsep + env.get("PYTHONPATH", "")
env.setdefault("BOLTZFIX_DATA", str(project / ".boltzfix"))
env.setdefault("BOLTZFIX_PHYSICS_PYTHON", str(python))
env.setdefault("XDG_CACHE_HOME", str(workspace / "work/cache"))
env.setdefault("MPLCONFIGDIR", str(workspace / "work/matplotlib"))
env.setdefault("BOLTZFIX_ALLOW_LIVE", "0")
env.setdefault("BOLTZFIX_SPONSOR_CONFIRMED", "0")
children = []


def shutdown(*_):
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)
commands = [
    ([str(python), "-m", "boltzfix.cli", "serve"], project, "science"),
    (["npm", "run", "dev:agents"], project, "agents"),
    (["npm", "run", "dev:web"], project, "web"),
]
try:
    for command, cwd, service in commands:
        children.append(
            subprocess.Popen(
                command,
                cwd=cwd,
                env=service_environment(env, service),
                start_new_session=True,
            )
        )
    print(
        "\nBoltzFix: http://127.0.0.1:5173 | API :8000 | private Eve :8787\nLive services stay disabled unless explicitly configured. Ctrl+C stops all services.\n",
        flush=True,
    )
    while all(p.poll() is None for p in children):
        time.sleep(0.5)
    print("A service stopped; shutting down the stack.", file=sys.stderr)
finally:
    shutdown()
