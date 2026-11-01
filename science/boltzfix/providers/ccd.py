"""Lossless chemical-dictionary encoding; never fetches experimental complexes.

The affected hosted SMILES exporter drops atoms. Resolve an exact CCD identity
instead, independently verifying the search hits with RDKit. No protonation,
tautomer, stereo or similarity substitutions are allowed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import uuid

import httpx
from rdkit import Chem

from ..chemistry import input_molecule
from ..errors import BoltzFixError
from ..models import digest, utcnow

SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
DATA_URL = "https://data.rcsb.org/rest/v1/core/chemcomp/"
VERSION = "rcsb-exact-ccd-v1"


def canonical_identity(smiles: str) -> str:
    molecule = input_molecule(smiles)
    # Atom maps label atoms, and are not part of molecular identity.
    for atom in molecule.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(molecule, isomericSmiles=True)


def verify_record(record: dict, canonical: str) -> bool:
    try:
        code = record["ccd"]
        return (
            re.fullmatch(r"[A-Z0-9]{1,5}", code) is not None
            and record["resolver_version"] == VERSION
            and record["source"] == DATA_URL + code
            and record["canonical_smiles"] == canonical
            and canonical_identity(record["descriptor"]) == canonical
            and record["identity_hash"]
            == digest({"ccd": code, "canonical_smiles": canonical})
        )
    except (KeyError, TypeError, ValueError, BoltzFixError):
        return False


class CCDResolver:
    def __init__(self, cache_dir: Path | None = None, transport=None):
        self.cache_dir = cache_dir
        self.transport = transport
        self.records: dict[str, dict] = {}

    async def resolve(self, smiles: str) -> dict:
        canonical = canonical_identity(smiles)
        if canonical in self.records:
            return dict(self.records[canonical])
        path = (
            self.cache_dir / (digest(canonical) + ".json")
            if self.cache_dir is not None
            else None
        )
        if path is not None and path.is_file():
            try:
                cached = json.loads(path.read_text())
                if verify_record(cached, canonical):
                    self.records[canonical] = cached
                    return dict(cached)
            except (ValueError, OSError):
                pass
            raise BoltzFixError(
                "CCD_CACHE_INVALID",
                "Cached ligand identity failed verification; remove that chemical-dictionary cache entry before retrying.",
                pause=True,
            )
        query = {
            "query": {
                "type": "terminal",
                "service": "chemical",
                "parameters": {
                    "value": canonical,
                    "type": "descriptor",
                    "descriptor_type": "SMILES",
                    "match_type": "graph-exact",
                },
            },
            "return_type": "mol_definition",
            "request_options": {"paginate": {"start": 0, "rows": 10}},
        }
        try:
            # Public dictionary calls receive no prediction/LLM credentials.
            # A total bound includes all requests; there are no retries.
            async with asyncio.timeout(45):
                async with httpx.AsyncClient(
                    timeout=15, follow_redirects=False, transport=self.transport
                ) as client:
                    search = await client.post(SEARCH_URL, json=query)
                    if search.status_code == 204:
                        hits = []
                    else:
                        search.raise_for_status()
                        hits = search.json()["result_set"]
                    for code in sorted(
                        {
                            hit["identifier"]
                            for hit in hits[:10]
                            if re.fullmatch(r"[A-Z0-9]{1,5}", hit["identifier"])
                        }
                    ):
                        response = await client.get(DATA_URL + code)
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        data = response.json()
                        if data.get("chem_comp", {}).get("id") != code:
                            continue
                        descriptors = data.get("pdbx_chem_comp_descriptor", [])
                        for descriptor in descriptors:
                            if descriptor.get("type") != "SMILES_CANONICAL":
                                continue
                            record = {
                                "ccd": code,
                                "canonical_smiles": canonical,
                                "descriptor": descriptor["descriptor"],
                                "source": DATA_URL + code,
                                "resolver_version": VERSION,
                                "resolved_at": utcnow(),
                                "identity_hash": digest(
                                    {"ccd": code, "canonical_smiles": canonical}
                                ),
                            }
                            if not verify_record(record, canonical):
                                continue
                            if path is not None:
                                path.parent.mkdir(parents=True, exist_ok=True)
                                temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                                try:
                                    temp.write_text(json.dumps(record, indent=2))
                                    temp.replace(path)
                                finally:
                                    temp.unlink(missing_ok=True)
                            self.records[canonical] = record
                            return dict(record)
        except (httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            raise BoltzFixError(
                "CCD_LOOKUP_UNAVAILABLE",
                "Exact ligand dictionary lookup is unavailable. No Boltz request was sent; retry when RCSB is reachable or use a previously verified cached ligand.",
                pause=True,
            ) from exc
        raise BoltzFixError(
            "CCD_MATCH_REQUIRED",
            "No exact CCD ligand match was verified. This hosted service currently drops atoms for SMILES input, so this molecule cannot be submitted safely. No Boltz request was sent; no similar molecule was substituted.",
            pause=True,
        )
