"""Phase 0 domain entities: Source, RightsPolicy, MediaAsset.

Field sets follow docs/DATA_MODEL_AND_METRICS.md §1. The D009 rights invariants are
enforced here, in the model, so that a mis-entered record cannot exist in memory or in
the database at all (validation error, never silent correction).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


def utc_now() -> datetime:
    """Timezone-aware current time. The only clock the domain uses."""
    return datetime.now(tz=UTC)


def new_id(prefix: str) -> str:
    """Opaque, URL-safe identifier such as ``src_3f9a1c2b7e4d5a60``."""
    return f"{prefix}_{secrets.token_hex(8)}"


class BasisType(StrEnum):
    """Rights basis classes (docs/RIGHTS_AND_PLATFORM_GUARDRAILS.md §2)."""

    OWNED = "OWNED"
    EXPLICIT_LICENSE = "EXPLICIT_LICENSE"
    CREATOR_AUTHORIZATION = "CREATOR_AUTHORIZATION"
    PUBLIC_DOMAIN_VERIFIED = "PUBLIC_DOMAIN_VERIFIED"
    TRANSFORMATIVE_REVIEW_REQUIRED = "TRANSFORMATIVE_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


#: Basis types that can never carry a permissive capability flag (D009).
FORCED_BLOCK_BASES: frozenset[BasisType] = frozenset({BasisType.BLOCKED, BasisType.UNKNOWN})

#: Basis types that assert a permission and therefore need recorded evidence
#: (guardrails §2, SOURCES.md: "a row without an evidence reference is UNKNOWN").
EVIDENCE_REQUIRED_BASES: frozenset[BasisType] = frozenset(
    {
        BasisType.OWNED,
        BasisType.EXPLICIT_LICENSE,
        BasisType.CREATOR_AUTHORIZATION,
        BasisType.PUBLIC_DOMAIN_VERIFIED,
        BasisType.TRANSFORMATIVE_REVIEW_REQUIRED,
    }
)


class SourceKind(StrEnum):
    LOCAL_FILE = "LOCAL_FILE"
    URL = "URL"


class SourceStatus(StrEnum):
    REGISTERED = "REGISTERED"
    INGESTED = "INGESTED"
    BLOCKED = "BLOCKED"
    RETIRED = "RETIRED"


class _Entity(BaseModel):
    """Immutable, strict base: unknown fields are an error, instances are frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def _require_aware(value: datetime | None, field: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        msg = f"{field} must be timezone-aware"
        raise ValueError(msg)
    return value


class RightsPolicy(_Entity):
    """Rights policy attached to a source.

    Invariants (D009), enforced by ``enforce_d009``:

    * ``basis_type in {BLOCKED, UNKNOWN}`` => every ``can_*`` flag is false;
    * ``basis_type == TRANSFORMATIVE_REVIEW_REQUIRED`` => ``can_publish`` is false and
      ``review_required`` is true;
    * a permissive basis type requires a non-empty ``basis_reference`` (evidence);
    * ``expiry_at`` in the past makes the policy evaluate as ``BLOCKED`` regardless of
      flags — see :meth:`effective_basis`; this is time-dependent, so it is evaluated at
      check time by the rights gate, not at construction.

    A violation raises a validation error. Nothing is corrected silently.
    """

    id: str = Field(min_length=1)
    basis_type: BasisType
    basis_reference: str | None = None
    can_ingest: bool = False
    can_extract_clip: bool = False
    can_transform: bool = False
    can_publish: bool = False
    requires_attribution: bool = False
    attribution_text: str | None = None
    territory_notes: str | None = None
    expiry_at: datetime | None = None
    review_required: bool = False
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("expiry_at", "created_at")
    @classmethod
    def _aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        return _require_aware(value, info.field_name or "datetime")

    @model_validator(mode="after")
    def enforce_d009(self) -> RightsPolicy:
        flags = {
            "can_ingest": self.can_ingest,
            "can_extract_clip": self.can_extract_clip,
            "can_transform": self.can_transform,
            "can_publish": self.can_publish,
        }
        if self.basis_type in FORCED_BLOCK_BASES:
            offending = sorted(name for name, on in flags.items() if on)
            if offending:
                msg = (
                    f"D009: basis_type={self.basis_type.value} forbids permissive flags, "
                    f"got {', '.join(offending)}=true"
                )
                raise ValueError(msg)
        if self.basis_type is BasisType.TRANSFORMATIVE_REVIEW_REQUIRED:
            if self.can_publish:
                msg = "D009: TRANSFORMATIVE_REVIEW_REQUIRED forbids can_publish=true"
                raise ValueError(msg)
            if not self.review_required:
                msg = "D009: TRANSFORMATIVE_REVIEW_REQUIRED requires review_required=true"
                raise ValueError(msg)
        if self.basis_type in EVIDENCE_REQUIRED_BASES and not (self.basis_reference or "").strip():
            msg = (
                f"basis_type={self.basis_type.value} requires basis_reference "
                "(evidence for the rights basis; without evidence the source is UNKNOWN)"
            )
            raise ValueError(msg)
        if self.requires_attribution and not (self.attribution_text or "").strip():
            msg = "requires_attribution=true requires attribution_text"
            raise ValueError(msg)
        return self

    def is_expired(self, now: datetime) -> bool:
        _require_aware(now, "now")
        return self.expiry_at is not None and self.expiry_at <= now

    def effective_basis(self, now: datetime) -> BasisType:
        """Basis type after applying expiry: an expired policy *is* ``BLOCKED``."""
        return BasisType.BLOCKED if self.is_expired(now) else self.basis_type


class Source(_Entity):
    """A registered long-form source. ``rights_policy_id`` is ``None`` until a policy
    is attached; the rights gate treats a missing policy as ``UNKNOWN`` (fail closed)."""

    id: str = Field(min_length=1)
    kind: SourceKind
    canonical_uri: str = Field(min_length=1)
    publisher: str | None = None
    title: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    rights_policy_id: str | None = None
    status: SourceStatus = SourceStatus.REGISTERED

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


class MediaAsset(_Entity):
    """A fingerprinted, probed media file that passed the ingest gate."""

    id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path_or_object_key: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    audio_codec: str | None = None
    video_codec: str | None = None
    ingested_at: datetime = Field(default_factory=utc_now)

    @field_validator("ingested_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "ingested_at")
        return value
