import asyncio
import io
import json
import zipfile
import pytest
from fastapi.testclient import TestClient
from boltzfix.api import create_app, bundle
from boltzfix.engine import Engine
from boltzfix.evaluator import evaluate_run, paired_bootstrap
from boltzfix.errors import BoltzFixError
from boltzfix.models import RunConfig


@pytest.mark.asyncio
async def test_collective_end_to_end_and_isolated_evaluation(store, config, tmp_path):
    run = store.create_run(config)
    store.claim(run, "test")
    await Engine(store).run(run)
    s = store.summary(run)
    assert s.status == "completed" and len(s.candidates) == 2
    assert s.budget.used["samples"] == 2 and not s.budget.reserved
    assert s.selection and s.selection.candidate_id == s.candidates[-1].id
    assert any(e["type"] == "review" for e in store.events(run))
    assert s.candidates[-1].provenance["pose_diversity_angstrom"]
    c = s.candidates[-1]
    p = store.artifact(c.artifact_ids["protein"])[1]
    l = store.artifact(c.artifact_ids["ligand"])[1]
    score = evaluate_run(store, run, p, l, tmp_path / "private-evaluation")
    assert score["selected"]["primary_success"] and score["synthetic"]
    assert not any("reference" in e["type"] for e in store.events(run))
    with pytest.raises(BoltzFixError):
        evaluate_run(store, run, p, l, store.root / "forbidden")
    z = zipfile.ZipFile(io.BytesIO(bundle(store, run)))
    assert "run.json" in z.namelist() and not any(
        "reference" in n for n in z.namelist()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        "initial",
        "fixed_resampling",
        "fixed_hybrid",
        "rules",
        "single_agent",
        "isolated",
    ],
)
async def test_baselines_preserve_resource_ceilings(store, config, policy):
    run = store.create_run(config.model_copy(update={"policy": policy}))
    store.claim(run, "test")
    await Engine(store).run(run)
    s = store.summary(run)
    assert s.status == "completed" and s.selection
    for key, value in s.budget.used.items():
        assert value <= getattr(config.budget, key)
    assert s.budget.used["samples"] >= 1
    assert all(
        c.kind == "prediction" for c in s.candidates
    )  # no invented mock mechanics


@pytest.mark.asyncio
async def test_shared_initial_is_charged_and_preserved(store, config):
    first = store.create_run(config.model_copy(update={"policy": "initial"}))
    store.claim(first, "test")
    await Engine(store).run(first)
    c = store.candidates(first)[0]
    other = store.create_run(config.model_copy(update={"policy": "fixed_resampling"}))
    store.claim(other, "test")
    await Engine(store).run(other, c)
    assert store.summary(other).budget.used["samples"] == 2
    assert store.candidates(other)[0].provenance["shared_initial_id"] == c.id


def test_api_validation_isolation_stream_reconnect_cancel_and_export(tmp_path, config):
    app = create_app(tmp_path / "app", False)
    with TestClient(app) as client:
        assert client.get("/v1/health").json()["status"] == "ok"
        assert (
            client.post(
                "/v1/runs", json={**config.model_dump(), "api_key": "not accepted"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/runs",
                json=config.model_dump(),
                headers={"Origin": "https://external.example"},
            ).status_code
            == 403
        )
        run = client.post("/v1/runs", json=config.model_dump()).json()["id"]
        assert client.get(f"/v1/runs/{run}").json()["status"] == "queued"
        assert client.post(f"/v1/runs/{run}/cancel").status_code == 202
        cursor = app.state.store.events(run)[0]["seq"]
        response = client.get(
            f"/v1/runs/{run}/events", headers={"Last-Event-ID": str(cursor)}
        )
        assert (
            f"id: {cursor}\n" not in response.text
            and "cancel_requested" in response.text
        )
        assert client.get(f"/v1/runs/{run}/export").status_code == 200
        assert client.post(f"/v1/runs/{run}/evaluate").status_code == 404
        assert client.get("/v1/artifacts/nonexistent").status_code == 404


def test_evaluation_requires_frozen_receipt(store, config, structures, tmp_path):
    run = store.create_run(config)
    with pytest.raises(BoltzFixError, match="frozen"):
        evaluate_run(store, run, *structures[:2], tmp_path / "score")


def test_paired_bootstrap_has_case_level_uncertainty():
    result = paired_bootstrap([1, 0, 1, 0], [0, 1, 0, 0])
    assert result["n_complexes"] == 4 and result["bootstrap_resamples"] == 10000
    assert not result["superiority_supported"]
