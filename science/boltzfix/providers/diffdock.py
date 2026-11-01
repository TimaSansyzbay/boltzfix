"""Explicit NVIDIA DiffDock backend. Complete SDF poses; no silent fallback."""

from __future__ import annotations
import math
import os
import time
import httpx

from .hosted import Prediction, require_live, live_enabled
from ..errors import BoltzFixError
from ..models import ProviderCapabilities, digest
from ..structure_io import protein_input, prepare_pair, conformer_from_smiles

ENDPOINT = "https://health.api.nvidia.com/v1/biology/mit/diffdock"
PARAMETERS = {
    "time_divisions": 20,
    "steps": 18,
    "save_trajectory": False,
    "is_staged": False,
}


class DiffDockProvider:
    name = "nvidia_diffdock"

    def __init__(self, transport=None):
        self.transport = transport

    async def capabilities(self):
        return ProviderCapabilities(
            name=self.name,
            available=live_enabled()
            and bool(os.getenv("NVIDIA_API_KEY") or os.getenv("NGC_API_KEY"))
            and os.getenv("BOLTZFIX_DIFFDOCK_FREE_TRIAL_CONFIRMED") == "1",
            sample_control=True,
            confidence_field="position_confidence",
            schema_hash=digest(
                {"adapter": "diffdock-sdf-v1", "parameters": PARAMETERS}
            ),
            reason="Dock complete ligand poses against a supplied protein structure; separate from sequence cofolding",
            details={
                "endpoint": ENDPOINT,
                "parameters": PARAMETERS,
                "requires": "protein_pdb",
                "confidence_scale": "uncalibrated ranking score, not a probability",
                "version_status": "not reported until response",
                "sampling": "multiple poses per metered request; diversity measured after generation",
            },
        )

    async def prepare(self, config):
        if not config.protein_pdb:
            raise BoltzFixError(
                "PROTEIN_REQUIRED",
                "DiffDock needs a protein PDB from AlphaFold, Boltz or your structure pipeline",
            )
        protein, sequence = protein_input(config.protein_pdb)
        if sequence != config.sequence:
            raise BoltzFixError(
                "PROTEIN_IDENTITY",
                "Protein PDB sequence differs from the registered sequence",
            )
        protein = (
            "\n".join(line for line in protein.splitlines() if line.startswith("ATOM"))
            + "\n"
        )
        return {
            "format": "sdf",
            "ligand_identity": "submitted isomeric SMILES",
            "protein_sha256": digest(protein.encode()),
            "source": "provided protein; no native ligand or binding pocket supplied",
        }

    @staticmethod
    def payload(config, samples):
        protein, _ = protein_input(config.protein_pdb)
        protein = (
            "\n".join(line for line in protein.splitlines() if line.startswith("ATOM"))
            + "\n"
        )
        return {
            **PARAMETERS,
            "protein": protein,
            "ligand": conformer_from_smiles(config.ligand_smiles, config.seed),
            "ligand_file_type": "sdf",
            "num_poses": samples,
        }

    @staticmethod
    def parse(data, config, samples):
        if data.get("status") != "success":
            raise BoltzFixError(
                "DOCKING_FAILED", "DiffDock did not return a successful docking result"
            )
        poses = data.get("ligand_positions", [])
        scores = data.get("position_confidence", [])
        if len(poses) != samples or len(scores) != samples:
            raise BoltzFixError(
                "PROVIDER_SCHEMA",
                "DiffDock pose/confidence count differs from requested allowance",
            )
        results = []
        for index, (sdf, score) in enumerate(zip(poses, scores)):
            if (
                not isinstance(sdf, str)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise BoltzFixError(
                    "PROVIDER_SCHEMA", "Invalid DiffDock candidate or confidence"
                )
            p, l, m, c = prepare_pair(config.protein_pdb, sdf, config.ligand_smiles)
            results.append(
                Prediction(
                    c,
                    float(score),
                    "position_confidence",
                    {
                        "candidate_index": index,
                        "model_version": data.get("model_version", "not_reported"),
                        "parameters": PARAMETERS,
                        "response_schema_hash": digest(sorted(data)),
                        "confidence_scale": "uncalibrated ranking score",
                    },
                    (p, l, m),
                )
            )
        return results

    async def submit_prediction(self, config, samples, offset=0):
        require_live()
        if not (await self.capabilities()).available:
            raise BoltzFixError(
                "PREFLIGHT_REQUIRED",
                "Sponsor-backed DiffDock free-development access is not configured",
                pause=True,
            )
        await self.prepare(config)
        payload = self.payload(config, samples)
        token = os.getenv("NVIDIA_API_KEY") or os.getenv("NGC_API_KEY")
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(900, connect=20),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BoltzFixError(
                "REMOTE_COMPLETION_UNKNOWN",
                "Docking completion unknown; no automatic retry",
                pause=True,
                unknown_remote=True,
            ) from exc
        if response.status_code in {401, 402, 403, 429}:
            raise BoltzFixError(
                "PROVIDER_ACCESS",
                f"DiffDock returned HTTP {response.status_code}",
                pause=True,
            )
        if response.status_code >= 500 or response.status_code == 202:
            raise BoltzFixError(
                "REMOTE_COMPLETION_UNKNOWN",
                f"Unresolved DiffDock HTTP {response.status_code}; no retry",
                pause=True,
                unknown_remote=True,
            )
        if response.status_code != 200:
            raise BoltzFixError(
                "PROVIDER_REJECTED",
                f"DiffDock rejected this request: HTTP {response.status_code}",
            )
        data = response.json()
        try:
            results = self.parse(data, config, samples)
        except BoltzFixError as exc:
            exc.raw_response = data
            raise
        for result in results:
            result.metadata.update(
                {
                    "raw_response": data,
                    "request_hash": digest(payload),
                    "service_latency_seconds": time.monotonic() - start,
                    "protein_input": "provided_structure",
                    "random_seed_control": "not exposed by this adapter",
                }
            )
        return results

    async def cancel(self, job_id):
        return False
