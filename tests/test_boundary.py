from __future__ import annotations

from pathlib import Path

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_stt import FakeSpeechToText, make_words
from vme.domain.models import BasisType, Word
from vme.ingestion.register import register_local_media
from vme.segmentation.boundary import BoundaryConfig, refine_span
from vme.segmentation.refine import refine_candidates
from vme.segmentation.segmenter import SegmentationConfig, segment_transcript
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

# word_ms=300 gap 100 -> each word occupies 400 ms
S = [
    "This is a LibriVox recording.",  # 5 words: 0-1900
    "First sentence about banks is here.",  # 6 words: 2000-4300
    "Second sentence explains deposits fully.",  # 5 words: 4400-6300
    "Third sentence ends the argument now.",  # 6 words: 6400-8700
    "End of section two.",  # 4 words: 8800-10300
]
W: list[Word] = make_words(S)
CFG = BoundaryConfig(min_ms=1000, max_ms=20000, max_extend_ms=2000)


def _span(text_from: str, text_to: str) -> tuple[int, int]:
    words = [w.text for w in W]
    return W[words.index(text_from)].start_ms, W[words.index(text_to)].end_ms


def test_no_change_when_aligned() -> None:
    s, e = _span("First", "here.")
    edit = refine_span(W, s, e, CFG)
    assert (edit.start_ms, edit.end_ms) == (s, e) and not edit.changed


def test_start_fragment_is_extended_back_when_cheap() -> None:
    s, e = _span("about", "fully.")  # starts mid-sentence, 800 ms after the sentence start
    edit = refine_span(W, s, e, CFG)
    assert edit.start_ms == _span("First", "here.")[0] and edit.reasons == ["extended_start"]


def test_start_fragment_is_trimmed_when_extension_too_long() -> None:
    s, e = _span("about", "now.")
    edit = refine_span(W, s, e, BoundaryConfig(min_ms=1000, max_ms=20000, max_extend_ms=500))
    assert edit.start_ms == _span("Second", "fully.")[0] and edit.reasons == [
        "trimmed_start_fragment"
    ]


def test_trim_only_mode_never_extends() -> None:
    s, e = _span("about", "ends")  # both edges mid-sentence
    edit = refine_span(W, s, e, BoundaryConfig(min_ms=1000, max_ms=20000, allow_extend=False))
    assert edit.start_ms == _span("Second", "fully.")[0]
    assert edit.end_ms == _span("Second", "fully.")[1]
    assert edit.reasons == ["trimmed_start_fragment", "trimmed_end_fragment"]


def test_end_fragment_is_extended_forward() -> None:
    s, e = _span("First", "explains")
    edit = refine_span(W, s, e, CFG)
    assert edit.end_ms == _span("Second", "fully.")[1] and edit.reasons == ["extended_end"]


def test_boilerplate_and_section_marker_dropped_at_edges() -> None:
    s, e = _span("This", "two.")  # whole thing: LibriVox intro ... "End of section two."
    edit = refine_span(W, s, e, CFG)
    assert edit.start_ms == _span("First", "here.")[0]
    assert edit.end_ms == _span("Third", "now.")[1]
    assert edit.reasons == ["dropped_boilerplate_start", "dropped_boilerplate_end"]
    # a "Part four ..." marker at the end is dropped too
    words = make_words(["Good sentence here.", "Part four the relation of the old world."])
    edit = refine_span(words, 0, words[-1].end_ms, CFG)
    assert edit.end_ms == words[2].end_ms and edit.reasons == ["dropped_boilerplate_end"]


def test_min_duration_guard_keeps_span_when_trim_would_be_too_short() -> None:
    s, e = _span("about", "here.")  # 4 words, 1500 ms
    edit = refine_span(W, s, e, BoundaryConfig(min_ms=5000, max_ms=20000, allow_extend=False))
    assert (edit.start_ms, edit.end_ms) == (s, e) and not edit.changed


def test_is_pure_and_deterministic() -> None:
    s, e = _span("about", "explains")
    assert refine_span(W, s, e, CFG) == refine_span(W, s, e, CFG)


def test_segmenter_v02_trims_edge_boilerplate() -> None:
    from tests.test_segmentation import _transcript

    t = _transcript(S)
    cfg = SegmentationConfig(min_ms=1000, target_ms=30000, max_ms=60000)
    (c,) = segment_transcript(t, cfg, now=NOW)
    assert c.created_by == "segmenter:v0.2.0"
    assert c.candidate_text.startswith("First sentence") and c.candidate_text.endswith(
        "argument now."
    )
    (raw,) = segment_transcript(
        t, SegmentationConfig(min_ms=1000, target_ms=30000, max_ms=60000, refine=False), now=NOW
    )
    assert raw.candidate_text.startswith("This is a LibriVox")


@requires_ffmpeg
def test_refine_service_creates_derived_candidates_once(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio_wav, artifacts_dir=artifacts_dir, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(S, pause_ms=0), artifacts_dir=artifacts_dir, now=NOW
    )
    originals = segment_and_store(
        store,
        t.id,
        SegmentationConfig(min_ms=1000, target_ms=30000, max_ms=60000, refine=False),
        now=NOW,
    )
    assert len(originals) == 1 and originals[0].candidate_text.startswith("This is a LibriVox")
    res = refine_candidates(store, t.id, BoundaryConfig(min_ms=1000, max_ms=60000), now=NOW)
    assert len(res.refined) == 1 and res.unchanged == 0
    new = res.refined[0]
    assert new.derived_from_id == originals[0].id and new.created_by == "boundary_editor:v0.1.0"
    assert new.candidate_text.startswith("First sentence") and store.candidates.get(new.id) == new
    assert res.edits[originals[0].id].reasons == [
        "dropped_boilerplate_start",
        "dropped_boilerplate_end",
    ]
    again = refine_candidates(store, t.id, BoundaryConfig(min_ms=1000, max_ms=60000), now=NOW)
    assert again.refined == [] and again.unchanged == 0  # original already refined, derived skipped
    assert len(store.candidates.list(t.id)) == 2
