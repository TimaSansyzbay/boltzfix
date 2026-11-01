from __future__ import annotations
import asyncio
from dataclasses import dataclass
import json
import math
import os
import time
from pathlib import Path
from typing import Any
import httpx
from ..chemistry import synthetic_fixture
from ..errors import BoltzFixError
from ..models import ProviderCapabilities, RunConfig, digest
from .ccd import CCDResolver

FROZEN_PARAMETERS = {
    "recycling_steps": 3,
    "sampling_steps": 50,
    "without_potentials": False,
    "output_format": "mmcif",
    "step_scale": 1.638,
    "constraints": [],
}
NIM_ENDPOINT = "https://health.api.nvidia.com/v1/biology/mit/boltz2/predict"


@dataclass
class Prediction:
    complex_cif: str
    confidence: float | None
    source: str
    metadata: dict[str, Any]
    prepared: tuple[str, str, dict] | None = None


def live_enabled():
    return (
        os.getenv("BOLTZFIX_ALLOW_LIVE") == "1"
        and os.getenv("BOLTZFIX_SPONSOR_CONFIRMED") == "1"
    )


def require_live():
    if not live_enabled():
        raise BoltzFixError(
            "LIVE_DISABLED",
            "Live requests require explicit sponsor-backed configuration",
            pause=True,
        )


class MockProvider:
    name = "mock"

    async def capabilities(self):
        return ProviderCapabilities(
            name=self.name,
            available=True,
            synthetic=True,
            version="synthetic-fixture-v1",
            sample_control=True,
            custom_msa=True,
            confidence_field="synthetic_confidence",
            schema_hash=digest(FROZEN_PARAMETERS),
            reason="Artificial scaffold; not a Boltz prediction",
        )

    async def submit_prediction(self, config: RunConfig, samples: int, offset=0):
        result = []
        for i in range(samples):
            p, s, c, m = await asyncio.to_thread(
                synthetic_fixture, config.ligand_smiles, offset + i, config.seed
            )
            result.append(
                Prediction(
                    c,
                    0.90 - 0.02 * (offset + i),
                    "synthetic_confidence",
                    {
                        "synthetic": True,
                        "sample_index": offset + i,
                        "parameters": FROZEN_PARAMETERS,
                    },
                    (p, s, m),
                )
            )
        return result

    async def get_status(self, job_id):
        return {"status": "synchronous", "job_id": job_id}

    async def cancel(self, job_id):
        return False


class NvidiaProvider:
    name = "nvidia_nim"

    def __init__(self, transport=None, resolver=None):
        self.transport = transport
        self.resolver = resolver or CCDResolver(
            Path(os.getenv("BOLTZFIX_DATA", ".boltzfix")) / "chemical_dictionary"
        )

    async def prepare(self, config):
        return await self.resolver.resolve(config.ligand_smiles)

    async def capabilities(self):
        blocked = (
            os.getenv("BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK")
            == "incomplete_ligand_coordinates"
        )
        enabled = (
            live_enabled()
            and bool(os.getenv("NVIDIA_API_KEY") or os.getenv("NGC_API_KEY"))
            and not blocked
        )
        return ProviderCapabilities(
            name=self.name,
            available=enabled,
            sample_control=True,
            custom_msa=True,
            confidence_field="confidence_scores",
            schema_hash=digest(
                {
                    "parameters": FROZEN_PARAMETERS,
                    "adapter": "nim-v3-verified-ccd",
                }
            ),
            reason=(
                "Authenticated preflight returned missing/incomplete ligand coordinates. Prediction dispatch is paused until complete output is verified."
                if blocked
                else (
                    "Enabled with exact, independently verified CCD ligand encoding; unmatched SMILES are paused before inference"
                    if enabled
                    else "Disabled until sponsor-backed environment access is configured"
                )
            ),
            details={
                "endpoint": NIM_ENDPOINT,
                "parameters": FROZEN_PARAMETERS,
                "affinity": False,
                "version_status": "deployment does not report a version before inference",
                "cancellation": "not supported by synchronous adapter",
                "ligand_encoding": "verified_ccd_only",
                "dictionary": "RCSB chemical definitions only; no experimental poses",
                "smiles_export": "disabled: missing-atom regression nvbug 6932678",
            },
        )

    @staticmethod
    def payload(config, samples, *, ccd):
        polymer = {"id": "A", "molecule_type": "protein", "sequence": config.sequence}
        if config.msa:
            polymer["msa"] = {
                "cached": {"a3m": {"alignment": config.msa, "format": "a3m", "rank": 0}}
            }
        return {
            **FROZEN_PARAMETERS,
            "polymers": [polymer],
            "ligands": [{"id": "L", "ccd": ccd, "predict_affinity": False}],
            "diffusion_samples": samples,
        }

    @staticmethod
    def parse(data, samples):
        structures = data.get("structures", [])
        confidence = data.get("confidence_scores", [])
        if len(structures) != samples or len(confidence) != samples:
            raise BoltzFixError(
                "PROVIDER_SCHEMA",
                "Candidate/confidence count does not match requested samples",
            )
        result = []
        for i, (structure, score) in enumerate(zip(structures, confidence)):
            if (
                not isinstance(structure, dict)
                or structure.get("format") not in {"mmcif", "cif"}
                or not isinstance(structure.get("structure"), str)
            ):
                raise BoltzFixError(
                    "PROVIDER_SCHEMA", "Missing downloadable mmCIF candidate"
                )
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                raise BoltzFixError("PROVIDER_SCHEMA", "Invalid candidate confidence")
            result.append(
                Prediction(
                    structure["structure"],
                    float(score),
                    "confidence_scores",
                    {
                        "candidate_index": i,
                        "provider_candidate_name": structure.get("name"),
                        "model_version": data.get("model_version", "not_reported"),
                        "metrics": data.get("metrics", {}),
                        "parameters": FROZEN_PARAMETERS,
                        "response_schema_hash": digest(sorted(data)),
                    },
                )
            )
        return result

    async def submit_prediction(self, config, samples, offset=0):
        require_live()
        if (
            os.getenv("BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK")
            == "incomplete_ligand_coordinates"
        ):
            raise BoltzFixError(
                "PREFLIGHT_REQUIRED",
                "Prediction dispatch paused: hosted output omitted required ligand coordinates.",
                pause=True,
            )
        token = os.getenv("NVIDIA_API_KEY") or os.getenv("NGC_API_KEY")
        if not token:
            raise BoltzFixError(
                "AUTH_REQUIRED", "NVIDIA credential is not configured", pause=True
            )
        encoding = await self.prepare(config)
        payload = self.payload(config, samples, ccd=encoding["ccd"])
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(900, connect=20),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    NIM_ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BoltzFixError(
                "REMOTE_COMPLETION_UNKNOWN",
                "Prediction connection ended; no automatic resubmission",
                pause=True,
                unknown_remote=True,
            ) from exc
        if response.status_code in {401, 403, 402, 429}:
            raise BoltzFixError(
                "PROVIDER_ACCESS",
                f"Provider returned HTTP {response.status_code}; campaign paused",
                pause=True,
            )
        if response.status_code >= 500 or response.status_code == 202:
            raise BoltzFixError(
                "REMOTE_COMPLETION_UNKNOWN",
                f"Unresolved provider response HTTP {response.status_code}; no resubmission",
                pause=True,
                unknown_remote=True,
            )
        if response.status_code != 200:
            raise BoltzFixError(
                "PROVIDER_REJECTED",
                f"Provider rejected request: HTTP {response.status_code}",
            )
        try:
            data = response.json()
            # Raw response is attached for preservation even if normalized parsing fails.
            try:
                results = self.parse(data, samples)
            except BoltzFixError as exc:
                exc.raw_response = data
                raise
        except (ValueError, TypeError, AttributeError) as exc:
            raise BoltzFixError(
                "PROVIDER_SCHEMA", "Provider response is not valid candidate JSON"
            ) from exc
        for item in results:
            item.metadata.update(
                {
                    "service_latency_seconds": time.monotonic() - start,
                    "raw_response": data,
                    "request_hash": digest(payload),
                    "ligand_encoding": {"format": "ccd", **encoding},
                    "msa_policy": (
                        "cached" if config.msa else "service_default_not_cached"
                    ),
                }
            )
        return results

    async def get_status(self, job_id):
        return {
            "status": "unknown",
            "reason": "Synchronous service has no verified polling interface",
        }

    async def cancel(self, job_id):
        return False


class NebiusProvider:
    """Discover the authenticated schema; never guess scientific tool names/payloads.

    A verified mapping can be installed after access is supplied. Discovery alone
    deliberately does not make this backend eligible for a scientific campaign.
    """

    name = "nebius_mcp"

    async def discover(self):
        require_live()
        key = os.getenv("NEBIUS_SCIENTIFIC_AI_API_KEY")
        endpoint = os.getenv("NEBIUS_MCP_URL")
        if not key or not endpoint:
            raise BoltzFixError(
                "AUTH_REQUIRED",
                "Nebius credential and verified MCP URL are not configured",
                pause=True,
            )
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            endpoint, headers={"Authorization": f"Bearer {key}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                return [t.model_dump(mode="json") for t in listing.tools]

    async def capabilities(self):
        return ProviderCapabilities(
            name=self.name,
            available=False,
            reason="Authenticated tool schema and verified candidate/job mapping required; discovery command is available",
        )

    async def submit_prediction(self, config, samples, offset=0):
        raise BoltzFixError(
            "PREFLIGHT_REQUIRED",
            "Discover and verify Nebius schemas before enabling scientific dispatch",
            pause=True,
        )


async def choose_provider(name):
    if name == "imported":
        return ImportedProvider()
    if name == "nvidia_diffdock":
        from .diffdock import DiffDockProvider

        return DiffDockProvider()
    if name == "mock":
        return MockProvider()
    if name == "nebius_mcp":
        return NebiusProvider()
    if name == "nvidia_nim":
        return NvidiaProvider()
    # No fallback after a backend is chosen. Nebius is ineligible until its live
    # tool mapping is verified, so the documented NVIDIA adapter is next.
    nebius = NebiusProvider()
    if (await nebius.capabilities()).available:
        return nebius
    nvidia = NvidiaProvider()
    if (await nvidia.capabilities()).available:
        return nvidia
    raise BoltzFixError(
        "NO_BACKEND",
        "No verified sponsor-backed prediction backend is enabled",
        pause=True,
    )


class ImportedProvider:
    name = "imported"

    async def capabilities(self):
        return ProviderCapabilities(
            name=self.name,
            available=True,
            version="verified-structure-import-v1",
            reason="Review supplied predictions without a prediction-service dependency",
            details={"source": "user-provided structures", "remote_prediction": False},
        )

    async def submit_prediction(self, *args, **kwargs):
        raise BoltzFixError(
            "RESAMPLING_UNAVAILABLE",
            "Imported review cannot generate new predictions; start a docking run to generate poses",
        )
