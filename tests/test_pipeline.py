from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.config import load_settings
from vme.domain.models import BasisType, DraftStatus
from vme.pipeline import PipelineConfig, PipelineError, run_pipeline
from vme.ranking.features import PrefilterConfig
from vme.segmentation.segmenter import SegmentationConfig
from vme.storage.db import Store

SENTENCES = [f"Sentence {i} shares a concrete market observation worth quoting." for i in range(12)]


def _cfg() -> PipelineConfig:
    return PipelineConfig(
        top_k=2,
        segmentation=SegmentationConfig(min_ms=4000, target_ms=6000, max_ms=9000),
        prefilter=PrefilterConfig(min_ms=1000, max_ms=60000, min_words=5),
        finalists_k=2,
    )


def _settings(artifacts: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VME_ARTIFACTS_DIR", str(artifacts))
    return load_settings()


@requires_ffmpeg
def test_pipeline_runs_to_review_boundary(
    store: Store, audio_wav: Path, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    llm = FakeLlm(editorial_claims=1, extractor_claims=0)
    result = run_pipeline(
        store, source_id="S001", media_path=audio_wav, media_id=None,
        stt=FakeSpeechToText(SENTENCES, pause_ms=200), llm=llm,
        settings=_settings(artifacts_dir, monkeypatch), config=_cfg(), now=NOW,
    )  # fmt: skip
    assert result.ok and [s.stage for s in result.stages] == [
        "register",
        "transcribe",
        "segment",
        "rank",
        "editorial",
    ]
    assert all(s.status == "success" for s in result.stages)
    assert result.media_id and result.transcript_id and result.batch_id
    assert len(result.candidate_ids) > 2 and len(result.draft_ids) == 2
    for draft_id in result.draft_ids:
        assert store.editorial.get(draft_id).status is DraftStatus.BLOCKED_FACTCHECK
    # drafts belong to the two best-scored candidates
    runs = store.ranking.list_runs(result.batch_id)
    assert {store.editorial.get(d).candidate_id for d in result.draft_ids} == {
        r.candidate_id for r in runs[:2]
    }
    # rerun on the same media id skips register and produces transcript v2 + new drafts
    again = run_pipeline(
        store, source_id="S001", media_path=None, media_id=result.media_id,
        stt=FakeSpeechToText(SENTENCES, pause_ms=200), llm=llm,
        settings=_settings(artifacts_dir, monkeypatch), config=_cfg(), now=NOW,
    )  # fmt: skip
    assert again.stages[0].status == "skipped" and again.ok


@requires_ffmpeg
def test_pipeline_stops_visibly_on_rights_block(
    store: Store, audio_wav: Path, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.sources.add(make_source())
    policy = make_policy(BasisType.UNKNOWN)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    result = run_pipeline(
        store, source_id="S001", media_path=audio_wav, media_id=None,
        stt=FakeSpeechToText(SENTENCES), llm=FakeLlm(),
        settings=_settings(artifacts_dir, monkeypatch), config=_cfg(), now=NOW,
    )  # fmt: skip
    assert (
        not result.ok
        and result.stages[-1].stage == "register"
        and result.stages[-1].status == "blocked_policy"
    )
    assert store.media.list() == []


def test_pipeline_needs_path_or_media(
    store: Store, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PipelineError, match="--path"):
        run_pipeline(
            store,
            source_id="S001",
            media_path=None,
            media_id=None,
            stt=FakeSpeechToText(),
            llm=FakeLlm(),
            settings=_settings(artifacts_dir, monkeypatch),
        )
