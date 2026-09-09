"""Versioned rejection-reason taxonomy. Codes are data; unknown codes are visible errors."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TAXONOMY_RESOURCE = "reasons_v1.json"
OTHER = "other"


class ReasonTaxonomy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    codes: dict[str, str] = Field(min_length=1)


class UnknownReasonError(ValueError):
    pass


def load_taxonomy(path: Path | None = None) -> ReasonTaxonomy:
    if path is None:
        text = (
            resources.files("vme.labeling.taxonomy_files")
            .joinpath(DEFAULT_TAXONOMY_RESOURCE)
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    return ReasonTaxonomy.model_validate(json.loads(text))


def validate_reasons(
    reasons: list[str], taxonomy: ReasonTaxonomy, *, note: str | None
) -> list[str]:
    """Normalise, de-duplicate and check codes; ``other`` needs a note."""
    cleaned: list[str] = []
    for raw in reasons:
        code = raw.strip().lower()
        if code and code not in cleaned:
            cleaned.append(code)
    unknown = [c for c in cleaned if c not in taxonomy.codes]
    if unknown:
        msg = (
            f"unknown reason code(s) {unknown}; taxonomy {taxonomy.version} allows: "
            f"{', '.join(sorted(taxonomy.codes))}"
        )
        raise UnknownReasonError(msg)
    if OTHER in cleaned and not (note or "").strip():
        msg = "reason 'other' requires a note"
        raise UnknownReasonError(msg)
    return cleaned
