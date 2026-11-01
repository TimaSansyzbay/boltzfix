import pytest
from boltzfix.agents import winner, validate_proposal
from boltzfix.models import Proposal, PeerReview
from boltzfix.errors import BoltzFixError


def proposal(id="p1", agent="chemistry", action="stop"):
    return Proposal(
        id=id,
        agent=agent,
        action=action,
        hypothesis="h",
        expected_benefit="b",
        uncertainty="u",
    )


def test_majority_and_disagreement():
    p = proposal()
    reviews = [
        PeerReview(
            agent=a, proposal_id=p.id, endorse=True, reason="Evidence supports this"
        )
        for a in ["chemistry", "structure"]
    ]
    assert winner([p], reviews, 0) == p
    assert winner([p], reviews[:1], 0) is None
    with pytest.raises(BoltzFixError):
        winner([p], reviews + [reviews[0]], 0)


def test_invalid_evidence_and_unavailable_action_rejected():
    p = proposal(action="resample")
    with pytest.raises(BoltzFixError):
        validate_proposal(p, [], {"samples": 0, "prediction_requests": 1}, set())
    p.evidence_ids = ["fake/check"]
    with pytest.raises(BoltzFixError):
        validate_proposal(p, [], {"samples": 1, "prediction_requests": 1}, set())


def test_one_vote_per_specialist_and_no_unknown_proposals():
    with pytest.raises(BoltzFixError):
        winner(
            [proposal()],
            [
                PeerReview(
                    agent="physics", proposal_id="missing", endorse=True, reason="x"
                )
            ],
            0,
        )


@pytest.mark.asyncio
async def test_three_independent_proposals_can_finish_within_five_calls(
    store, config, monkeypatch
):
    from boltzfix.agents import LiveAgents
    from boltzfix.engine import Engine

    run = store.create_run(config)
    calls = []

    async def call(self, role, phase, context):
        calls.append((role, phase))
        assert len(calls) <= 5, "A decided majority must not consume a sixth call"
        if phase == "propose":
            return proposal(id="p_" + role, agent=role)
        return PeerReview(
            agent=role,
            proposal_id=context["proposals"][0]["id"],
            endorse=True,
            reason="Evidence supports stopping",
        )

    monkeypatch.setattr(LiveAgents, "call", call)
    result = await Engine(store).decide(
        run, 0, config.model_copy(update={"agent_mode": "live"})
    )
    assert result.action == "stop"
    assert calls[:3] == [(r, "propose") for r in ("chemistry", "structure", "physics")]
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_live_client_meters_mocked_receipts_and_assigns_unique_ids(
    store, config, monkeypatch
):
    import httpx
    import boltzfix.providers.hosted as hosted
    from boltzfix.agents import LiveAgents

    run = store.create_run(
        config.model_copy(update={"provider": "nvidia_nim", "agent_mode": "live"})
    )
    store.claim(run, "test")
    store.set_status(run, "running")
    monkeypatch.setattr(hosted, "require_live", lambda: None)
    monkeypatch.setenv("BOLTZFIX_LLM_CALL_USD_BOUND", "0.1")
    original_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200,
            json={
                "decision": proposal(
                    id="reused-model-id", agent="physics"
                ).model_dump(),
                "model": config.llm_model,
                "usage": {"total_tokens": 300},
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handler)
        ),
    )
    agent = LiveAgents(store, run)
    first = await agent.call("physics", "propose", {"candidates": []})
    second = await agent.call("physics", "propose", {"candidates": []})
    assert first.id != second.id and first.id.startswith("prop_")
    assert store.ledger(run).used["llm_calls"] == 2
    assert store.ledger(run).used["llm_tokens"] == 600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,unknown",
    [
        ("CONTEXT_BUDGET", False),
        ("TOKEN_COUNT_UNAVAILABLE", False),
        ("AGENT_PROVIDER", True),
    ],
)
async def test_typed_service_failures_preserve_reason_and_conservative_accounting(
    store, config, monkeypatch, code, unknown
):
    import httpx
    import boltzfix.providers.hosted as hosted
    from boltzfix.agents import LiveAgents

    run = store.create_run(
        config.model_copy(update={"provider": "nvidia_nim", "agent_mode": "live"})
    )
    store.claim(run, "test")
    store.set_status(run, "running")
    monkeypatch.setattr(hosted, "require_live", lambda: None)
    monkeypatch.setenv("BOLTZFIX_LLM_CALL_USD_BOUND", "0.1")
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            503,
            json={
                "error": {
                    "code": code,
                    "message": "untrusted upstream details",
                    "completion_unknown": unknown,
                }
            },
        )
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=transport)
    )
    with pytest.raises(BoltzFixError) as error:
        await LiveAgents(store, run).call("physics", "review", {"candidates": []})
    assert error.value.code == code
    assert "untrusted" not in error.value.message
    assert store.ledger(run).used["llm_calls"] == 1
    assert store.ledger(run).used["llm_tokens"] == (12000 if unknown else 0)
    assert store.actions(run)[0].status == ("unknown" if unknown else "failed")
    with store.connection() as connection:
        usd = connection.execute("SELECT usd FROM campaign_usage").fetchone()[0]
    assert usd == pytest.approx(0.1 if unknown else 0)
