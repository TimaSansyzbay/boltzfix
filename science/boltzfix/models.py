from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    return hashlib.sha256(raw).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BudgetLimits(Strict):
    samples: int = Field(4, ge=0, le=4)
    prediction_requests: int = Field(4, ge=0, le=4)
    refinements: int = Field(2, ge=0, le=2)
    epochs: int = Field(6, ge=1, le=6)
    llm_calls: int = Field(36, ge=0, le=36)
    llm_tokens: int = Field(60000, ge=0, le=60000)
    wall_seconds: int = Field(1800, ge=10, le=1800)
    refinement_timeout: int = Field(300, ge=1, le=300)

    @classmethod
    def demo(cls):
        return cls(
            samples=2,
            prediction_requests=2,
            refinements=1,
            epochs=3,
            llm_calls=18,
            llm_tokens=30000,
            wall_seconds=900,
        )


class RunConfig(Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    name: str = Field("Untitled investigation", min_length=1, max_length=100)
    case_id: str = Field("interactive", pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    sequence: str = Field(min_length=100, max_length=400)
    ligand_smiles: str = Field(min_length=1, max_length=2000)
    msa: str | None = Field(None, max_length=3000000)
    provider: Literal[
        "mock", "auto", "nvidia_nim", "nebius_mcp", "nvidia_diffdock", "imported"
    ] = "mock"
    protein_pdb: str | None = Field(None, max_length=3000000)
    initial_samples: int = Field(1, ge=0, le=4)
    input_ligands: list[str] = Field(default_factory=list, max_length=4)
    policy: Literal[
        "collective",
        "single_agent",
        "fixed_resampling",
        "fixed_hybrid",
        "rules",
        "initial",
        "isolated",
    ] = "collective"
    agent_mode: Literal["synthetic", "live"] = "synthetic"
    budget: BudgetLimits = Field(default_factory=BudgetLimits.demo)
    seed: int = 20261031
    protocol_version: Literal["boltzfix-v1", "boltzfix-v2"] = "boltzfix-v1"
    llm_model: str = Field("claude-opus-5-5", min_length=1, max_length=100)
    hybrid_schedule: Literal["sample_then_minimize", "alternate"] = (
        "sample_then_minimize"
    )

    @field_validator("sequence", mode="before")
    @classmethod
    def clean_sequence(cls, value):
        if not isinstance(value, str):
            raise ValueError("Protein sequence must be text")
        return (
            "".join(x.strip() for x in value.splitlines() if not x.startswith(">"))
            .replace(" ", "")
            .upper()
        )

    @field_validator("sequence")
    @classmethod
    def standard_residues(cls, value):
        if set(value) - set("ACDEFGHIKLMNPQRSTVWY"):
            raise ValueError("Only the 20 standard amino acids are supported")
        return value

    @model_validator(mode="after")
    def separate_mock_and_live(self):
        if self.provider == "imported":
            if (
                not self.protein_pdb
                or not self.input_ligands
                or self.initial_samples != 0
            ):
                raise ValueError(
                    "Imported review requires protein, ligand poses and zero generated initial samples"
                )
            if self.budget.samples != 0 or self.budget.prediction_requests != 0:
                raise ValueError(
                    "Imported review must disable remote prediction allowances"
                )
        elif self.initial_samples < 1:
            raise ValueError("Prediction workflows need at least one initial sample")
        if any(len(sdf) > 1000000 for sdf in self.input_ligands):
            raise ValueError("Maximum 1 MB per ligand pose")
        if self.initial_samples > self.budget.samples:
            raise ValueError("Initial pose count exceeds the sample allowance")
        if self.initial_samples != 1 and self.protocol_version == "boltzfix-v1":
            raise ValueError(
                "Multiple initial samples require the explicit v2 protocol"
            )
        if self.provider == "nvidia_diffdock" and not self.protein_pdb:
            raise ValueError("DiffDock requires a protein PDB")
        if self.msa:
            rows = []
            started = False
            for line in self.msa.splitlines():
                if line.startswith(">"):
                    if started:
                        break
                    started = True
                elif line.strip():
                    rows.append(line.strip())
                    started = True
            query = "".join(rows).replace("-", "").replace(".", "").upper()
            if query != self.sequence:
                raise ValueError(
                    "The first cached MSA sequence must match the submitted protein"
                )
        if (
            self.provider != "mock"
            and self.agent_mode == "synthetic"
            and self.policy in {"collective", "single_agent", "isolated"}
        ):
            raise ValueError(
                "Synthetic agents are restricted to explicitly synthetic mock runs"
            )
        if self.provider == "mock" and self.agent_mode == "live":
            raise ValueError(
                "Live LLMs cannot be used to present mock fixtures as live science"
            )
        return self


class Check(Strict):
    id: str
    status: Literal["pass", "fail", "unknown", "not_applicable", "tool_error"]
    category: Literal["identity", "geometry", "environment", "protocol", "tool"]
    value: Any = None
    detail: str = ""


class DiagnosticReport(Strict):
    version: str = "diagnostics-v1"
    checks: list[Check]
    chemically_eligible: bool
    physical_valid: bool
    failed_physical: int
    severe_clashes: int
    confidence: float | None = None
    confidence_source: str = "not_reported"
    inherited_confidence: bool = False
    closest_contacts: list[dict[str, Any]] = Field(default_factory=list)
    tool_versions: dict[str, str] = Field(default_factory=dict)


class Candidate(Strict):
    id: str
    run_id: str
    parent_id: str | None = None
    action_id: str
    kind: Literal["prediction", "minimized"]
    artifact_ids: dict[str, str]
    diagnostics: DiagnosticReport
    provenance: dict[str, Any]
    created_at: str = Field(default_factory=utcnow)


class Proposal(Strict):
    id: str
    agent: Literal["chemistry", "structure", "physics", "single"]
    action: Literal["resample", "minimize", "stop"]
    candidate_id: str | None = None
    samples: int = Field(1, ge=1, le=3)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    hypothesis: str = Field(max_length=1200)
    expected_benefit: str = Field(max_length=1200)
    uncertainty: str = Field(max_length=1200)


class PeerReview(Strict):
    agent: Literal["chemistry", "structure", "physics"]
    proposal_id: str
    endorse: bool
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(max_length=1200)


class ProviderCapabilities(Strict):
    name: str
    available: bool
    synthetic: bool = False
    version: str = "not_reported"
    sample_control: bool = False
    custom_msa: bool = False
    cancellation: bool = False
    gpu_telemetry: bool = False
    confidence_field: str = "not_reported"
    schema_hash: str = "not_reported"
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class RunStatus(StrEnum):
    queued = "queued"
    preparing = "preparing"
    running = "running"
    paused = "paused"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL = {"completed", "partial", "failed", "cancelled", "paused"}


class BudgetLedger(Strict):
    limits: BudgetLimits
    used: dict[str, int]
    reserved: dict[str, int]
    wall_seconds: float
    outstanding_remote: list[str]
    timing_basis: str = "service allowances and wall time; not equal GPU time"


class ActionRecord(Strict):
    id: str
    run_id: str
    action: str
    status: str
    reservation: dict[str, int]
    payload: dict[str, Any]
    started_at: str
    finished_at: str | None
    actual: dict[str, Any]


class SelectionReceipt(Strict):
    candidate_id: str | None
    artifact_hashes: dict[str, str]
    config_hash: str
    selector_version: str = "validity-clashes-confidence-v1"
    physical_valid: bool
    limitations: list[str]
    frozen_at: str = Field(default_factory=utcnow)


class RunSummary(Strict):
    id: str
    status: RunStatus
    config: RunConfig
    config_hash: str
    synthetic: bool
    created_at: str
    updated_at: str
    capabilities: dict[str, Any]
    candidates: list[Candidate]
    budget: BudgetLedger
    selection: SelectionReceipt | None
    error: dict[str, Any] | None
