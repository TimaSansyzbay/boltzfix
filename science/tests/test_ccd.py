import json

import httpx
import pytest

from boltzfix.engine import Engine
from boltzfix.errors import BoltzFixError
from boltzfix.models import RunConfig
from boltzfix.providers.ccd import CCDResolver, canonical_identity
from boltzfix.providers.hosted import NvidiaProvider

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
NAG = "CC(=O)N[C@@H]1[C@@H](O)[C@H](O)[C@@H](CO)O[C@H]1O"
# Different traversal, identical stereochemistry (RCSB NAG descriptor).
NAG_CCD = "CC(=O)N[C@@H]1[C@H]([C@@H]([C@H](O[C@H]1O)CO)O)O"


def dictionary_transport(descriptor=ASPIRIN, code="AIN", seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        if request.url.host == "search.rcsb.org":
            query = json.loads(request.content)
            assert query["query"]["parameters"]["match_type"] == "graph-exact"
            assert query["return_type"] == "mol_definition"
            return httpx.Response(200, json={"result_set": [{"identifier": code}]})
        assert str(request.url).endswith("/" + code)
        return httpx.Response(
            200,
            json={
                "chem_comp": {"id": code},
                "pdbx_chem_comp_descriptor": [
                    {"type": "SMILES_CANONICAL", "descriptor": descriptor}
                ],
                # This field must never enter the cache/agent evidence.
                "chem_comp_atom": [{"x": 999}],
            },
        )

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_exact_identity_cache_preserves_stereo_and_fetches_no_pose(tmp_path):
    seen = []
    resolver = CCDResolver(tmp_path, dictionary_transport(NAG_CCD, "NAG", seen))
    record = await resolver.resolve(NAG)
    assert record["ccd"] == "NAG"
    assert record["canonical_smiles"] == canonical_identity(NAG)
    assert "chem_comp_atom" not in json.dumps(record)
    assert len(seen) == 2
    assert await resolver.resolve(NAG_CCD) == record

    # A fresh resolver reuses the frozen, revalidated dictionary record offline.
    def no_network(request):
        pytest.fail("Verified cache must not perform another lookup")

    assert (
        await CCDResolver(tmp_path, httpx.MockTransport(no_network)).resolve(NAG)
        == record
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "descriptor",
    [
        "CC(=O)Oc1ccccc1C(=O)[O-]",  # different charge/protonation
        "CC(=O)Oc1ccccc1",  # search hits can be substructures
        NAG.replace("N[C@@H]", "N[C@H]"),  # different stereoisomer
    ],
)
async def test_search_hit_is_not_accepted_without_exact_local_identity(descriptor):
    query = NAG if "[C@" in descriptor else ASPIRIN
    with pytest.raises(BoltzFixError) as error:
        await CCDResolver(transport=dictionary_transport(descriptor)).resolve(query)
    assert error.value.code == "CCD_MATCH_REQUIRED"


@pytest.mark.asyncio
async def test_tampered_cache_pauses_instead_of_silently_changing_molecule(tmp_path):
    await CCDResolver(tmp_path, dictionary_transport()).resolve(ASPIRIN)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    record["descriptor"] = "CC(=O)Oc1ccccc1C(=O)[O-]"
    path.write_text(json.dumps(record))
    with pytest.raises(BoltzFixError) as error:
        await CCDResolver(tmp_path).resolve(ASPIRIN)
    assert error.value.code == "CCD_CACHE_INVALID"


@pytest.mark.asyncio
async def test_unmatched_ligand_uses_no_prediction_allowance(store):
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    provider = NvidiaProvider(resolver=CCDResolver(transport=transport))
    config = RunConfig(
        sequence="A" * 100,
        ligand_smiles=ASPIRIN,
        provider="nvidia_nim",
        policy="initial",
    )
    run = store.create_run(config)
    store.set_status(run, "running")
    with pytest.raises(BoltzFixError) as error:
        await Engine(store).predict(run, provider, 1)
    assert error.value.code == "CCD_MATCH_REQUIRED"
    assert store.actions(run) == []
    assert store.ledger(run).used.get("samples", 0) == 0


@pytest.mark.asyncio
async def test_hosted_submission_uses_verified_ccd_and_records_immutable_identity(
    config, monkeypatch
):
    monkeypatch.setenv("BOLTZFIX_ALLOW_LIVE", "1")
    monkeypatch.setenv("BOLTZFIX_SPONSOR_CONFIRMED", "1")
    monkeypatch.delenv("BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-only-not-a-credential")

    def inference(request):
        body = json.loads(request.content)
        assert body["ligands"] == [{"id": "L", "ccd": "AIN", "predict_affinity": False}]
        return httpx.Response(
            200,
            json={
                "structures": [{"format": "mmcif", "structure": "data_test"}],
                "confidence_scores": [0.7],
            },
        )

    provider = NvidiaProvider(
        transport=httpx.MockTransport(inference),
        resolver=CCDResolver(transport=dictionary_transport()),
    )
    original = config.model_dump()
    predictions = await provider.submit_prediction(config, 1)
    assert config.model_dump() == original
    assert predictions[0].metadata["ligand_encoding"]["ccd"] == "AIN"
    assert predictions[0].metadata["ligand_encoding"][
        "canonical_smiles"
    ] == canonical_identity(ASPIRIN)
