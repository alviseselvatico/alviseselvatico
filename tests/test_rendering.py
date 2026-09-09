from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText, make_words
from vme.domain.models import (
    BasisType,
    Caption,
    CaptionConfig,
    DraftStatus,
    OverlayConfig,
    RenderPlan,
    TimelineItem,
    TimelineKind,
)
from vme.editorial.review import approve
from vme.editorial.service import generate_editorial
from vme.ingestion.register import register_local_media
from vme.rendering.ffmpeg import RenderError, RenderSettings, build_command, find_font, wrap_text
from vme.rendering.plan import (
    TEMPLATE_VERSION,
    PlanConfig,
    PlanError,
    build_render_plan,
    chunk_captions,
)
from vme.rendering.service import render_plan_to_file
from vme.rights.gate import RightsBlockedError
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

# 3 sentences x ~7 words x 400 ms ≈ 8.4 s: fits inside the 10 s fixtures
SENTENCES = [
    "Ask not what your country can do.",
    "Ask what you can do for it, honestly?",
    "That is a personal view, not guidance!",
]


def _approved_draft(
    store: Store, media: Path, artifacts: Path, *, attribution: bool = False
) -> str:
    store.sources.add(make_source())
    extra: dict[str, object] = {}
    if attribution:
        extra = {"requires_attribution": True, "attribution_text": "Source: operator archive"}
    policy = make_policy(BasisType.OWNED, **extra)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", media, artifacts_dir=artifacts, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=100), artifacts_dir=artifacts, now=NOW
    )
    cands = segment_and_store(
        store, t.id, SegmentationConfig(min_ms=2000, target_ms=20000, max_ms=30000), now=NOW
    )
    assert len(cands) == 1
    draft = generate_editorial(
        store, cands[0].id, FakeLlm(editorial_claims=0, extractor_claims=0), now=NOW
    ).draft
    assert approve(store, draft.id, reviewer="alice", now=NOW).draft.status is DraftStatus.APPROVED
    return draft.id


# ------------------------------------------------------------------ plan (pure)


def test_chunk_captions_respects_limits_and_sentence_ends() -> None:
    words = make_words(SENTENCES, word_ms=300, gap_ms=100)
    cfg = CaptionConfig(max_words=4, max_chars=24, max_duration_ms=2000)
    caps = chunk_captions(words, 0, words[-1].end_ms, cfg)
    assert caps and all(c.end_ms > c.start_ms for c in caps)
    assert all(len(c.text.split()) <= 4 for c in caps)
    texts = " ".join(c.text for c in caps)
    assert texts == " ".join(SENTENCES)
    # a caption never crosses a sentence end
    assert any(c.text.endswith("do.") for c in caps)
    # times are relative to the span start and ordered
    starts = [c.start_ms for c in caps]
    assert starts == sorted(starts) and starts[0] == 0
    # sub-span only keeps words fully inside
    sub = chunk_captions(words, 1000, 3000, cfg)
    assert all(w.start_ms >= 1000 for w in words if w.text in sub[0].text.split()) if sub else True


def test_wrap_text_and_timeline_validation() -> None:
    assert wrap_text("a b c d e f", 5) == "a b c\nd e f"
    with pytest.raises(ValueError, match="source_start_ms"):
        TimelineItem(kind=TimelineKind.SOURCE, duration_ms=1000)
    with pytest.raises(ValueError, match="must equal"):
        TimelineItem(
            kind=TimelineKind.SOURCE, duration_ms=1000, source_start_ms=0, source_end_ms=500
        )
    with pytest.raises(ValueError, match="ends after"):
        TimelineItem(
            kind=TimelineKind.CARD,
            duration_ms=1000,
            captions=[Caption(start_ms=0, end_ms=1500, text="x")],
        )


@requires_ffmpeg
def test_build_plan_from_approved_draft(
    store: Store, video_mp4_10s: Path, artifacts_dir: Path
) -> None:
    draft_id = _approved_draft(store, video_mp4_10s, artifacts_dir, attribution=True)
    plan = build_render_plan(
        store,
        draft_id,
        PlanConfig(hook_ms=1500, outro_ms=2000),
        artifacts_dir=artifacts_dir,
        now=NOW,
    )
    assert plan.template_version == TEMPLATE_VERSION and (plan.width, plan.height) == (1080, 1920)
    kinds = [i.kind for i in plan.timeline]
    assert kinds == [TimelineKind.CARD, TimelineKind.SOURCE, TimelineKind.CARD]
    hook, src, outro = plan.timeline
    assert hook.title.startswith("Why this moment") and hook.duration_ms >= 1500
    assert src.captions and src.source_start_ms == 0
    assert outro.title.startswith("Context") and outro.duration_ms >= 2000
    assert plan.overlay_config.attribution_text == "Source: operator archive"
    assert plan.total_duration_ms == hook.duration_ms + src.duration_ms + outro.duration_ms
    assert store.renders.get_plan(plan.id) == plan
    written = json.loads((artifacts_dir / "render_plans" / f"{plan.id}.json").read_text())
    assert written["id"] == plan.id and len(written["timeline"]) == 3
    # deterministic: a second plan has the same timeline
    again = build_render_plan(store, draft_id, PlanConfig(hook_ms=1500, outro_ms=2000), now=NOW)
    assert again.timeline == plan.timeline and again.id != plan.id
    assert [p.id for p in store.renders.list_plans(draft_id)] == [plan.id, again.id]


@requires_ffmpeg
def test_plan_requires_approved_draft_and_clip_rights(
    store: Store, video_mp4_10s: Path, artifacts_dir: Path
) -> None:
    draft_id = _approved_draft(store, video_mp4_10s, artifacts_dir)
    store.editorial.set_status(draft_id, DraftStatus.NEEDS_REVIEW)
    with pytest.raises(PlanError, match="only approved"):
        build_render_plan(store, draft_id, now=NOW)
    store.editorial.set_status(draft_id, DraftStatus.APPROVED)
    noclip = make_policy(BasisType.EXPLICIT_LICENSE, id="pol_noclip", can_extract_clip=False)
    store.policies.add(noclip)
    store.sources.attach_policy("S001", noclip.id)
    with pytest.raises(RightsBlockedError) as excinfo:
        build_render_plan(store, draft_id, now=NOW)
    assert excinfo.value.decision.reason_code == "clip_not_permitted"
    assert store.renders.list_plans(draft_id) == []


# ----------------------------------------------------------------- ffmpeg argv


def _plan() -> RenderPlan:
    captions = [
        Caption(start_ms=0, end_ms=900, text="one two"),
        Caption(start_ms=900, end_ms=2000, text="three"),
    ]
    return RenderPlan(
        id="rpl_t",
        editorial_version_id="e",
        template_version="t",
        width=1080,
        height=1920,
        timeline=[
            TimelineItem(
                kind=TimelineKind.CARD, duration_ms=1000, title="Hook: it's 'quoted'", body="b"
            ),
            TimelineItem(
                kind=TimelineKind.SOURCE,
                duration_ms=2000,
                source_start_ms=500,
                source_end_ms=2500,
                captions=captions,
            ),
        ],
        caption_config=CaptionConfig(),
        overlay_config=OverlayConfig(attribution_text="Src: a:b"),
    )


def test_build_command_is_argv_with_textfiles_not_inline_text(tmp_path: Path) -> None:
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    argv = build_command(
        _plan(),
        tmp_path / "in.mp4",
        tmp_path / "out.mp4",
        tmp_path / "work",
        RenderSettings(font_path=font),
        has_video=True,
        has_audio=True,
    )
    assert argv[0] == "ffmpeg" and argv[-1] == str(tmp_path / "out.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert (
        "quoted" not in graph and "one two" not in graph and "Src: a:b" not in graph
    )  # text only via files
    assert graph.count("textfile=") == 5  # title, body, 2 captions, attribution
    assert (
        "boxblur" in graph and "overlay=(W-w)/2:(H-h)/2" in graph and "concat=n=2:v=1:a=1" in graph
    )
    assert "enable='between(t,0.000,0.900)'" in graph
    files = sorted((tmp_path / "work").glob("text_*.txt"))
    assert next(f.read_text() for f in files) == "Hook: it's 'quoted'"
    assert "-ss" in argv and argv[argv.index("-ss") + 1] == "0.500"
    assert all(isinstance(a, str) for a in argv) and not any(
        ";" in a and a.startswith("ffmpeg") for a in argv
    )
    # audio-only sources get a colour background input instead of the blur graph
    argv2 = build_command(
        _plan(),
        tmp_path / "in.wav",
        tmp_path / "o.mp4",
        tmp_path / "w2",
        RenderSettings(font_path=font),
        has_video=False,
        has_audio=True,
    )
    graph2 = argv2[argv2.index("-filter_complex") + 1]
    assert "boxblur" not in graph2 and "color=c=" in " ".join(argv2)


def test_find_font_errors_are_visible(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="VME_RENDER_FONT"):
        find_font(tmp_path / "missing.ttf")


# ------------------------------------------------------------ real render (ffmpeg)


@requires_ffmpeg
@pytest.mark.parametrize("fixture_name", ["video_mp4_10s", "audio_wav_10s"])
def test_render_end_to_end(
    store: Store, artifacts_dir: Path, request: pytest.FixtureRequest, fixture_name: str
) -> None:
    media: Path = request.getfixturevalue(fixture_name)
    draft_id = _approved_draft(store, media, artifacts_dir, attribution=True)
    plan = build_render_plan(
        store,
        draft_id,
        PlanConfig(hook_ms=1000, outro_ms=1000),
        artifacts_dir=artifacts_dir,
        now=NOW,
    )
    settings = RenderSettings(font_path=find_font(), preset="ultrafast", crf=30)
    render = render_plan_to_file(store, plan.id, settings, artifacts_dir=artifacts_dir, now=NOW)
    out = Path(render.file_path)
    assert out.is_file() and out.parent == (artifacts_dir / "renders").resolve()
    assert render.validation["passed"] and render.validation["problems"] == []
    assert (render.validation["width"], render.validation["height"]) == (1080, 1920)
    assert render.validation["video_codec"] == "h264" and render.validation["audio_codec"] == "aac"
    assert abs(render.duration_ms - plan.total_duration_ms) <= 1200
    assert store.renders.get_render(render.id) == render
    assert store.renders.list_renders(plan.id) == [render]
    assert not (artifacts_dir / "tmp" / render.id).exists()  # workdir cleaned


@requires_ffmpeg
def test_render_refuses_when_draft_no_longer_approved_or_source_changed(
    store: Store, video_mp4_10s: Path, artifacts_dir: Path
) -> None:
    draft_id = _approved_draft(store, video_mp4_10s, artifacts_dir)
    plan = build_render_plan(store, draft_id, now=NOW)
    settings = RenderSettings(font_path=find_font(), preset="ultrafast")
    store.editorial.set_status(draft_id, DraftStatus.RETIRED)
    with pytest.raises(RenderError, match="needs an approved draft"):
        render_plan_to_file(store, plan.id, settings, artifacts_dir=artifacts_dir, now=NOW)
    store.editorial.set_status(draft_id, DraftStatus.APPROVED)
    video_mp4_10s.write_bytes(video_mp4_10s.read_bytes() + b"\x00")
    with pytest.raises(RenderError, match="no longer matches"):
        render_plan_to_file(store, plan.id, settings, artifacts_dir=artifacts_dir, now=NOW)
    assert store.renders.list_renders(plan.id) == []


def test_card_duration_scales_with_words() -> None:
    from vme.rendering.plan import card_duration_ms

    assert card_duration_ms("short", minimum_ms=2500) == 2500
    forty = " ".join(["word"] * 40)
    assert 2500 < card_duration_ms(forty, minimum_ms=2500) <= 9000
    assert card_duration_ms(" ".join(["w"] * 500), minimum_ms=2500) == 9000


def test_editorial_schema_enforces_card_sizes() -> None:
    from pydantic import ValidationError

    from vme.editorial.schemas import EditorialDraft

    base = {
        "hook": "h",
        "commentary_before": "b",
        "source_excerpt_plan": [{"start_ms": 0, "end_ms": 10, "purpose": "p"}],
        "commentary_after": "a",
        "title_options": ["t"],
        "cta": None,
        "generated_claims": [],
        "transformation_summary": "s",
    }
    EditorialDraft.model_validate(base)
    with pytest.raises(ValidationError, match="commentary_before is"):
        EditorialDraft.model_validate({**base, "commentary_before": "x" * 281})
    with pytest.raises(ValidationError, match="hook is"):
        EditorialDraft.model_validate({**base, "hook": "x" * 111})
