import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "local_credentials",
    Path(__file__).resolve().parents[2] / "scripts/local_credentials.py",
)
credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credentials)


def test_only_relevant_services_receive_credential_names():
    # Names alone suffice to verify distribution; no credential values are fixtures.
    env = {key: key for key in credentials.SECRET_NAMES} | {"BOLTZFIX_ALLOW_LIVE": "0"}
    assert not credentials.SECRET_NAMES.intersection(
        credentials.service_environment(env, "web")
    )
    assert "ANTHROPIC_API_KEY" not in credentials.service_environment(env, "science")
    assert "NVIDIA_API_KEY" not in credentials.service_environment(env, "agents")
    assert credentials.service_environment(env, "agents")["BOLTZFIX_ALLOW_LIVE"] == "0"


def test_private_file_requirements_and_unrelated_fields(tmp_path):
    path = tmp_path / "private/credentials.json"
    credentials.save_credentials(path, {})
    assert credentials.load_credentials(path) == {}
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        credentials.load_credentials(path)
    with pytest.raises(ValueError, match="Unsupported"):
        credentials.save_credentials(path, {"BOLTZFIX_ALLOW_LIVE": "1"})
