"""Versioned weight configuration (DATA_MODEL §2). Weights are data, never code."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from vme.ranking.schemas import COMPONENTS, PENALTIES

DEFAULT_WEIGHTS_RESOURCE = "viral_v0.json"
_SUM_TOLERANCE = 1e-6


class Weights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    components: dict[str, float]
    penalties: dict[str, float]

    @model_validator(mode="after")
    def _contract(self) -> Weights:
        if set(self.components) != set(COMPONENTS):
            msg = f"component weights must cover exactly {sorted(COMPONENTS)}"
            raise ValueError(msg)
        if set(self.penalties) != set(PENALTIES):
            msg = f"penalty weights must cover exactly {sorted(PENALTIES)}"
            raise ValueError(msg)
        total = sum(self.components.values())
        if abs(total - 100.0) > _SUM_TOLERANCE or any(w < 0 for w in self.components.values()):
            msg = f"component weights must be >= 0 and sum to 100, got {total}"
            raise ValueError(msg)
        if any(not 0.0 <= w <= 100.0 for w in self.penalties.values()):
            msg = "penalty weights must each be within [0, 100]"
            raise ValueError(msg)
        return self


def load_weights(path: Path | None = None) -> Weights:
    """Load from ``path`` or the packaged default. Invalid files fail loudly."""
    if path is None:
        text = (
            resources.files("vme.ranking.weight_files")
            .joinpath(DEFAULT_WEIGHTS_RESOURCE)
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    return Weights.model_validate(json.loads(text))
