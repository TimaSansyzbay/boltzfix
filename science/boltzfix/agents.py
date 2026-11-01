from __future__ import annotations
import json
import os
from urllib.parse import urlparse
import httpx
from .errors import BoltzFixError
from .models import Proposal, PeerReview
from .store import uid
from .validation import select_candidate

ROLES = ("chemistry", "structure", "physics")


def evidence_ids(candidates):
    return {f"{c.id}/{check.id}" for c in candidates for check in c.diagnostics.checks}


def validate_proposal(p, candidates, remaining, attempted):
    by_id = {c.id: c for c in candidates}
    if set(p.evidence_ids) - evidence_ids(candidates):
        raise BoltzFixError("INVALID_EVIDENCE", "Proposal refers to missing evidence")
    if p.action == "resample" and (
        p.samples > remaining.get("samples", 0)
        or remaining.get("prediction_requests", 0) < 1
    ):
        raise BoltzFixError("ILLEGAL_ACTION", "Insufficient prediction allowance")
    if p.action == "minimize":
        c = by_id.get(p.candidate_id)
        if (
            not c
            or c.kind != "prediction"
            or c.id in attempted
            or not c.diagnostics.chemically_eligible
            or remaining.get("refinements", 0) < 1
        ):
            raise BoltzFixError(
                "ILLEGAL_ACTION", "Candidate is not eligible for refinement"
            )


def winner(proposals, reviews, epoch):
    # One vote per specialist, never a model-generated or weighted central score.
    if len({r.agent for r in reviews}) != len(reviews):
        raise BoltzFixError("INVALID_VOTE", "Duplicate specialist vote")
    if len({p.id for p in proposals}) != len(proposals):
        raise BoltzFixError("INVALID_VOTE", "Duplicate proposal identifier")
    counts = {p.id: 0 for p in proposals}
    for review in reviews:
        if review.proposal_id not in counts:
            raise BoltzFixError("INVALID_VOTE", "Unknown proposal")
        if review.endorse:
            counts[review.proposal_id] += 1
    qualified = [p for p in proposals if counts[p.id] >= 2]
    order = ROLES[epoch % 3 :] + ROLES[: epoch % 3]
    return (
        min(
            qualified,
            key=lambda p: (
                -counts[p.id],
                order.index(p.agent) if p.agent in order else 3,
                p.id,
            ),
        )
        if qualified
        else None
    )


def synthetic_proposal(role, candidates, remaining, attempted):
    best = select_candidate(candidates)
    evidence = (
        []
        if best is None
        else [
            f"{best.id}/{c.id}" for c in best.diagnostics.checks if c.status == "fail"
        ][:10]
    )
    action = "stop"
    candidate = None
    can_sample = (
        remaining.get("samples", 0) > 0 and remaining.get("prediction_requests", 0) > 0
    )
    can_refine = (
        best
        and best.kind == "prediction"
        and best.id not in attempted
        and remaining.get("refinements", 0) > 0
    )
    if best is None or not best.diagnostics.physical_valid:
        if role == "physics" and can_refine:
            action = "minimize"
            candidate = best.id
        elif can_sample:
            action = "resample"
        elif can_refine:
            action = "minimize"
            candidate = best.id
    return Proposal(
        id=uid("prop"),
        agent=role,
        action=action,
        candidate_id=candidate,
        evidence_ids=evidence,
        hypothesis=(
            "Synthetic policy: investigate observed physical failures"
            if evidence
            else "Synthetic policy: no actionable physical failure remains"
        ),
        expected_benefit="Exercise bounded scientific-action coordination; no scientific performance claim",
        uncertainty="Authored mock decision, not an LLM assessment",
    )


def synthetic_review(role, proposals):
    # Deliberate disagreement is evidence-triggered, never fabricated measurements.
    preferred = "minimize" if role == "physics" else "resample"
    p = next((p for p in proposals if p.action == preferred), proposals[0])
    return PeerReview(
        agent=role,
        proposal_id=p.id,
        endorse=True,
        evidence_ids=p.evidence_ids,
        reason=(
            "Synthetic physics policy requests a local-mechanics experiment"
            if role == "physics"
            else "Synthetic policy favors sampling when physical failures remain"
        ),
    )


class LiveAgents:
    """One bounded Eve decision-tool call per reservation; never direct scientific access."""

    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id

    @staticmethod
    def specialist_context(role, phase, context):
        if phase == "review" or role == "single":
            return context
        # Independent proposals start from specialist-specific measurements.
        import copy

        view = copy.deepcopy(context)
        for candidate in view["candidates"]:
            if role == "chemistry":
                candidate.pop("physics", None)
                candidate.pop("pose_diversity_angstrom", None)
                candidate["diagnostics"].pop("confidence", None)
            elif role == "structure":
                candidate.pop("physics", None)
            elif role == "physics":
                candidate.pop("pose_diversity_angstrom", None)
                candidate["diagnostics"].pop("confidence", None)
        if role != "physics":
            view.pop("physics_availability", None)
        return view

    async def call(self, role, phase, context):
        from .providers.hosted import require_live

        require_live()
        context = self.specialist_context(role, phase, context)
        url = os.getenv("BOLTZFIX_AGENT_URL", "http://127.0.0.1:8787/decision")
        if urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise BoltzFixError(
                "AGENT_URL_INVALID",
                "Agent service must remain on the private local interface",
                pause=True,
            )
        ledger = self.store.ledger(self.run_id)
        remaining = (
            ledger.limits.llm_tokens
            - ledger.used.get("llm_tokens", 0)
            - ledger.reserved.get("llm_tokens", 0)
        )
        allowance = min(12000, remaining)
        if allowance < 2500:
            raise BoltzFixError(
                "BUDGET_EXHAUSTED", "Final LLM allowance reserved for reporting"
            )
        cost = float(os.getenv("BOLTZFIX_LLM_CALL_USD_BOUND", "0"))
        action = self.store.reserve(
            self.run_id,
            "llm",
            {"llm_calls": 1, "llm_tokens": allowance},
            {"role": role, "phase": phase, "cost_bound_usd": cost},
        )
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                response = await client.post(
                    url,
                    json={
                        "role": role,
                        "phase": phase,
                        "context": context,
                        "token_allowance": allowance,
                        "model_id": self.store.summary(self.run_id).config.llm_model,
                        "reservation_id": action,
                        "run_id": self.run_id,
                    },
                )
            if response.status_code != 200:
                try:
                    failure = response.json().get("error", {})
                except ValueError:
                    failure = {}
                messages = {
                    "CONTEXT_BUDGET": "Remaining token allowance cannot fit the evidence and a bounded decision. Completed candidates are preserved.",
                    "CONTEXT_TOO_LARGE": "Agent evidence exceeds the request size limit; nothing was truncated or sent.",
                    "TOKEN_COUNT_UNAVAILABLE": "Claude token-count preflight is unavailable; no generation was sent.",
                    "MODEL_CHANGED": "Configured agent model differs from the frozen run model.",
                    "LIVE_DISABLED": "Sponsor-backed agent access is not configured.",
                    "AGENT_PROVIDER": "Agent provider did not return a verified decision; no automatic retry was made.",
                }
                code = failure.get("code") if isinstance(failure, dict) else None
                if code in messages:
                    unknown = failure.get("completion_unknown") is not False
                    self.store.settle(
                        action,
                        "unknown" if unknown else "failed",
                        actual={
                            "error": {"code": code, "message": messages[code]},
                            **(
                                {"provider_dispatched": False, "llm_tokens": 0}
                                if not unknown
                                else {}
                            ),
                        },
                    )
                    raise BoltzFixError(
                        code,
                        messages[code],
                        pause=code not in {"CONTEXT_BUDGET", "CONTEXT_TOO_LARGE"},
                        unknown_remote=unknown,
                    )
                raise BoltzFixError(
                    "AGENT_SERVICE",
                    "Agent decision unavailable; no provider fallback",
                    pause=True,
                    unknown_remote=True,
                )
            result = response.json()
            expected_model = self.store.summary(self.run_id).config.llm_model
            if result.get("model") != expected_model:
                raise BoltzFixError(
                    "MODEL_CHANGED",
                    "Returned LLM model differs from frozen model",
                    pause=True,
                )
            tokens = result["usage"]["total_tokens"]
            if not isinstance(tokens, int) or tokens < 0 or tokens > allowance:
                raise BoltzFixError(
                    "USAGE_INVALID",
                    "Agent usage exceeds reservation or is unavailable",
                    pause=True,
                    unknown_remote=True,
                )
            self.store.settle(
                action,
                actual={
                    "llm_tokens": tokens,
                    "model": result["model"],
                    "model_settings": result.get("model_settings", {}),
                    "cost_bound_usd": cost,
                },
            )
            model = Proposal if phase == "propose" else PeerReview
            output = model.model_validate(result["decision"])
            if output.agent != role:
                raise BoltzFixError("AGENT_SCHEMA", "Agent identity mismatch")
            if phase == "propose":
                output.id = uid("prop")
            return output
        except BoltzFixError as exc:
            if any(
                a.id == action and a.status == "reserved"
                for a in self.store.actions(self.run_id)
            ):
                self.store.settle(
                    action,
                    "unknown" if exc.unknown_remote else "failed",
                    actual={"error": exc.public()},
                )
            raise
        except Exception as exc:
            if any(
                a.id == action and a.status == "reserved"
                for a in self.store.actions(self.run_id)
            ):
                self.store.settle(
                    action,
                    "unknown",
                    actual={"error": "Agent result unavailable; completion unknown"},
                )
            raise BoltzFixError(
                "AGENT_COMPLETION_UNKNOWN",
                "Decision completion unknown; no automatic retry",
                pause=True,
                unknown_remote=True,
            ) from exc
