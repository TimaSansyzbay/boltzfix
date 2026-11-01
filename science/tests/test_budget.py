from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from boltzfix.errors import BoltzFixError
from boltzfix.models import SelectionReceipt


def test_atomic_concurrent_reservations(store, config):
    run = store.create_run(config)

    def reserve(_):
        try:
            return store.reserve(run, "diagnostic", {"samples": 1})
        except BoltzFixError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(r is not None for r in results) == 2
    assert store.ledger(run).reserved["samples"] == 2


def test_full_request_and_unknown_work_charged(store, config):
    run = store.create_run(config)
    a = store.reserve(run, "predict", {"samples": 2, "prediction_requests": 1})
    store.settle(a, "unknown")
    assert store.ledger(run).used["samples"] == 2
    with pytest.raises(BoltzFixError, match="Unresolved"):
        store.reserve(run, "predict", {"samples": 1})


def test_scientific_actions_cannot_run_concurrently_across_runs(store, config):
    one = store.create_run(config)
    two = store.create_run(config)
    action = store.reserve(one, "predict", {"samples": 1})
    with pytest.raises(BoltzFixError, match="already running"):
        store.reserve(two, "predict", {"samples": 1})
    store.settle(action)
    assert store.reserve(two, "predict", {"samples": 1})


def test_retries_refinement_and_frozen_run_cannot_increase_budget(store, config):
    run = store.create_run(config)
    a = store.reserve(run, "minimize", {"refinements": 1}, {"candidate_id": "c1"})
    store.settle(a, "failed")
    with pytest.raises(BoltzFixError):
        store.reserve(run, "minimize", {"refinements": 1}, {"candidate_id": "c1"})
    store.freeze(
        run,
        SelectionReceipt(
            candidate_id=None,
            artifact_hashes={},
            config_hash=store.row(run)["config_hash"],
            physical_valid=False,
            limitations=[],
        ),
    )
    with pytest.raises(BoltzFixError):
        store.reserve(run, "predict", {"samples": 1})
    with pytest.raises(BoltzFixError):
        store.add_artifact(run, "late.txt", "forbidden")


def test_recovery_preserves_unknown_reservations(store, config):
    run = store.create_run(config)
    store.claim(run, "crashed")
    store.reserve(run, "predict", {"samples": 1})
    with store.connection(immediate=True) as c:
        c.execute("UPDATE runs SET heartbeat=? WHERE id=?", (time.time() - 100, run))
    store.recover_stale()
    assert store.row(run)["status"] == "paused"
    assert store.ledger(run).outstanding_remote
    assert not store.claim(run, "restart")


def test_tampered_artifacts_are_rejected(store, config):
    run = store.create_run(config)
    a = store.add_artifact(run, "data.txt", "original")
    _, path = store.artifact(a)
    path.write_text("changed")
    with pytest.raises(BoltzFixError, match="hash"):
        store.artifact(a)


def test_sponsor_cost_bounds_are_atomic_and_required(store, config, monkeypatch):
    c = config.model_copy(update={"provider": "nvidia_nim", "policy": "initial"})
    run = store.create_run(c)
    monkeypatch.setenv("BOLTZFIX_SPONSOR_USD_CAP", "0.5")
    with pytest.raises(BoltzFixError, match="upper bound"):
        store.reserve(run, "predict", {"samples": 1})
    a = store.reserve(run, "predict", {"samples": 1}, {"cost_bound_usd": 0.4})
    store.settle(a)
    with pytest.raises(BoltzFixError, match="Sponsor cost"):
        store.reserve(run, "predict", {"samples": 1}, {"cost_bound_usd": 0.4})


def test_llm_actual_tokens_settle_and_overage_freezes_dispatch(store, config):
    run = store.create_run(config)
    a = store.reserve(run, "llm", {"llm_calls": 1, "llm_tokens": 1000})
    store.settle(a, actual={"llm_tokens": 300})
    assert store.ledger(run).used["llm_tokens"] == 300
    b = store.reserve(run, "llm", {"llm_calls": 1, "llm_tokens": 100})
    store.settle(b, actual={"llm_tokens": 101})
    assert store.ledger(run).outstanding_remote


def test_verified_free_nvidia_usage_still_requires_metered_sample_limits(
    store, config, monkeypatch
):
    run = store.create_run(
        config.model_copy(update={"provider": "nvidia_nim", "policy": "initial"})
    )
    payload = {
        "provider": "nvidia_nim",
        "cost_bound_usd": 0,
        "billing_mode": "free_development_api",
    }
    with pytest.raises(BoltzFixError, match="upper bound"):
        store.reserve(run, "predict", {"samples": 1}, payload)
    monkeypatch.setenv("BOLTZFIX_NVIDIA_FREE_TRIAL_CONFIRMED", "1")
    action = store.reserve(
        run, "predict", {"samples": 2, "prediction_requests": 1}, payload
    )
    store.settle(action)
    with pytest.raises(BoltzFixError, match="samples"):
        store.reserve(run, "predict", {"samples": 1}, payload)
    with store.connection() as c:
        assert c.execute("SELECT usd FROM campaign_usage").fetchone()[0] == 0
    with pytest.raises(BoltzFixError):
        store.reserve(run, "llm", {"llm_calls": 1}, payload)
