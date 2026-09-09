"""One-shot orchestration of the automated stages, up to the human review boundary.

``register -> transcribe -> segment -> rank -> editorial (top-k)``. Every stage ends in a
visible state (ARCHITECTURE §7) and is logged under the invocation's correlation id.
Review, claim resolution and rendering stay explicit human-triggered commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from vme.config import Settings
from vme.domain.models import utc_now
from vme.editorial.service import EditorialConfig, EditorialError, generate_editorial
from vme.ingestion.register import register_local_media
from vme.llm.base import StructuredLlm
from vme.logs import get_logger
from vme.ranking.features import PrefilterConfig
from vme.ranking.service import RankingConfig, RankingError, rank_transcript
from vme.ranking.weights import load_weights
from vme.rights.gate import RightsBlockedError
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.storage.repositories import DuplicateRecordError
from vme.transcription.base import SpeechToText
from vme.transcription.service import transcribe_media

log = get_logger("pipeline")


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    top_k: int = 3
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    prefilter: PrefilterConfig = field(default_factory=PrefilterConfig)
    finalists_k: int = 5
    max_tokens: int = 8192
    vertical: str = "general"
    audience: str = "general English-speaking short-form viewers"
    target_ms: int = 45_000
    weights_path: Path | None = None


@dataclass(slots=True)
class StageResult:
    stage: str
    status: str  # success | blocked_policy | permanent_failure | skipped
    detail: str = ""
    ids: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineResult:
    stages: list[StageResult]
    media_id: str | None = None
    transcript_id: str | None = None
    candidate_ids: list[str] = field(default_factory=list)
    batch_id: str | None = None
    draft_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.status in {"success", "skipped"} for s in self.stages)


def _record(result: PipelineResult, stage: StageResult) -> None:
    result.stages.append(stage)
    log.info(
        "pipeline_stage",
        extra={"stage": stage.stage, "status": stage.status, "detail": stage.detail, **stage.ids},
    )


def run_pipeline(
    store: Store,
    *,
    source_id: str,
    media_path: Path | None,
    media_id: str | None,
    stt: SpeechToText,
    llm: StructuredLlm,
    settings: Settings,
    config: PipelineConfig | None = None,
    now: datetime | None = None,
) -> PipelineResult:
    """Run the automated stages. Stops at the first blocked/failed stage."""
    config = config or PipelineConfig()
    now = now or utc_now()
    result = PipelineResult(stages=[])
    artifacts = settings.artifacts_dir

    # 1. register (or reuse)
    if media_id is None:
        if media_path is None:
            msg = "pipeline needs --path (file to register) or --media (registered asset)"
            raise PipelineError(msg)
        try:
            asset = register_local_media(
                store,
                source_id,
                media_path,
                artifacts_dir=artifacts,
                ffprobe_bin=settings.ffprobe_bin,
                now=now,
            )
        except RightsBlockedError as exc:
            _record(result, StageResult("register", "blocked_policy", str(exc)))
            return result
        except DuplicateRecordError as exc:
            _record(result, StageResult("register", "permanent_failure", str(exc)))
            return result
        media_id = asset.id
        _record(result, StageResult("register", "success", ids={"media_id": media_id}))
    else:
        store.media.get(media_id)
        _record(
            result,
            StageResult("register", "skipped", "existing media asset", {"media_id": media_id}),
        )
    result.media_id = media_id

    # 2. transcribe
    try:
        transcript = transcribe_media(store, media_id, stt, artifacts_dir=artifacts, now=now)
    except RightsBlockedError as exc:
        _record(result, StageResult("transcribe", "blocked_policy", str(exc)))
        return result
    except RuntimeError as exc:
        _record(result, StageResult("transcribe", "permanent_failure", str(exc)))
        return result
    result.transcript_id = transcript.id
    _record(
        result,
        StageResult(
            "transcribe",
            "success",
            f"{len(transcript.words())} words, {transcript.duration_ms} ms",
            {"transcript_id": transcript.id},
        ),
    )

    # 3. segment
    try:
        candidates = segment_and_store(store, transcript.id, config.segmentation, now=now)
    except RightsBlockedError as exc:
        _record(result, StageResult("segment", "blocked_policy", str(exc)))
        return result
    except DuplicateRecordError as exc:
        _record(result, StageResult("segment", "permanent_failure", str(exc)))
        return result
    result.candidate_ids = [c.id for c in candidates]
    _record(
        result,
        StageResult(
            "segment", "success", f"{len(candidates)} candidates", {"count": len(candidates)}
        ),
    )
    if not candidates:
        _record(result, StageResult("rank", "permanent_failure", "no candidates to rank"))
        return result

    # 4. rank
    try:
        ranking = rank_transcript(
            store,
            transcript.id,
            llm,
            load_weights(config.weights_path),
            RankingConfig(
                finalists_k=config.finalists_k,
                max_tokens=config.max_tokens,
                vertical=config.vertical,
                audience=config.audience,
                prefilter=config.prefilter,
            ),
            now=now,
        )
    except RightsBlockedError as exc:
        _record(result, StageResult("rank", "blocked_policy", str(exc)))
        return result
    except (RankingError, RuntimeError) as exc:
        _record(result, StageResult("rank", "permanent_failure", str(exc)))
        return result
    result.batch_id = ranking.batch.id
    _record(
        result,
        StageResult(
            "rank",
            "success",
            f"prefiltered={ranking.prefiltered} cheap={ranking.cheap_scored} "
            f"strong={ranking.strong_scored}",
            {
                "batch_id": ranking.batch.id,
                "top_scores": [r.final_score for r in ranking.runs[: config.top_k]],
            },
        ),
    )

    # 5. editorial for the top-k scored candidates
    top = [r for r in ranking.runs if r.final_score > 0][: config.top_k]
    if not top:
        _record(
            result, StageResult("editorial", "permanent_failure", "no candidate scored above zero")
        )
        return result
    edit_cfg = EditorialConfig(
        vertical=config.vertical,
        audience=config.audience,
        target_ms=config.target_ms,
        max_tokens=config.max_tokens,
    )
    failures: list[str] = []
    for run in top:
        try:
            draft = generate_editorial(store, run.candidate_id, llm, edit_cfg, now=now).draft
            result.draft_ids.append(draft.id)
        except RightsBlockedError as exc:
            _record(result, StageResult("editorial", "blocked_policy", str(exc)))
            return result
        except EditorialError as exc:
            failures.append(f"{run.candidate_id}: {exc}")
    status = "success" if result.draft_ids and not failures else "permanent_failure"
    _record(
        result,
        StageResult(
            "editorial",
            status,
            f"{len(result.draft_ids)} drafts"
            + (f"; failures: {'; '.join(failures)}" if failures else ""),
            {"draft_ids": result.draft_ids},
        ),
    )
    return result
