from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import NOW, make_policy
from vme.domain.models import BasisType
from vme.rights.gate import Action, RightsBlockedError, check, require

ALL_ACTIONS = list(Action)


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_missing_policy_blocks_everything(action: Action) -> None:
    d = check(None, action, now=NOW)
    assert d.blocked and d.reason_code == "no_policy" and d.policy_id is None


@pytest.mark.parametrize("basis", [BasisType.UNKNOWN, BasisType.BLOCKED])
@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_unknown_and_blocked_fail_closed(basis: BasisType, action: Action) -> None:
    d = check(make_policy(basis), action, now=NOW)
    assert d.blocked and d.reason_code == "ingest_not_permitted"


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_expired_policy_blocks_regardless_of_flags(action: Action) -> None:
    p = make_policy(BasisType.OWNED, can_publish=True, expiry_at=NOW - timedelta(days=1))
    d = check(p, action, now=NOW)
    assert d.blocked and d.reason_code == "policy_expired"
    # and it was allowed one day earlier
    assert check(p, action, now=NOW - timedelta(days=2)).allowed


def test_owned_allows_ingest_clip_transform_but_not_publish_by_default() -> None:
    p = make_policy(BasisType.OWNED)
    assert check(p, Action.INGEST, now=NOW).allowed
    assert check(p, Action.CLIP, now=NOW).allowed
    assert check(p, Action.RENDER_TRANSFORM, now=NOW).allowed
    d = check(p, Action.PUBLISH, now=NOW)
    assert d.blocked and d.reason_code == "publish_not_permitted"


def test_transformative_review_required_never_publishes_and_flags_review() -> None:
    p = make_policy(BasisType.TRANSFORMATIVE_REVIEW_REQUIRED)
    d = check(p, Action.INGEST, now=NOW)
    assert d.allowed and d.review_required is True
    assert check(p, Action.PUBLISH, now=NOW).blocked


def test_each_action_maps_to_its_own_flag() -> None:
    p = make_policy(BasisType.EXPLICIT_LICENSE, can_extract_clip=False, can_transform=False)
    assert check(p, Action.INGEST, now=NOW).allowed
    assert check(p, Action.CLIP, now=NOW).reason_code == "clip_not_permitted"
    assert (
        check(p, Action.RENDER_TRANSFORM, now=NOW).reason_code == "render_transform_not_permitted"
    )


def test_ingest_is_prerequisite_for_every_action() -> None:
    p = make_policy(BasisType.OWNED, can_ingest=False)
    for action in ALL_ACTIONS:
        assert check(p, action, now=NOW).reason_code == "ingest_not_permitted"


def test_require_raises_with_decision() -> None:
    with pytest.raises(RightsBlockedError) as excinfo:
        require(make_policy(BasisType.UNKNOWN), Action.INGEST, now=NOW)
    assert excinfo.value.decision.reason_code == "ingest_not_permitted"
    assert require(make_policy(BasisType.OWNED), Action.INGEST, now=NOW).allowed
