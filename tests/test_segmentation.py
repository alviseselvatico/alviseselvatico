from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_stt import FakeSpeechToText, make_words
from vme.domain.models import BasisType, Transcript, TranscriptKind, TranscriptSegment
from vme.ingestion.register import register_local_media
from vme.rights.gate import RightsBlockedError
from vme.segmentation.segmenter import (
    SegmentationConfig,
    build_windows,
    created_by,
    segment_transcript,
    split_sentences,
)
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.storage.repositories import DuplicateRecordError
from vme.transcription.service import transcribe_media


def _transcript(sentences: list[str], *, pause_ms: int = 0, with_words: bool = True) -> Transcript:
    words = make_words(sentences, pause_ms=pause_ms)
    segments: list[TranscriptSegment] = []
    i = 0
    for s in sentences:
        n = len(s.split())
        chunk = words[i : i + n]
        segments.append(
            TranscriptSegment(
                start_ms=chunk[0].start_ms,
                end_ms=chunk[-1].end_ms,
                text=s,
                words=chunk if with_words else [],
            )
        )
        i += n
    return Transcript(
        id="trn_x",
        media_asset_id="med_x",
        kind=TranscriptKind.RAW,
        version=1,
        provider="fake",
        provider_version="0",
        model_alias="fake-1",
        language="en",
        raw_text=" ".join(sentences),
        segments=segments,
        created_at=NOW,
    )


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="min_ms <= target_ms"):
        SegmentationConfig(min_ms=10, target_ms=5, max_ms=20)
    assert created_by(SegmentationConfig()) == "segmenter:v0.2.0"


def test_sentences_split_on_punctuation_and_pauses() -> None:
    cfg = SegmentationConfig(sentence_pause_ms=700)
    words = make_words(["One two.", "Three four?", "five six"], pause_ms=0)
    assert [s.text for s in split_sentences(words, cfg)] == ["One two.", "Three four?", "five six"]
    # no punctuation, but a long pause between "b" and "c"
    words = make_words(["a b", "c d"], pause_ms=800)
    assert [s.text for s in split_sentences(words, cfg)] == ["a b", "c d"]
    words = make_words(["a b", "c d"], pause_ms=0)
    assert [s.text for s in split_sentences(words, cfg)] == ["a b c d"]


def test_windows_close_at_target_and_never_exceed_max_when_splittable() -> None:
    # 10 sentences x 5 words x 400 ms ≈ 2 s each
    sentences = [f"w{i}a w{i}b w{i}c w{i}d w{i}e." for i in range(10)]
    t = _transcript(sentences)
    cfg = SegmentationConfig(min_ms=3000, target_ms=6000, max_ms=8000)
    cands = segment_transcript(t, cfg, now=NOW)
    assert len(cands) >= 3
    for c in cands:
        assert c.duration_ms <= cfg.max_ms
        assert c.candidate_text.endswith(".")  # boundaries on sentence ends
    # non-overlapping, ordered, covering all words
    for a, b in pairwise(cands):
        assert a.end_ms <= b.start_ms
    assert " ".join(c.candidate_text for c in cands) == " ".join(sentences)


def test_short_transcript_yields_single_candidate_with_context() -> None:
    t = _transcript(["Ask not what your country can do for you."])
    (c,) = segment_transcript(t, SegmentationConfig(), now=NOW)
    assert c.start_ms == 0 and c.end_ms == t.duration_ms
    assert c.context_before == "" and c.context_after == ""
    assert c.created_by == "segmenter:v0.2.0" and c.speaker is None and c.topic is None


def test_context_before_and_after_are_neighbouring_words() -> None:
    sentences = [f"s{i}a s{i}b s{i}c s{i}d s{i}e." for i in range(6)]
    t = _transcript(sentences)
    cfg = SegmentationConfig(min_ms=1000, target_ms=2000, max_ms=2500, context_words=3)
    cands = segment_transcript(t, cfg, now=NOW)
    assert len(cands) >= 3
    mid = cands[1]
    assert mid.context_before.split() == cands[0].candidate_text.split()[-3:]
    assert mid.context_after.split() == cands[2].candidate_text.split()[:3]


def test_hard_pause_closes_window_after_min() -> None:
    sentences = [f"p{i}a p{i}b p{i}c." for i in range(4)]  # ~1.2 s each
    t = _transcript(sentences, pause_ms=2000)
    cfg = SegmentationConfig(min_ms=1000, target_ms=10_000, max_ms=20_000, hard_pause_ms=1500)
    cands = segment_transcript(t, cfg, now=NOW)
    assert [c.candidate_text for c in cands] == sentences


def test_falls_back_to_segments_without_word_timestamps() -> None:
    t = _transcript(["Alpha beta.", "Gamma delta."], with_words=False)
    cands = segment_transcript(t, SegmentationConfig(), now=NOW)
    assert len(cands) == 1 and cands[0].candidate_text == "Alpha beta. Gamma delta."


def test_deterministic() -> None:
    t = _transcript([f"d{i}a d{i}b d{i}c d{i}d." for i in range(12)])
    cfg = SegmentationConfig(min_ms=2000, target_ms=4000, max_ms=6000)
    a = segment_transcript(t, cfg, now=NOW)
    b = segment_transcript(t, cfg, now=NOW)
    assert [(c.start_ms, c.end_ms, c.candidate_text) for c in a] == [
        (c.start_ms, c.end_ms, c.candidate_text) for c in b
    ]
    assert build_windows(split_sentences(t.words(), cfg), cfg)


@requires_ffmpeg
def test_service_persists_once_and_is_gated(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio_wav, artifacts_dir=artifacts_dir, now=NOW)
    transcript = transcribe_media(
        store, asset.id, FakeSpeechToText(pause_ms=300), artifacts_dir=artifacts_dir, now=NOW
    )
    cfg = SegmentationConfig(min_ms=3000, target_ms=6000, max_ms=9000)
    cands = segment_and_store(store, transcript.id, cfg, now=NOW)
    assert len(cands) >= 2
    assert store.candidates.list(transcript.id) == cands
    assert store.candidates.get(cands[0].id) == cands[0]
    with pytest.raises(DuplicateRecordError, match="already exist"):
        segment_and_store(store, transcript.id, cfg, now=NOW)
    # a different segmenter version may add its own set
    other = SegmentationConfig(version="v0.1.0-test", min_ms=3000, target_ms=6000, max_ms=9000)
    more = segment_and_store(store, transcript.id, other, now=NOW)
    assert len(store.candidates.list(transcript.id)) == len(cands) + len(more)
    assert store.candidates.list(transcript.id, created_by="segmenter:v0.1.0-test") == more
    # rights re-checked: swap in an UNKNOWN policy
    unknown = make_policy(BasisType.UNKNOWN)
    store.policies.add(unknown)
    store.sources.attach_policy("S001", unknown.id)
    with pytest.raises(RightsBlockedError):
        segment_and_store(store, transcript.id, SegmentationConfig(version="v9"), now=NOW)
