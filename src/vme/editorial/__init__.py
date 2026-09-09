"""Editorial transformation (text-only, D014), claims (D010) and human review."""

from vme.editorial.review import ReviewError, approve, reject, resolve_claim
from vme.editorial.service import (
    EditorialConfig,
    EditorialError,
    EditorialResult,
    generate_editorial,
)

__all__ = [
    "EditorialConfig",
    "EditorialError",
    "EditorialResult",
    "ReviewError",
    "approve",
    "generate_editorial",
    "reject",
    "resolve_claim",
]
