from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import NOW
from vme.domain.models import Candidate, Transcript, TranscriptKind, TranscriptSegment, Word


def _segment() -> TranscriptSegment:
    return TranscriptSegment(
        start_ms=0,
        end_ms=1000,
        text="Hello world.",
        words=[
            Word(text="Hello", start_ms=0, end_ms=400),
            Word(text="world.", start_ms=500, end_ms=1000),
        ],
    )


def test_raw_transcript_shape() -> None:
    t = Transcript(
        id="trn_1",
        media_asset_id="med_1",
        kind=TranscriptKind.RAW,
        version=1,
        provider="fake",
        provider_version="0",
        model_alias="tiny",
        language="en",
        raw_text="Hello world.",
        segments=[_segment()],
        created_at=NOW,
    )
    assert t.duration_ms == 1000 and [w.text for w in t.words()] == ["Hello", "world."]


def test_raw_cannot_have_parent_and_derived_needs_one() -> None:
    base = {
        "id": "t",
        "media_asset_id": "m",
        "version": 1,
        "provider": "p",
        "provider_version": "0",
        "model_alias": "a",
        "raw_text": "",
        "segments": [],
        "created_at": NOW,
    }
    with pytest.raises(ValidationError, match="RAW transcript cannot"):
        Transcript(kind=TranscriptKind.RAW, derived_from_id="parent", **base)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="requires derived_from_id"):
        Transcript(kind=TranscriptKind.CORRECTED, **base)  # type: ignore[arg-type]


def test_word_and_segment_ordering() -> None:
    with pytest.raises(ValidationError):
        Word(text="x", start_ms=10, end_ms=5)
    with pytest.raises(ValidationError):
        TranscriptSegment(start_ms=10, end_ms=5, text="x")


def test_candidate_requires_positive_span_and_text() -> None:
    with pytest.raises(ValidationError):
        Candidate(
            id="c", transcript_id="t", start_ms=5, end_ms=5, candidate_text="x", created_by="me"
        )
    with pytest.raises(ValidationError):
        Candidate(
            id="c", transcript_id="t", start_ms=0, end_ms=5, candidate_text="", created_by="me"
        )
    c = Candidate(
        id="c", transcript_id="t", start_ms=0, end_ms=5, candidate_text="x", created_by="me"
    )
    assert c.duration_ms == 5 and c.topic is None and c.speaker is None
