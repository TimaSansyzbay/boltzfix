"""Private local credential loading. Values must never be logged or bundled."""

from __future__ import annotations
import getpass
import json
import os
from pathlib import Path
import stat

SECRET_NAMES = frozenset(
    {
        "NVIDIA_API_KEY",
        "NGC_API_KEY",
        "ANTHROPIC_API_KEY",
        "NEBIUS_SCIENTIFIC_AI_API_KEY",
    }
)
RUNTIME_NAMES = frozenset(
    {
        "BOLTZFIX_ALLOW_LIVE",
        "BOLTZFIX_SPONSOR_CONFIRMED",
        "BOLTZFIX_NVIDIA_FREE_TRIAL_CONFIRMED",
        "BOLTZFIX_DIFFDOCK_FREE_TRIAL_CONFIRMED",
        "BOLTZFIX_SAMPLE_USD_BOUND",
        "BOLTZFIX_LLM_CALL_USD_BOUND",
        "BOLTZFIX_SPONSOR_USD_CAP",
        "BOLTZFIX_LLM_MODEL",
        "BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK",
    }
)


def load_runtime(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = json.loads(path.read_text())
    if (
        not isinstance(values, dict)
        or set(values) - RUNTIME_NAMES
        or any(not isinstance(v, str) for v in values.values())
    ):
        raise ValueError("Runtime configuration contains unsupported fields")
    return values


def credential_path(project: Path) -> Path:
    return Path(
        os.getenv(
            "BOLTZFIX_CREDENTIALS_FILE",
            str(project.parent / "work/private/boltzfix-credentials.json"),
        )
    ).expanduser()


def load_credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError(
            "Credential file must be a regular owner-only file (permissions 0600)"
        )
    if info.st_uid != os.getuid():
        raise ValueError("Credential file must belong to the current user")
    values = json.loads(path.read_text())
    if not isinstance(values, dict) or set(values) - SECRET_NAMES:
        raise ValueError("Credential file contains unsupported fields")
    if any(
        not isinstance(value, str)
        or not value.strip()
        or any(c.isspace() for c in value)
        for value in values.values()
    ):
        raise ValueError(
            "Credential values must be nonempty strings without whitespace"
        )
    return values


def service_environment(env: dict[str, str], service: str) -> dict[str, str]:
    allowed = {
        "science": {"NVIDIA_API_KEY", "NGC_API_KEY", "NEBIUS_SCIENTIFIC_AI_API_KEY"},
        "agents": {"ANTHROPIC_API_KEY"},
        "web": set(),
    }[service]
    return {
        key: value
        for key, value in env.items()
        if key not in SECRET_NAMES or key in allowed
    }


def save_credentials(path: Path, values: dict[str, str]):
    if set(values) - SECRET_NAMES:
        raise ValueError("Unsupported credential names")
    if any(
        not isinstance(value, str) or not value or any(c.isspace() for c in value)
        for value in values.values()
    ):
        raise ValueError(
            "Credential values must be nonempty strings without whitespace"
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists() and path.is_symlink():
        raise ValueError("Credential path must not be a symlink")
    temp = path.with_name(path.name + ".new")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(values, file)
        temp.replace(path)
        path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()
    load_credentials(path)


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    path = credential_path(project)
    values = load_credentials(path)
    for name in ("NVIDIA_API_KEY", "ANTHROPIC_API_KEY"):
        value = getpass.getpass(f"{name} (hidden): ").strip().replace("\\_", "_")
        if value:
            values[name] = value
    save_credentials(path, values)
    print(
        "Credentials saved with owner-only permissions outside the application source. No API requests made."
    )
