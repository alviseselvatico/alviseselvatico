"""Phase 0 domain entities: Source, RightsPolicy, MediaAsset.

Field sets follow docs/DATA_MODEL_AND_METRICS.md §1. The D009 rights invariants are
enforced here, in the model, so that a mis-entered record cannot exist in memory or in
the database at all (validation error, never silent correction).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

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


# ----------------------------------------------------------------------- transcripts


class TranscriptKind(StrEnum):
    RAW = "RAW"
    CORRECTED = "CORRECTED"
    ENRICHED = "ENRICHED"


class Word(_Entity):
    text: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    probability: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Word:
        if self.end_ms < self.start_ms:
            msg = f"word {self.text!r}: end_ms {self.end_ms} < start_ms {self.start_ms}"
            raise ValueError(msg)
        return self


class TranscriptSegment(_Entity):
    """Provider segment with word-level timestamps where the provider supplies them."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    words: list[Word] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> TranscriptSegment:
        if self.end_ms < self.start_ms:
            msg = f"segment: end_ms {self.end_ms} < start_ms {self.start_ms}"
            raise ValueError(msg)
        return self


class Transcript(_Entity):
    """``RAW`` transcripts are immutable. Corrections are new rows pointing at the parent."""

    id: str = Field(min_length=1)
    media_asset_id: str = Field(min_length=1)
    kind: TranscriptKind
    derived_from_id: str | None = None
    version: int = Field(ge=1)
    provider: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_alias: str = Field(min_length=1)
    language: str | None = None
    raw_text: str
    segments: list[TranscriptSegment]
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value

    @model_validator(mode="after")
    def _lineage(self) -> Transcript:
        if self.kind is TranscriptKind.RAW and self.derived_from_id is not None:
            msg = "RAW transcript cannot have derived_from_id"
            raise ValueError(msg)
        if self.kind is not TranscriptKind.RAW and self.derived_from_id is None:
            msg = f"{self.kind.value} transcript requires derived_from_id"
            raise ValueError(msg)
        return self

    @property
    def duration_ms(self) -> int:
        return max((s.end_ms for s in self.segments), default=0)

    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


class Candidate(_Entity):
    """A semantic span of a transcript proposed for short-form treatment."""

    id: str = Field(min_length=1)
    transcript_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    context_before: str = ""
    context_after: str = ""
    speaker: str | None = None
    topic: str | None = None
    candidate_text: str = Field(min_length=1)
    created_by: str = Field(min_length=1, description="e.g. 'segmenter:v0.1.0' or 'operator'")
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Candidate:
        if self.end_ms <= self.start_ms:
            msg = f"candidate: end_ms {self.end_ms} must be > start_ms {self.start_ms}"
            raise ValueError(msg)
        return self

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# -------------------------------------------------------------------- LLM + ranking


class LlmValidationStatus(StrEnum):
    VALID = "VALID"
    INVALID_RETRIED = "INVALID_RETRIED"
    FAILED = "FAILED"


class LlmCall(_Entity):
    """One row per LLM invocation that influences an output (ARCHITECTURE §6)."""

    id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    input_artifact_refs: list[str] = Field(default_factory=list)
    prompt_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_alias: str = Field(min_length=1)
    model_id_reported: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    validation_status: LlmValidationStatus
    attempts: int = Field(ge=1)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


class RankingBatch(_Entity):
    """One ranking execution over a transcript's candidates."""

    id: str = Field(min_length=1)
    transcript_id: str = Field(min_length=1)
    scoring_version: str = Field(min_length=1)
    weights_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_alias: str = Field(min_length=1, description="alias of the decisive tier, e.g. strong")
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


class RankingRun(_Entity):
    """Score of one candidate inside a batch. Components and penalties are persisted."""

    id: str = Field(min_length=1)
    ranking_batch_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    features: dict[str, float] = Field(default_factory=dict)
    risks: dict[str, float] = Field(default_factory=dict)
    final_score: float = Field(ge=0.0, le=100.0)
    rationale: str
    llm_call_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


# ------------------------------------------------------------ editorial + fact check


class DraftStatus(StrEnum):
    GENERATED = "generated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED_RIGHTS = "blocked_rights"
    BLOCKED_FACTCHECK = "blocked_factcheck"
    PUBLISHED = "published"
    RETIRED = "retired"


#: Allowed transitions (DATA_MODEL §1, draft state machine). ``published`` is Phase 3+.
DRAFT_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {
    DraftStatus.GENERATED: frozenset({DraftStatus.NEEDS_REVIEW, DraftStatus.BLOCKED_FACTCHECK}),
    DraftStatus.NEEDS_REVIEW: frozenset(
        {
            DraftStatus.APPROVED,
            DraftStatus.REJECTED,
            DraftStatus.BLOCKED_RIGHTS,
            DraftStatus.BLOCKED_FACTCHECK,
        }
    ),
    DraftStatus.BLOCKED_FACTCHECK: frozenset({DraftStatus.NEEDS_REVIEW}),
    DraftStatus.BLOCKED_RIGHTS: frozenset({DraftStatus.NEEDS_REVIEW}),
    DraftStatus.APPROVED: frozenset({DraftStatus.PUBLISHED, DraftStatus.RETIRED}),
    DraftStatus.PUBLISHED: frozenset({DraftStatus.RETIRED}),
    DraftStatus.REJECTED: frozenset(),
    DraftStatus.RETIRED: frozenset(),
}


class InvalidTransitionError(ValueError):
    pass


def check_transition(current: DraftStatus, new: DraftStatus) -> None:
    if new not in DRAFT_TRANSITIONS[current]:
        msg = f"draft transition {current.value} -> {new.value} is not allowed"
        raise InvalidTransitionError(msg)


class ClaimType(StrEnum):
    FACT = "FACT"
    OPINION_ATTRIBUTION = "OPINION_ATTRIBUTION"
    FORECAST = "FORECAST"
    ESTIMATE = "ESTIMATE"
    GUIDANCE = "GUIDANCE"
    ALLEGATION = "ALLEGATION"
    HYPOTHETICAL = "HYPOTHETICAL"


class ClaimImportance(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ClaimStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    AMBIGUOUS = "AMBIGUOUS"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    REMOVED = "REMOVED"


#: Claim states that keep a draft in ``blocked_factcheck`` (DATA_MODEL state machine).
BLOCKING_CLAIM_STATUSES: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.UNVERIFIED, ClaimStatus.CONTRADICTED, ClaimStatus.AMBIGUOUS}
)


class ExcerptSpan(_Entity):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    purpose: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> ExcerptSpan:
        if self.end_ms <= self.start_ms:
            msg = "excerpt span end_ms must be > start_ms"
            raise ValueError(msg)
        return self


class EditorialVersion(_Entity):
    """One generated draft for a candidate. Immutable text; only ``status`` moves."""

    id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    hook: str
    commentary_before: str
    commentary_after: str
    excerpt_plan: list[ExcerptSpan] = Field(default_factory=list)
    title: str
    title_options: list[str] = Field(default_factory=list)
    cta: str | None = None
    transformation_summary: str
    status: DraftStatus
    prompt_version: str = Field(min_length=1)
    model_alias: str = Field(min_length=1)
    llm_call_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


class Claim(_Entity):
    """A factual proposition introduced or materially restated by VME (D010)."""

    id: str = Field(min_length=1)
    editorial_version_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    claim_type: ClaimType
    importance: ClaimImportance = ClaimImportance.MEDIUM
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    reviewer_note: str | None = None
    origin: str = Field(min_length=1, description="which prompt produced it")
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING_CLAIM_STATUSES


class ReviewEvent(_Entity):
    """Audit log of a human or system decision about an object (never the state itself)."""

    id: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    reason_codes: list[str] = Field(default_factory=list)
    notes: str | None = None
    reviewer: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value


# --------------------------------------------------------------------- rendering


class TimelineKind(StrEnum):
    CARD = "card"
    SOURCE = "source"


class Caption(_Entity):
    """Burned-in caption, times relative to the timeline item start."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> Caption:
        if self.end_ms <= self.start_ms:
            msg = "caption end_ms must be > start_ms"
            raise ValueError(msg)
        return self


class TimelineItem(_Entity):
    kind: TimelineKind
    duration_ms: int = Field(gt=0)
    title: str = ""
    body: str = ""
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    captions: list[Caption] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> TimelineItem:
        if self.kind is TimelineKind.SOURCE:
            if self.source_start_ms is None or self.source_end_ms is None:
                msg = "source item needs source_start_ms and source_end_ms"
                raise ValueError(msg)
            if self.source_end_ms - self.source_start_ms != self.duration_ms:
                msg = "source item duration_ms must equal source_end_ms - source_start_ms"
                raise ValueError(msg)
        for c in self.captions:
            if c.end_ms > self.duration_ms:
                msg = f"caption {c.text!r} ends after the item ({c.end_ms} > {self.duration_ms})"
                raise ValueError(msg)
        return self


class CaptionConfig(_Entity):
    font_size: int = Field(default=58, ge=12)
    max_words: int = Field(default=5, ge=1)
    max_chars: int = Field(default=28, ge=8)
    max_duration_ms: int = Field(default=2500, ge=300)
    margin_bottom_px: int = Field(default=360, ge=0)


class OverlayConfig(_Entity):
    attribution_text: str | None = None
    hook_font_size: int = Field(default=68, ge=12)
    body_font_size: int = Field(default=44, ge=12)
    attribution_font_size: int = Field(default=30, ge=12)
    safe_margin_px: int = Field(default=96, ge=0)
    wrap_chars: int = Field(default=24, ge=8)
    background_color: str = "0x101418"
    text_color: str = "white"


class RenderPlan(_Entity):
    """Deterministic description of the vertical draft. Rendering reads nothing else."""

    id: str = Field(min_length=1)
    editorial_version_id: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    timeline: list[TimelineItem] = Field(min_length=1)
    caption_config: CaptionConfig
    overlay_config: OverlayConfig
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value

    @property
    def total_duration_ms(self) -> int:
        return sum(item.duration_ms for item in self.timeline)


class Render(_Entity):
    id: str = Field(min_length=1)
    render_plan_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        _require_aware(value, "created_at")
        return value
