"""D009 invariants live in the model and fail validation, never silent correction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tests.conftest import NOW, make_policy
from vme.domain.models import BasisType, RightsPolicy


def test_owned_policy_with_evidence_is_valid() -> None:
    p = make_policy(BasisType.OWNED)
    assert p.can_ingest and p.can_extract_clip and p.can_transform
    assert p.can_publish is False
    assert p.effective_basis(NOW) is BasisType.OWNED


@pytest.mark.parametrize("basis", [BasisType.BLOCKED, BasisType.UNKNOWN])
@pytest.mark.parametrize("flag", ["can_ingest", "can_extract_clip", "can_transform", "can_publish"])
def test_blocked_and_unknown_cannot_carry_any_permissive_flag(basis: BasisType, flag: str) -> None:
    with pytest.raises(ValidationError, match="D009"):
        make_policy(basis, **{flag: True})


@pytest.mark.parametrize("basis", [BasisType.BLOCKED, BasisType.UNKNOWN])
def test_blocked_and_unknown_valid_with_all_flags_false(basis: BasisType) -> None:
    p = make_policy(basis)
    assert not any((p.can_ingest, p.can_extract_clip, p.can_transform, p.can_publish))


def test_transformative_review_required_forbids_publish() -> None:
    with pytest.raises(ValidationError, match="forbids can_publish"):
        make_policy(BasisType.TRANSFORMATIVE_REVIEW_REQUIRED, can_publish=True)


def test_transformative_review_required_requires_review_flag() -> None:
    with pytest.raises(ValidationError, match="requires review_required"):
        make_policy(BasisType.TRANSFORMATIVE_REVIEW_REQUIRED, review_required=False)


def test_transformative_review_required_valid_shape() -> None:
    p = make_policy(BasisType.TRANSFORMATIVE_REVIEW_REQUIRED)
    assert p.review_required is True and p.can_publish is False


@pytest.mark.parametrize(
    "basis",
    [
        BasisType.OWNED,
        BasisType.EXPLICIT_LICENSE,
        BasisType.CREATOR_AUTHORIZATION,
        BasisType.PUBLIC_DOMAIN_VERIFIED,
        BasisType.TRANSFORMATIVE_REVIEW_REQUIRED,
    ],
)
@pytest.mark.parametrize("reference", [None, "", "   "])
def test_permissive_basis_requires_evidence_reference(
    basis: BasisType, reference: str | None
) -> None:
    with pytest.raises(ValidationError, match="requires basis_reference"):
        make_policy(basis, basis_reference=reference)


def test_attribution_text_required_when_attribution_required() -> None:
    with pytest.raises(ValidationError, match="attribution_text"):
        make_policy(BasisType.EXPLICIT_LICENSE, requires_attribution=True)


def test_expired_policy_evaluates_as_blocked() -> None:
    p = make_policy(BasisType.OWNED, expiry_at=NOW - timedelta(seconds=1))
    assert p.is_expired(NOW)
    assert p.effective_basis(NOW) is BasisType.BLOCKED
    # flags are untouched: the invariant is applied at evaluation time, not by mutation
    assert p.can_ingest is True


def test_future_expiry_is_not_expired() -> None:
    p = make_policy(BasisType.OWNED, expiry_at=NOW + timedelta(days=1))
    assert not p.is_expired(NOW)
    assert p.effective_basis(NOW) is BasisType.OWNED


def test_naive_expiry_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_policy(BasisType.OWNED, expiry_at=datetime(2030, 1, 1))  # noqa: DTZ001


def test_unknown_field_rejected_and_model_is_frozen() -> None:
    with pytest.raises(ValidationError):
        make_policy(BasisType.OWNED, safe_duration_seconds=10)
    p = make_policy(BasisType.OWNED)
    with pytest.raises(ValidationError):
        p.can_publish = True  # type: ignore[misc]


def test_invalid_basis_type_rejected() -> None:
    with pytest.raises(ValidationError):
        RightsPolicy(id="x", basis_type="FAIR_USE", created_at=datetime.now(tz=UTC))  # type: ignore[arg-type]
