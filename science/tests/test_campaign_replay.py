import io
import json
from pathlib import Path
import pytest
from boltzfix.api import bundle
from boltzfix.benchmark import register, campaign, analyze, evaluate_campaign
from boltzfix.engine import Engine
from boltzfix.evaluator import evaluate_run
from boltzfix.models import digest
from boltzfix.replay import import_bundle
from boltzfix.store import Store
from boltzfix.errors import BoltzFixError


@pytest.mark.asyncio
async def test_registered_synthetic_comparison_and_report(store, config, tmp_path):
    manifest = {
        "evaluation": [
            {
                "case_id": "eng_001",
                "sequence": config.sequence,
                "ligand_smiles": config.ligand_smiles,
                "msa": None,
                "training_similarity_stratum": "synthetic_not_scientific",
            }
        ]
    }
    source = tmp_path / "public.json"
    source.write_text(json.dumps(manifest))
    registration = tmp_path / "registration.json"
    register(source, registration)
    result = await campaign(store, registration, tmp_path / "campaign")
    assert result["status"] == "completed" and len(result["rows"]) == 6
    initial_run = next(r for r in result["rows"] if r["policy"] == "initial")["run_id"]
    c = store.candidates(initial_run)[0]
    p = store.artifact(c.artifact_ids["protein"])[1]
    l = store.artifact(c.artifact_ids["ligand"])[1]
    references = {
        "evaluation": [
            {
                "anonymous_id": "eng_001",
                "sequence": config.sequence,
                "ligand_smiles": config.ligand_smiles,
                "reference_protein": str(p),
                "reference_ligand": str(l),
                "reference_hashes": {
                    "protein": digest(p.read_bytes()),
                    "ligand": digest(l.read_bytes()),
                },
            }
        ]
    }
    ref = tmp_path / "reference-manifest.json"
    ref.write_text(json.dumps(references))
    scored = evaluate_campaign(
        store,
        registration,
        tmp_path / "campaign/campaign.jsonl",
        ref,
        tmp_path / "scores",
    )
    assert scored["scored_case_policy_pairs"] == 6
    report = analyze(tmp_path / "scores", tmp_path / "report")
    assert report["synthetic"] and report["n_complexes"] == 1
    assert len(report["policies"]) == 6
    assert (tmp_path / "report/benchmark.csv").exists()


@pytest.mark.asyncio
async def test_replay_restores_artifacts_without_dispatch(store, config, tmp_path):
    r = store.create_run(config)
    store.claim(r, "test")
    await Engine(store).run(r)
    archive = tmp_path / "archive.zip"
    archive.write_bytes(bundle(store, r))
    restored = Store(tmp_path / "replay-state")
    new = import_bundle(restored, archive)
    summary = restored.summary(new)
    assert summary.status == "completed" and summary.capabilities["archived_replay"]
    assert len(summary.candidates) == 2 and summary.selection
    assert restored.queued() == []
    assert summary.budget.used["samples"] == store.summary(r).budget.used["samples"]
    assert any(
        e["type"] == "review" and e["data"]["archived"] for e in restored.events(new)
    )


def test_registration_is_immutable_and_rejects_reference_fields(tmp_path, config):
    source = tmp_path / "public.json"
    output = tmp_path / "frozen.json"
    case = {
        "case_id": "case",
        "sequence": config.sequence,
        "ligand_smiles": config.ligand_smiles,
        "reference_ligand": "secret.sdf",
    }
    source.write_text(json.dumps({"evaluation": [case]}))
    with pytest.raises(BoltzFixError, match="prediction inputs"):
        register(source, output)
    case.pop("reference_ligand")
    source.write_text(json.dumps({"evaluation": [case]}))
    register(source, output)
    with pytest.raises(BoltzFixError, match="immutable"):
        register(source, output)


def test_incomplete_scoring_cannot_drop_cases(tmp_path, store):
    registration = {
        "record": {
            "partition": "evaluation",
            "cases": [{"case_id": "c"}],
            "policies": ["initial", "collective"],
        }
    }
    registration["sha256"] = digest(registration["record"])
    r = tmp_path / "r.json"
    r.write_text(json.dumps(registration))
    refs = tmp_path / "refs.json"
    refs.write_text('{"evaluation":[]}')
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")
    with pytest.raises(BoltzFixError, match="missing cases"):
        evaluate_campaign(store, r, journal, refs, tmp_path / "scores")
