from __future__ import annotations
import asyncio
import json
import os
import time
from .agents import (
    ROLES,
    LiveAgents,
    evidence_ids,
    synthetic_proposal,
    synthetic_review,
    validate_proposal,
    winner,
)
from .chemistry import (
    extract_complex,
    input_molecule,
    protein_transform,
    compare_poses,
    ca_sequence,
    combine_complex,
)
from .errors import BoltzFixError
from .models import Candidate, Check, RunConfig, SelectionReceipt, Proposal
from .providers import choose_provider
from .refinement import refine, availability
from .store import uid
from .validation import diagnostics, select_candidate
from .structure_io import prepare_pair


class Engine:
    def __init__(self, store):
        self.store = store

    def remaining(self, run_id):
        ledger = self.store.ledger(run_id)
        return {
            k: v - ledger.used.get(k, 0) - ledger.reserved.get(k, 0)
            for k, v in ledger.limits.model_dump().items()
        }

    async def candidate(
        self,
        run_id,
        action_id,
        protein,
        ligand,
        mapping,
        *,
        complex_cif=None,
        parent=None,
        provenance=None,
        physics=None,
        confidence=None,
        source="not_reported",
        extra_artifacts=None,
    ):
        config = self.store.check_active(run_id)
        cid = uid("cand")
        artifacts = {
            "protein": self.store.add_artifact(
                run_id, f"{cid}_protein.pdb", protein, "chemical/x-pdb"
            ),
            "ligand": self.store.add_artifact(
                run_id, f"{cid}_ligand.sdf", ligand, "chemical/x-mdl-sdfile"
            ),
            "mapping": self.store.add_artifact(
                run_id,
                f"{cid}_mapping.json",
                json.dumps(mapping, indent=2),
                "application/json",
            ),
        }
        if (
            config.provider != "mock"
            and ca_sequence(self.store.artifact(artifacts["protein"])[1])[0]
            != config.sequence
        ):
            raise BoltzFixError(
                "PROTEIN_IDENTITY",
                "Returned protein sequence differs from immutable input",
            )
        if complex_cif:
            artifacts["complex"] = self.store.add_artifact(
                run_id, f"{cid}.cif", complex_cif, "chemical/x-mmcif"
            )
        if physics:
            artifacts["physics"] = self.store.add_artifact(
                run_id,
                f"{cid}_physics.json",
                json.dumps(physics, indent=2),
                "application/json",
            )
        if extra_artifacts:
            artifacts.update(extra_artifacts)
        start = time.monotonic()
        report = await asyncio.to_thread(
            diagnostics,
            self.store.artifact(artifacts["protein"])[1],
            self.store.artifact(artifacts["ligand"])[1],
            config.ligand_smiles,
            confidence,
            source,
            parent is not None,
        )
        if physics:
            good = physics["local_protocol"]
            report.checks.append(
                Check(
                    id="protocol.local_displacement",
                    status="pass" if good else "fail",
                    category="protocol",
                    value=physics["displacement"],
                    detail="Protein Cα ≤0.5 Å and ligand ≤2 Å after parent-protein alignment",
                )
            )
            if not good:
                report.physical_valid = False
                report.failed_physical += 1
        artifacts["diagnostics"] = self.store.add_artifact(
            run_id,
            f"{cid}_diagnostics.json",
            report.model_dump_json(indent=2),
            "application/json",
        )
        provenance = provenance or {}
        previous = self.store.candidates(run_id)
        if previous:
            try:
                rot, trans, _, _ = protein_transform(
                    self.store.artifact(previous[0].artifact_ids["protein"])[1],
                    self.store.artifact(artifacts["protein"])[1],
                )
                provenance["initial_alignment"] = {
                    "rotation": rot.tolist(),
                    "translation": trans.tolist(),
                    "convention": "row-vector initial @ rotation + translation into candidate frame",
                }
                provenance["pose_diversity_angstrom"] = {
                    old.id: compare_poses(
                        self.store.artifact(artifacts["protein"])[1],
                        self.store.artifact(artifacts["ligand"])[1],
                        self.store.artifact(old.artifact_ids["protein"])[1],
                        self.store.artifact(old.artifact_ids["ligand"])[1],
                    )["ligand_rmsd"]
                    for old in previous
                }
            except BoltzFixError as exc:
                provenance["alignment_error"] = exc.public()
        c = Candidate(
            id=cid,
            run_id=run_id,
            action_id=action_id,
            parent_id=parent.id if parent else None,
            kind="minimized" if parent else "prediction",
            artifact_ids=artifacts,
            diagnostics=report,
            provenance=provenance,
        )
        self.store.add_candidate(c)
        self.store.event(
            run_id,
            "diagnostics_completed",
            {
                "candidate_id": cid,
                "elapsed_seconds": time.monotonic() - start,
                "report": report.model_dump(),
            },
        )
        return c

    async def predict(self, run_id, provider, samples, shared_initial=None):
        config = self.store.check_active(run_id)
        if not shared_initial and hasattr(provider, "prepare"):
            # Resolve chemical identity before charging any prediction allowance.
            encoding = await provider.prepare(config)
            self.store.event(run_id, "ligand_encoding_verified", encoding)
        cost = (
            samples * float(os.getenv("BOLTZFIX_SAMPLE_USD_BOUND", "0"))
            if config.provider != "mock"
            else 0
        )
        action = self.store.reserve(
            run_id,
            "predict",
            {"samples": samples, "prediction_requests": 1},
            {
                "samples": samples,
                "provider": provider.name,
                "billing_mode": (
                    "free_development_api"
                    if (
                        provider.name == "nvidia_nim"
                        and os.getenv("BOLTZFIX_NVIDIA_FREE_TRIAL_CONFIRMED") == "1"
                        or provider.name == "nvidia_diffdock"
                        and os.getenv("BOLTZFIX_DIFFDOCK_FREE_TRIAL_CONFIRMED") == "1"
                    )
                    else "reserved_usd"
                ),
                "cost_bound_usd": cost,
                "shared_initial": shared_initial.id if shared_initial else None,
            },
        )
        try:
            if shared_initial:
                original = self.store.summary(shared_initial.run_id)
                # Cached work is only interchangeable for identical scientific input/backend.
                keys = (
                    "sequence",
                    "ligand_smiles",
                    "msa",
                    "provider",
                    "seed",
                    "protocol_version",
                )
                if (
                    any(getattr(original.config, k) != getattr(config, k) for k in keys)
                    or samples != 1
                ):
                    raise BoltzFixError(
                        "CACHE_MISMATCH", "Initial candidate configuration differs"
                    )

                def read(key):
                    return self.store.artifact(shared_initial.artifact_ids[key])[
                        1
                    ].read_text()

                await self.candidate(
                    run_id,
                    action,
                    read("protein"),
                    read("ligand"),
                    json.loads(read("mapping")),
                    complex_cif=(
                        read("complex")
                        if "complex" in shared_initial.artifact_ids
                        else None
                    ),
                    confidence=shared_initial.diagnostics.confidence,
                    source=shared_initial.diagnostics.confidence_source,
                    provenance={
                        **shared_initial.provenance,
                        "shared_initial_id": shared_initial.id,
                        "logical_allowance_charged": True,
                    },
                )
                self.store.settle(
                    action, actual={"shared_initial": True, "physical_request": False}
                )
                return
            offset = self.store.ledger(run_id).used.get("samples", 0)
            remaining = max(
                1, config.budget.wall_seconds - self.store.ledger(run_id).wall_seconds
            )
            results = await asyncio.wait_for(
                provider.submit_prediction(config, samples, offset), remaining
            )
            # Save the complete returned payload before attempting chemical reconstruction.
            accepted = 0
            rejections = []
            for result in results:
                raw = result.metadata.pop("raw_response", None)
                if raw is not None:
                    self.store.add_artifact(
                        run_id,
                        f"{action}_raw.json",
                        json.dumps(raw),
                        "application/json",
                    )
                raw_id = self.store.add_artifact(
                    run_id, f"{action}_raw.cif", result.complex_cif, "chemical/x-mmcif"
                )
                try:
                    p, l, m = result.prepared or extract_complex(
                        result.complex_cif, config.ligand_smiles
                    )
                    await self.candidate(
                        run_id,
                        action,
                        p,
                        l,
                        m,
                        complex_cif=result.complex_cif,
                        confidence=result.confidence,
                        source=result.source,
                        provenance={
                            **result.metadata,
                            "raw_structure_artifact": raw_id,
                            "provider": provider.name,
                            "msa_hash": (
                                None
                                if not config.msa
                                else __import__("hashlib")
                                .sha256(config.msa.encode())
                                .hexdigest()
                            ),
                        },
                    )
                    accepted += 1
                except BoltzFixError as exc:
                    rejections.append(exc.public())
                    self.store.event(
                        run_id,
                        "candidate_rejected",
                        {"raw_structure_artifact": raw_id, "error": exc.public()},
                    )
            self.store.settle(
                action,
                actual={"returned_samples": len(results), "physical_request": True},
            )
            if not accepted:
                raise BoltzFixError(
                    "NO_ELIGIBLE_PREDICTIONS",
                    "No usable protein–ligand structure returned. "
                    + "; ".join(error["message"] for error in rejections),
                )
        except asyncio.CancelledError:
            self.store.settle(
                action,
                "unknown" if config.provider != "mock" else "failed",
                actual={"cancelled": True, "remote_cancellation_supported": False},
            )
            raise
        except Exception as exc:
            if isinstance(exc, asyncio.TimeoutError):
                exc = BoltzFixError(
                    "REMOTE_COMPLETION_UNKNOWN",
                    "Run timed out while awaiting prediction",
                    pause=True,
                    unknown_remote=config.provider != "mock",
                )
            if getattr(exc, "raw_response", None) is not None:
                self.store.add_artifact(
                    run_id,
                    f"{action}_rejected_response.json",
                    json.dumps(exc.raw_response),
                    "application/json",
                )
            if any(
                a.id == action and a.status == "reserved"
                for a in self.store.actions(run_id)
            ):
                self.store.settle(
                    action,
                    "unknown" if getattr(exc, "unknown_remote", False) else "failed",
                    actual={
                        "error": (
                            exc.public()
                            if isinstance(exc, BoltzFixError)
                            else {"code": "PREDICTION_FAILED"}
                        )
                    },
                )
            raise exc

    async def minimize(self, run_id, candidate_id):
        config = self.store.check_active(run_id)
        c = self.store.candidate(candidate_id)
        if (
            c.run_id != run_id
            or c.kind != "prediction"
            or not c.diagnostics.chemically_eligible
        ):
            raise BoltzFixError("ILLEGAL_ACTION", "Refinement parent is ineligible")
        action = self.store.reserve(
            run_id, "minimize", {"refinements": 1}, {"candidate_id": c.id}
        )
        try:
            if config.provider == "mock":
                # The artificial Cα scaffold has no all-atom force field. Never
                # invent an energy or present a coordinate shift as minimization.
                raise BoltzFixError(
                    "SYNTHETIC_PHYSICS_UNAVAILABLE",
                    "Synthetic Cα scaffold cannot be parameterized as a real protein; original retained",
                )
            p, l, result = await refine(
                self.store.artifact(c.artifact_ids["protein"])[1],
                self.store.artifact(c.artifact_ids["ligand"])[1],
                min(
                    config.budget.refinement_timeout,
                    max(
                        1,
                        int(
                            config.budget.wall_seconds
                            - self.store.ledger(run_id).wall_seconds
                        ),
                    ),
                ),
            )
            mapping = json.loads(
                self.store.artifact(c.artifact_ids["mapping"])[1].read_text()
            )
            import base64

            prepared = result.pop("_preparation_bundle")
            prepared_id = self.store.add_artifact(
                run_id,
                f"{action}_preparation.zip",
                base64.b64decode(prepared),
                "application/zip",
            )
            await self.candidate(
                run_id,
                action,
                p,
                l,
                mapping,
                complex_cif=combine_complex(p, l, mapping),
                extra_artifacts={"prepared_system": prepared_id},
                parent=c,
                physics=result,
                confidence=c.diagnostics.confidence,
                source=c.diagnostics.confidence_source,
                provenance={
                    "physics": result,
                    "confidence_inherited_from": c.id,
                    "synthetic": False,
                },
            )
            self.store.settle(
                action,
                actual={
                    "local_protocol": result["local_protocol"],
                    "latency_seconds": result["latency_seconds"],
                },
            )
        except asyncio.CancelledError:
            self.store.settle(action, "failed", actual={"cancelled": True})
            raise
        except Exception as exc:
            error = (
                exc.public()
                if isinstance(exc, BoltzFixError)
                else {"code": "MINIMIZATION_FAILED", "message": str(exc)[:1000]}
            )
            self.store.settle(
                action, "failed", actual={"error": error, "parent_preserved": True}
            )
            self.store.event(run_id, "refinement_unavailable", error)

    def freeze(self, run_id, reason):
        row = self.store.row(run_id)
        if row["selection"]:
            return
        selected = select_candidate(self.store.candidates(run_id))
        limitations = [
            reason,
            "Physical plausibility and confidence do not establish experimental pose accuracy.",
        ]
        if json.loads(row["config"])["provider"] == "mock":
            limitations.append(
                "SYNTHETIC: not Boltz inference, experimental data, or evidence of collective benefit."
            )
        if selected and not selected.diagnostics.physical_valid:
            limitations.append(
                "Selected candidate fails or lacks applicable physical checks."
            )
        if not selected:
            limitations.append("No chemically eligible candidate available.")
        receipt = SelectionReceipt(
            candidate_id=selected.id if selected else None,
            artifact_hashes=(
                {
                    k: self.store.artifact(v)[0]["sha256"]
                    for k, v in selected.artifact_ids.items()
                }
                if selected
                else {}
            ),
            config_hash=row["config_hash"],
            physical_valid=bool(selected and selected.diagnostics.physical_valid),
            limitations=limitations,
        )
        self.store.freeze(run_id, receipt)

    def context(self, run_id):
        candidates = self.store.candidates(run_id)
        # Coordinates and experimental references never enter the decision service.
        return {
            "candidates": [
                {
                    "id": c.id,
                    "parent_id": c.parent_id,
                    "kind": c.kind,
                    "diagnostics": {
                        "chemically_eligible": c.diagnostics.chemically_eligible,
                        "physical_valid": c.diagnostics.physical_valid,
                        "severe_clashes": c.diagnostics.severe_clashes,
                        "confidence": c.diagnostics.confidence,
                        "confidence_source": c.diagnostics.confidence_source,
                        "confidence_scale": c.provenance.get(
                            "confidence_scale",
                            "model-specific; not experimental accuracy",
                        ),
                        "inherited_confidence": c.diagnostics.inherited_confidence,
                        "checks_passed": sum(
                            check.status == "pass" for check in c.diagnostics.checks
                        ),
                        "checks": [
                            {"id": f"{c.id}/{check.id}", "status": check.status}
                            for check in c.diagnostics.checks
                            if check.status != "pass"
                            or check.id
                            in {
                                "identity.graph",
                                "identity.stereochemistry_3d",
                                "environment.severe_clashes",
                            }
                        ],
                    },
                    "pose_diversity_angstrom": c.provenance.get(
                        "pose_diversity_angstrom"
                    ),
                    "physics": c.provenance.get("physics"),
                }
                for c in candidates
            ],
            "remaining": self.remaining(run_id),
            "refinement_attempts": [
                a.payload.get("candidate_id")
                for a in self.store.actions(run_id)
                if a.action == "minimize"
            ],
            "physics_availability": availability(),
            "recent_events": [
                e
                for e in self.store.events(run_id)
                if e["type"]
                in {"action_executed", "refinement_unavailable", "disagreement"}
            ][-6:],
        }

    async def decide(self, run_id, epoch, config):
        candidates = self.store.candidates(run_id)
        remaining = self.remaining(run_id)
        attempted = {
            a.payload.get("candidate_id")
            for a in self.store.actions(run_id)
            if a.action == "minimize"
        }
        best = select_candidate(candidates)
        can_refine = (
            best
            and best.kind == "prediction"
            and best.id not in attempted
            and remaining["refinements"] > 0
        )
        can_sample = remaining["samples"] > 0 and remaining["prediction_requests"] > 0
        policy = config.policy
        if policy == "initial":
            return None
        if policy in {"fixed_resampling", "fixed_hybrid", "rules"}:
            refine_now = can_refine and (
                policy == "fixed_hybrid"
                and (
                    not can_sample
                    or config.hybrid_schedule == "alternate"
                    and epoch % 2 == 1
                )
                or policy == "rules"
                and not best.diagnostics.physical_valid
            )
            action = "minimize" if refine_now else "resample" if can_sample else "stop"
            p = Proposal(
                id=uid("prop"),
                agent="single",
                action=action,
                candidate_id=best.id if refine_now else None,
                samples=1,
                hypothesis=f"Registered {policy} controller",
                expected_benefit="Baseline action",
                uncertainty="No learned decision",
            )
            return p
        live = LiveAgents(self.store, run_id)
        context = self.context(run_id)
        if policy == "single_agent":
            proposal = (
                await live.call("single", "propose", context)
                if config.agent_mode == "live"
                else synthetic_proposal("single", candidates, remaining, attempted)
            )
            validate_proposal(proposal, candidates, remaining, attempted)
            self.store.event(run_id, "proposal", proposal.model_dump())
            return proposal
        for revision in range(2):
            proposals = []
            for role in ROLES:
                try:
                    p = (
                        await live.call(
                            role, "propose", {**context, "revision": revision}
                        )
                        if config.agent_mode == "live"
                        else synthetic_proposal(role, candidates, remaining, attempted)
                    )
                    validate_proposal(p, candidates, remaining, attempted)
                    proposals.append(p)
                    self.store.event(run_id, "proposal", p.model_dump())
                except BoltzFixError as exc:
                    if exc.pause or exc.code == "BUDGET_EXHAUSTED":
                        raise
                    self.store.event(
                        run_id,
                        "proposal_rejected",
                        {"agent": role, "error": exc.public()},
                    )
            if not proposals:
                return None
            if policy == "isolated":
                # Preregistered pooling: deterministic plurality; no peer messages.
                grouped = {
                    a: sum(p.action == a for p in proposals)
                    for a in {p.action for p in proposals}
                }
                return sorted(
                    proposals, key=lambda p: (-grouped[p.action], ROLES.index(p.agent))
                )[0]
            reviews = []
            for role in ROLES:
                review = (
                    await live.call(
                        role,
                        "review",
                        {
                            **context,
                            "proposals": [p.model_dump() for p in proposals],
                            "revision": revision,
                        },
                    )
                    if config.agent_mode == "live"
                    else synthetic_review(role, proposals)
                )
                if set(review.evidence_ids) - evidence_ids(candidates):
                    raise BoltzFixError(
                        "INVALID_EVIDENCE", "Review refers to missing evidence"
                    )
                reviews.append(review)
                self.store.event(run_id, "review", review.model_dump())
                # With three voters, a two-vote majority cannot be overturned
                # by the last reviewer. Preserve its unused resource allowance.
                if winner(proposals, reviews, epoch) is not None:
                    self.store.event(
                        run_id,
                        "quorum_reached",
                        {
                            "reviews": len(reviews),
                            "required_endorsements": 2,
                            "remaining_review_cannot_change_winner": True,
                        },
                    )
                    break
            selected = winner(proposals, reviews, epoch)
            if selected:
                return selected
            self.store.event(
                run_id,
                "disagreement",
                {
                    "revision": revision,
                    "reason": "No proposal received two endorsements",
                },
            )
            context["previous_proposals"] = [p.model_dump() for p in proposals]
            context["previous_reviews"] = [r.model_dump() for r in reviews]
        return None

    async def run(self, run_id, shared_initial=None):
        config = self.store.check_active(run_id)
        try:
            input_molecule(config.ligand_smiles)
            provider = await choose_provider(config.provider)
            caps = await provider.capabilities()
            self.store.set_status(run_id, "preparing", capabilities=caps.model_dump())
            if not caps.available:
                raise BoltzFixError("PREFLIGHT_REQUIRED", caps.reason, pause=True)
            self.store.set_status(run_id, "running")
            if config.provider == "imported":
                original_protein = self.store.add_artifact(
                    run_id,
                    "submitted_protein.pdb",
                    config.protein_pdb,
                    "chemical/x-pdb",
                )
                for index, sdf in enumerate(config.input_ligands):
                    self.store.check_active(run_id)
                    action = self.store.reserve(
                        run_id, "import", {}, {"index": index, "remote_dispatch": False}
                    )
                    raw = self.store.add_artifact(
                        run_id,
                        f"submitted_pose_{index+1}.sdf",
                        sdf,
                        "chemical/x-mdl-sdfile",
                    )
                    p, l, m, complex_cif = prepare_pair(
                        config.protein_pdb, sdf, config.ligand_smiles
                    )
                    await self.candidate(
                        run_id,
                        action,
                        p,
                        l,
                        m,
                        complex_cif=complex_cif,
                        provenance={
                            "provider": "imported",
                            "source": "user-provided prediction; origin not independently verified",
                            "raw_ligand_artifact": raw,
                            "raw_protein_artifact": original_protein,
                        },
                        source="not_reported",
                    )
                    self.store.settle(
                        action, actual={"remote_dispatch": False, "imported_poses": 1}
                    )
            else:
                await self.predict(
                    run_id, provider, config.initial_samples, shared_initial
                )
            for epoch in range(config.budget.epochs):
                self.store.check_active(run_id)
                if config.policy == "initial":
                    break
                action = self.store.reserve(
                    run_id, "epoch", {"epochs": 1}, {"index": epoch}
                )
                self.store.settle(action)
                proposal = await self.decide(run_id, epoch, config)
                if proposal is None or proposal.action == "stop":
                    break
                attempted = {
                    a.payload.get("candidate_id")
                    for a in self.store.actions(run_id)
                    if a.action == "minimize"
                }
                validate_proposal(
                    proposal,
                    self.store.candidates(run_id),
                    self.remaining(run_id),
                    attempted,
                )
                self.store.event(run_id, "action_executed", proposal.model_dump())
                if proposal.action == "resample":
                    await self.predict(run_id, provider, proposal.samples)
                else:
                    await self.minimize(run_id, proposal.candidate_id)
            self.freeze(
                run_id, "Investigation stopped under its registered resource profile."
            )
            self.store.set_status(
                run_id, "completed" if self.store.candidates(run_id) else "failed"
            )
        except BoltzFixError as exc:
            if exc.code == "CANCELLED":
                self.freeze(run_id, "Cancelled; last completed candidates preserved.")
                self.store.set_status(run_id, "cancelled", error=exc.public())
            elif exc.pause:
                self.store.set_status(run_id, "paused", error=exc.public())
            else:
                self.freeze(run_id, exc.message)
                self.store.set_status(
                    run_id,
                    "partial" if self.store.candidates(run_id) else "failed",
                    error=exc.public(),
                )
        except asyncio.CancelledError:
            self.store.set_status(
                run_id,
                "paused",
                error={
                    "code": "WORKER_STOPPED",
                    "message": "Worker stopped; completed artifacts retained, no automatic retry",
                },
            )
            raise
        except Exception as exc:
            self.store.event(run_id, "internal_error", {"type": type(exc).__name__})
            import logging

            logging.getLogger("boltzfix.worker").exception(
                "Investigation failed: %s", run_id
            )
            self.freeze(
                run_id, "Unexpected execution error; inspect local worker logs."
            )
            self.store.set_status(
                run_id,
                "partial" if self.store.candidates(run_id) else "failed",
                error={"code": "INTERNAL_ERROR", "message": type(exc).__name__},
            )
        finally:
            self.store.export_events(run_id)


async def worker_loop(store, stop):
    owner = uid("worker")
    engine = Engine(store)
    while not stop.is_set():
        if store.acquire_worker(owner):
            store.recover_stale()
            for run_id in store.queued():
                if stop.is_set():
                    break
                if not store.claim(run_id, owner):
                    continue
                task = asyncio.create_task(engine.run(run_id))
                try:
                    while not task.done():
                        store.heartbeat(owner, run_id)
                        if store.row(run_id)["cancel"] or stop.is_set():
                            task.cancel()
                        await asyncio.wait({task}, timeout=0.25)
                    await task
                except asyncio.CancelledError:
                    if store.row(run_id)["cancel"]:
                        engine.freeze(
                            run_id,
                            "Cancelled; outstanding remote work may remain charged.",
                        )
                        store.set_status(run_id, "cancelled")
                    elif stop.is_set():
                        return
                    else:
                        raise
        try:
            await asyncio.wait_for(stop.wait(), 0.5)
        except asyncio.TimeoutError:
            pass
