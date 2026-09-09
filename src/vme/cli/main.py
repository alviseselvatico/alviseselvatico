"""``vme`` CLI: ``source``, ``policy``, ``media``, ``transcribe``, ``transcript``,
``segment``, ``candidate``, ``rank``, ``ranking``, ``llm``, ``editorial``, ``claim``,
``review``, ``render``, ``pipeline``, ``db migrate``.

Every invocation gets a correlation id, logs JSON to stderr and prints one JSON document
to stdout. Exit codes: 0 ok, 1 error, 2 usage, 3 blocked by the rights gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from vme import __version__
from vme.config import Settings, load_settings
from vme.domain.models import (
    BasisType,
    ClaimStatus,
    InvalidTransitionError,
    RightsPolicy,
    Source,
    SourceKind,
    new_id,
    utc_now,
)
from vme.editorial.review import approve, recheck, reject, resolve_claim
from vme.editorial.service import EditorialConfig, generate_editorial
from vme.ingestion.probe import ProbeError
from vme.ingestion.register import IngestionError, register_local_media
from vme.llm.factory import build_llm
from vme.logs import configure_logging, display_path, get_logger, new_correlation_id
from vme.pipeline import PipelineConfig, run_pipeline
from vme.ranking.features import PrefilterConfig
from vme.ranking.service import RankingConfig, rank_transcript
from vme.ranking.weights import load_weights
from vme.rendering.ffmpeg import RenderSettings, find_font
from vme.rendering.plan import PlanConfig, build_render_plan
from vme.rendering.service import render_plan_to_file
from vme.rights.gate import Action, RightsBlockedError, check
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store, applied_versions, migrate
from vme.storage.repositories import DuplicateRecordError, NotFoundError
from vme.transcription.factory import build_transcriber
from vme.transcription.service import transcribe_media

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

log = get_logger("cli")


class CliError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ----------------------------------------------------------------------------- output


def _dump(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    if isinstance(obj, dict):
        return {str(k): _dump(v) for k, v in obj.items()}
    return obj


def _emit(payload: Any, out: Any) -> None:
    json.dump(_dump(payload), out, indent=2, ensure_ascii=False, default=str)
    out.write("\n")


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        msg = f"invalid ISO-8601 datetime: {value!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# --------------------------------------------------------------------------- commands


def cmd_db_migrate(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    applied = migrate(store.conn)
    return {"applied": applied, "current": applied_versions(store.conn)}


def cmd_source_add(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    source = Source(
        id=args.id or new_id("src"),
        kind=SourceKind(args.kind.upper()),
        canonical_uri=args.uri,
        publisher=args.publisher,
        title=args.title,
    )
    with store.transaction():
        store.sources.add(source)
    log.info("source_added", extra={"source_id": source.id, "kind": source.kind.value})
    return source


def cmd_source_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    source = store.sources.get(args.id)
    policy = store.policies.get(source.rights_policy_id) if source.rights_policy_id else None
    now = utc_now()
    gate = {a.value: check(policy, a, now=now).allowed for a in Action}
    return {
        "source": source,
        "rights_policy": policy,
        "gate": gate,
        "media": store.media.list(source.id),
    }


def cmd_source_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.sources.list()


def cmd_policy_add(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    store.sources.get(args.source)  # NotFoundError if missing
    policy = RightsPolicy(
        id=args.id or new_id("pol"),
        basis_type=BasisType(args.basis.upper()),
        basis_reference=args.reference,
        can_ingest=args.can_ingest,
        can_extract_clip=args.can_extract_clip,
        can_transform=args.can_transform,
        can_publish=args.can_publish,
        requires_attribution=args.requires_attribution,
        attribution_text=args.attribution_text,
        territory_notes=args.territory_notes,
        expiry_at=args.expiry,
        review_required=args.review_required,
        notes=args.notes,
    )
    with store.transaction():
        store.policies.add(policy)
        source = store.sources.attach_policy(args.source, policy.id)
    log.info(
        "policy_attached",
        extra={
            "source_id": source.id,
            "policy_id": policy.id,
            "basis_type": policy.basis_type.value,
            "can_ingest": policy.can_ingest,
            "can_publish": policy.can_publish,
        },
    )
    return {"policy": policy, "source": source}


def cmd_policy_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.policies.get(args.id)


def cmd_media_register(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    return register_local_media(
        store,
        args.source,
        Path(args.path),
        artifacts_dir=settings.artifacts_dir,
        ffprobe_bin=settings.ffprobe_bin,
    )


def cmd_media_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.media.get(args.id)


def cmd_media_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.media.list(args.source)


def cmd_transcribe(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    stt = build_transcriber(settings)
    transcript = transcribe_media(store, args.media, stt, artifacts_dir=settings.artifacts_dir)
    return _transcript_view(transcript, full=args.full)


def _transcript_view(transcript: Any, *, full: bool) -> Any:
    if full:
        return transcript
    data = transcript.model_dump(mode="json", exclude={"segments"})
    data["segments"] = len(transcript.segments)
    data["words"] = len(transcript.words())
    data["duration_ms"] = transcript.duration_ms
    return data


def cmd_transcript_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return _transcript_view(store.transcripts.get(args.id), full=args.full)


def cmd_transcript_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return [_transcript_view(t, full=False) for t in store.transcripts.list(args.media)]


def cmd_segment(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    kwargs: dict[str, Any] = {}
    for name in ("min_ms", "target_ms", "max_ms", "sentence_pause_ms", "hard_pause_ms"):
        value = getattr(args, name)
        if value is not None:
            kwargs[name] = value
    config = SegmentationConfig(**kwargs)
    return segment_and_store(store, args.transcript, config)


def cmd_candidate_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.candidates.get(args.id)


def cmd_candidate_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.candidates.list(args.transcript, created_by=args.created_by)


def cmd_rank(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    llm = build_llm(settings)
    weights = load_weights(Path(args.weights) if args.weights else settings.ranking_weights_path)
    defaults = PrefilterConfig()
    prefilter = PrefilterConfig(
        min_ms=args.min_ms if args.min_ms is not None else defaults.min_ms,
        max_ms=args.max_ms if args.max_ms is not None else defaults.max_ms,
        min_words=args.min_words if args.min_words is not None else defaults.min_words,
    )
    config = RankingConfig(
        finalists_k=args.finalists or settings.ranking_finalists,
        max_tokens=settings.llm_max_tokens,
        vertical=args.vertical or settings.vertical,
        audience=args.audience or settings.audience,
        prefilter=prefilter,
        created_by=args.created_by,
    )
    result = rank_transcript(store, args.transcript, llm, weights, config)
    return {
        "batch": result.batch,
        "prefiltered": result.prefiltered,
        "cheap_scored": result.cheap_scored,
        "strong_scored": result.strong_scored,
        "llm_calls": [c.id for c in result.llm_calls],
        "runs": result.runs,
    }


def cmd_ranking_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    batch = store.ranking.get_batch(args.id)
    return {"batch": batch, "runs": store.ranking.list_runs(batch.id)}


def cmd_ranking_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.ranking.list_batches(args.transcript)


def cmd_llm_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.llm_calls.get(args.id)


def cmd_llm_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return [
        c.model_dump(mode="json", exclude={"response"}) for c in store.llm_calls.list(args.purpose)
    ]


def _reviewer(args: argparse.Namespace, settings: Settings) -> str:
    return args.reviewer or settings.reviewer


def _draft_view(store: Store, draft_id: str) -> Any:
    draft = store.editorial.get(draft_id)
    return {
        "draft": draft,
        "claims": store.claims.list(draft.id),
        "events": store.reviews.list("editorial_version", draft.id),
    }


def cmd_editorial_generate(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    llm = build_llm(settings)
    config = EditorialConfig(
        vertical=args.vertical or settings.vertical,
        audience=args.audience or settings.audience,
        target_ms=args.target_ms or settings.target_clip_ms,
        max_tokens=settings.llm_max_tokens,
        use_ranking=not args.no_ranking,
    )
    result = generate_editorial(store, args.candidate, llm, config)
    return {
        "draft": result.draft,
        "claims": result.claims,
        "llm_calls": [c.id for c in result.llm_calls],
    }


def cmd_editorial_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return _draft_view(store, args.id)


def cmd_editorial_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return [
        d.model_dump(mode="json", exclude={"excerpt_plan", "title_options"})
        for d in store.editorial.list(args.candidate)
    ]


def cmd_claim_resolve(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    status = ClaimStatus(args.status.upper().replace("-", "_"))
    claim, event, draft = resolve_claim(
        store, args.id, status, reviewer=_reviewer(args, settings), notes=args.note
    )
    return {"claim": claim, "event": event, "draft_status": draft.status.value}


def cmd_claim_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.claims.list(args.draft)


def cmd_review_approve(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    outcome = approve(store, args.id, reviewer=_reviewer(args, settings), notes=args.note)
    return {"draft": outcome.draft, "event": outcome.event}


def cmd_review_reject(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    outcome = reject(
        store,
        args.id,
        reviewer=_reviewer(args, settings),
        reason_codes=[r for part in args.reason for r in part.split(",")],
        notes=args.note,
    )
    return {"draft": outcome.draft, "event": outcome.event}


def cmd_review_recheck(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    outcome = recheck(store, args.id, reviewer=_reviewer(args, settings))
    if outcome is None:
        return {"draft": store.editorial.get(args.id), "event": None, "changed": False}
    return {"draft": outcome.draft, "event": outcome.event, "changed": True}


def cmd_review_events(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.reviews.list(args.object_type, args.object_id)


def cmd_render_plan(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    config = PlanConfig(
        width=settings.render_width,
        height=settings.render_height,
        hook_ms=args.hook_ms or settings.render_hook_ms,
        outro_ms=args.outro_ms or settings.render_outro_ms,
    )
    return build_render_plan(store, args.draft, config, artifacts_dir=settings.artifacts_dir)


def cmd_render_run(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    rs = RenderSettings(
        ffmpeg_bin=settings.ffmpeg_bin,
        ffprobe_bin=settings.ffprobe_bin,
        font_path=find_font(settings.render_font or None),
        preset=args.preset or settings.render_preset,
        crf=settings.render_crf,
        fps=settings.render_fps,
        timeout_s=settings.render_timeout_s,
    )
    return render_plan_to_file(store, args.plan, rs, artifacts_dir=settings.artifacts_dir)


def cmd_render_show(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    render = store.renders.get_render(args.id)
    return {"render": render, "plan": store.renders.get_plan(render.render_plan_id)}


def cmd_render_plans(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return [
        p.model_dump(mode="json", exclude={"timeline"})
        | {"items": len(p.timeline), "total_duration_ms": p.total_duration_ms}
        for p in store.renders.list_plans(args.draft)
    ]


def cmd_render_list(args: argparse.Namespace, store: Store, _: Settings) -> Any:
    return store.renders.list_renders(args.plan)


def cmd_pipeline_run(args: argparse.Namespace, store: Store, settings: Settings) -> Any:
    seg = SegmentationConfig(
        **{
            k: v
            for k, v in {
                "min_ms": args.seg_min_ms,
                "target_ms": args.seg_target_ms,
                "max_ms": args.seg_max_ms,
            }.items()
            if v is not None
        }
    )
    defaults = PrefilterConfig()
    prefilter = PrefilterConfig(
        min_ms=args.min_ms if args.min_ms is not None else defaults.min_ms,
        max_ms=args.max_ms if args.max_ms is not None else defaults.max_ms,
        min_words=args.min_words if args.min_words is not None else defaults.min_words,
    )
    config = PipelineConfig(
        top_k=args.top,
        segmentation=seg,
        prefilter=prefilter,
        finalists_k=args.finalists or settings.ranking_finalists,
        max_tokens=settings.llm_max_tokens,
        vertical=args.vertical or settings.vertical,
        audience=args.audience or settings.audience,
        target_ms=args.target_ms or settings.target_clip_ms,
        weights_path=settings.ranking_weights_path,
    )
    result = run_pipeline(
        store,
        source_id=args.source,
        media_path=Path(args.path) if args.path else None,
        media_id=args.media,
        stt=build_transcriber(settings),
        llm=build_llm(settings),
        settings=settings,
        config=config,
    )
    payload: dict[str, Any] = {
        "ok": result.ok,
        "media_id": result.media_id,
        "transcript_id": result.transcript_id,
        "candidates": len(result.candidate_ids),
        "batch_id": result.batch_id,
        "draft_ids": result.draft_ids,
        "stages": [
            {"stage": s.stage, "status": s.status, "detail": s.detail, **s.ids}
            for s in result.stages
        ],
        "next": (
            "review drafts: vme editorial show <draft_id>; vme claim resolve ...; "
            "vme review approve ..."
        ),
    }
    return payload


Handler = Callable[[argparse.Namespace, Store, Settings], Any]


# ----------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vme", description="Vertical Media Engine (Phase 0)")
    parser.add_argument("--version", action="version", version=f"vme {__version__}")
    parser.add_argument("--db", type=Path, help="SQLite path (default: VME_DB_PATH)")
    parser.add_argument("--log-level", help="DEBUG|INFO|WARNING|ERROR (default: VME_LOG_LEVEL)")
    sub = parser.add_subparsers(dest="group", required=True)

    # db
    db = sub.add_parser("db", help="database maintenance").add_subparsers(
        dest="command", required=True
    )
    db.add_parser("migrate", help="apply pending schema migrations").set_defaults(
        handler=cmd_db_migrate
    )

    # source
    source = sub.add_parser("source", help="sources").add_subparsers(dest="command", required=True)
    p = source.add_parser("add", help="register a source (no rights yet: blocked by default)")
    p.add_argument("--id", help="explicit id (default: generated)")
    p.add_argument(
        "--kind",
        choices=[k.value.lower() for k in SourceKind],
        default=SourceKind.LOCAL_FILE.value.lower(),
    )
    p.add_argument("--uri", required=True, help="canonical path/URI of the source")
    p.add_argument("--publisher")
    p.add_argument("--title")
    p.set_defaults(handler=cmd_source_add)
    p = source.add_parser("show", help="show a source, its policy, gate state and media")
    p.add_argument("id")
    p.set_defaults(handler=cmd_source_show)
    source.add_parser("list", help="list sources").set_defaults(handler=cmd_source_list)

    # policy
    policy = sub.add_parser("policy", help="rights policies").add_subparsers(
        dest="command", required=True
    )
    p = policy.add_parser("add", help="attach a rights policy to a source (D009 enforced)")
    p.add_argument("--source", required=True, help="source id")
    p.add_argument("--id", help="explicit id (default: generated)")
    p.add_argument("--basis", required=True, choices=[b.value.lower() for b in BasisType])
    p.add_argument("--reference", help="evidence reference for the basis (required unless blocked)")
    p.add_argument("--can-ingest", action="store_true")
    p.add_argument("--can-extract-clip", action="store_true")
    p.add_argument("--can-transform", action="store_true")
    p.add_argument("--can-publish", action="store_true")
    p.add_argument("--requires-attribution", action="store_true")
    p.add_argument("--attribution-text")
    p.add_argument("--territory-notes")
    p.add_argument("--expiry", type=_parse_datetime, help="ISO-8601, e.g. 2027-01-01T00:00:00Z")
    p.add_argument("--review-required", action="store_true")
    p.add_argument("--notes")
    p.set_defaults(handler=cmd_policy_add)
    p = policy.add_parser("show", help="show a rights policy")
    p.add_argument("id")
    p.set_defaults(handler=cmd_policy_show)

    # media
    media = sub.add_parser("media", help="media assets").add_subparsers(
        dest="command", required=True
    )
    p = media.add_parser("register", help="fingerprint + probe a local file (rights-gated)")
    p.add_argument("--source", required=True, help="source id")
    p.add_argument("path", help="local media file")
    p.set_defaults(handler=cmd_media_register)
    p = media.add_parser("show", help="show a media asset")
    p.add_argument("id")
    p.set_defaults(handler=cmd_media_show)
    p = media.add_parser("list", help="list media assets")
    p.add_argument("--source", help="filter by source id")
    p.set_defaults(handler=cmd_media_list)

    # transcribe / transcript
    p = sub.add_parser("transcribe", help="transcribe a registered media asset (rights-gated)")
    p.add_argument("--media", required=True, help="media asset id")
    p.add_argument("--full", action="store_true", help="include all segments and words")
    p.set_defaults(group="transcribe", command="run", handler=cmd_transcribe)
    transcript = sub.add_parser("transcript", help="transcripts").add_subparsers(
        dest="command", required=True
    )
    p = transcript.add_parser("show", help="show a transcript")
    p.add_argument("id")
    p.add_argument("--full", action="store_true", help="include all segments and words")
    p.set_defaults(handler=cmd_transcript_show)
    p = transcript.add_parser("list", help="list transcripts")
    p.add_argument("--media", help="filter by media asset id")
    p.set_defaults(handler=cmd_transcript_list)

    # segment / candidate
    p = sub.add_parser("segment", help="split a transcript into candidate spans (deterministic)")
    p.add_argument("--transcript", required=True, help="transcript id")
    for name in ("min-ms", "target-ms", "max-ms", "sentence-pause-ms", "hard-pause-ms"):
        p.add_argument(f"--{name}", type=int, default=None)
    p.set_defaults(group="segment", command="run", handler=cmd_segment)
    candidate = sub.add_parser("candidate", help="candidate spans").add_subparsers(
        dest="command", required=True
    )
    p = candidate.add_parser("show", help="show a candidate")
    p.add_argument("id")
    p.set_defaults(handler=cmd_candidate_show)
    p = candidate.add_parser("list", help="list candidates of a transcript")
    p.add_argument("--transcript", required=True)
    p.add_argument("--created-by", help="filter, e.g. segmenter:v0.1.0")
    p.set_defaults(handler=cmd_candidate_list)

    # rank / ranking / llm
    p = sub.add_parser("rank", help="score and rank a transcript's candidates (LLM funnel)")
    p.add_argument("--transcript", required=True)
    p.add_argument("--finalists", type=int, help="strong-model finalists (VME_RANKING_FINALISTS)")
    p.add_argument(
        "--created-by", help="only candidates from this segmenter, e.g. segmenter:v0.1.0"
    )
    p.add_argument("--weights", help="weights JSON path (default: packaged viral_v0)")
    p.add_argument("--vertical")
    p.add_argument("--audience")
    p.add_argument("--min-ms", type=int, help="prefilter: minimum candidate duration")
    p.add_argument("--max-ms", type=int, help="prefilter: maximum candidate duration")
    p.add_argument("--min-words", type=int, help="prefilter: minimum words in the excerpt")
    p.set_defaults(group="rank", command="run", handler=cmd_rank)
    ranking = sub.add_parser("ranking", help="ranking batches").add_subparsers(
        dest="command", required=True
    )
    p = ranking.add_parser("show", help="show a batch and its runs (best first)")
    p.add_argument("id")
    p.set_defaults(handler=cmd_ranking_show)
    p = ranking.add_parser("list", help="list batches")
    p.add_argument("--transcript")
    p.set_defaults(handler=cmd_ranking_list)
    llm = sub.add_parser("llm", help="persisted LLM calls").add_subparsers(
        dest="command", required=True
    )
    p = llm.add_parser("show", help="show one LLM call with its structured response")
    p.add_argument("id")
    p.set_defaults(handler=cmd_llm_show)
    p = llm.add_parser("list", help="list LLM calls (without responses)")
    p.add_argument("--purpose")
    p.set_defaults(handler=cmd_llm_list)

    # editorial / claim / review
    editorial = sub.add_parser(
        "editorial", help="editorial drafts (text-only, D014)"
    ).add_subparsers(dest="command", required=True)
    p = editorial.add_parser("generate", help="generate a draft + claims for a candidate")
    p.add_argument("--candidate", required=True)
    p.add_argument("--target-ms", type=int, help="target clip duration (VME_TARGET_CLIP_MS)")
    p.add_argument("--vertical")
    p.add_argument("--audience")
    p.add_argument("--no-ranking", action="store_true", help="ignore ranking rationale/claims")
    p.set_defaults(handler=cmd_editorial_generate)
    p = editorial.add_parser("show", help="draft with claims and review events")
    p.add_argument("id")
    p.set_defaults(handler=cmd_editorial_show)
    p = editorial.add_parser("list", help="list drafts")
    p.add_argument("--candidate")
    p.set_defaults(handler=cmd_editorial_list)

    claim = sub.add_parser(
        "claim", help="generated claims (human-only fact check, D010)"
    ).add_subparsers(dest="command", required=True)
    p = claim.add_parser("resolve", help="mark a claim human-approved or removed")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=["human-approved", "removed"])
    p.add_argument("--reviewer", help="operator identity (default: VME_REVIEWER)")
    p.add_argument("--note")
    p.set_defaults(handler=cmd_claim_resolve)
    p = claim.add_parser("list", help="claims of a draft")
    p.add_argument("--draft", required=True)
    p.set_defaults(handler=cmd_claim_list)

    review = sub.add_parser("review", help="human review decisions (audited)").add_subparsers(
        dest="command", required=True
    )
    p = review.add_parser(
        "approve", help="approve a needs_review draft (re-checks rights + claims)"
    )
    p.add_argument("id")
    p.add_argument("--reviewer")
    p.add_argument("--note")
    p.set_defaults(handler=cmd_review_approve)
    p = review.add_parser("reject", help="reject a needs_review draft with reason codes")
    p.add_argument("id")
    p.add_argument(
        "--reason",
        action="append",
        required=True,
        help="reason code (repeatable or comma-separated)",
    )
    p.add_argument("--reviewer")
    p.add_argument("--note")
    p.set_defaults(handler=cmd_review_reject)
    p = review.add_parser("recheck", help="lift a factcheck/rights block when it no longer holds")
    p.add_argument("id")
    p.add_argument("--reviewer")
    p.set_defaults(handler=cmd_review_recheck)
    p = review.add_parser("events", help="audit log")
    p.add_argument("--object-type", help="editorial_version | claim")
    p.add_argument("--object-id")
    p.set_defaults(handler=cmd_review_events)

    # render
    render = sub.add_parser("render", help="deterministic vertical render (D013)").add_subparsers(
        dest="command", required=True
    )
    p = render.add_parser("plan", help="build a RenderPlan from an approved draft")
    p.add_argument("--draft", required=True)
    p.add_argument("--hook-ms", type=int)
    p.add_argument("--outro-ms", type=int)
    p.set_defaults(handler=cmd_render_plan)
    p = render.add_parser("run", help="render a plan to artifacts/renders/<id>.mp4 and validate it")
    p.add_argument("--plan", required=True)
    p.add_argument("--preset", help="x264 preset (default VME_RENDER_PRESET)")
    p.set_defaults(handler=cmd_render_run)
    p = render.add_parser("show", help="render with its plan")
    p.add_argument("id")
    p.set_defaults(handler=cmd_render_show)
    p = render.add_parser("plans", help="list render plans")
    p.add_argument("--draft")
    p.set_defaults(handler=cmd_render_plans)
    p = render.add_parser("list", help="list renders")
    p.add_argument("--plan")
    p.set_defaults(handler=cmd_render_list)

    # pipeline
    pipeline = sub.add_parser(
        "pipeline", help="run the automated stages up to human review"
    ).add_subparsers(dest="command", required=True)
    p = pipeline.add_parser(
        "run", help="register -> transcribe -> segment -> rank -> editorial(top-k)"
    )
    p.add_argument(
        "--source", required=True, help="source id (policy must permit ingest/transform)"
    )
    p.add_argument("--path", help="local media file to register")
    p.add_argument("--media", help="already registered media asset id (skips register)")
    p.add_argument("--top", type=int, default=3, help="drafts to generate for the best candidates")
    p.add_argument("--finalists", type=int)
    p.add_argument("--seg-min-ms", type=int)
    p.add_argument("--seg-target-ms", type=int)
    p.add_argument("--seg-max-ms", type=int)
    p.add_argument("--min-ms", type=int, help="prefilter minimum duration")
    p.add_argument("--max-ms", type=int, help="prefilter maximum duration")
    p.add_argument("--min-words", type=int)
    p.add_argument("--vertical")
    p.add_argument("--audience")
    p.add_argument("--target-ms", type=int)
    p.set_defaults(handler=cmd_pipeline_run)
    return parser


# ------------------------------------------------------------------------------- main


def run(argv: Sequence[str] | None, *, out: Any, err: Any) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage/help
        return int(exc.code or 0)

    settings = load_settings()
    cid = new_correlation_id()
    configure_logging(
        level=args.log_level or settings.log_level,
        stream=err,
        secret_values=settings.secret_values(),
    )
    db_path = args.db or settings.db_path
    log.info(
        "cli_start",
        extra={
            "command": f"{args.group} {args.command}",
            "db": display_path(db_path, settings.artifacts_dir),
        },
    )
    handler: Handler = args.handler
    store: Store | None = None
    try:
        store = Store.open(db_path)
        result = handler(args, store, settings)
        _emit(result, out)
        log.info("cli_end", extra={"status": "success"})
        return EXIT_OK
    except RightsBlockedError as exc:
        d = exc.decision
        _emit(
            {
                "error": "blocked_policy",
                "action": d.action.value,
                "policy_id": d.policy_id,
                "reason_code": d.reason_code,
                "detail": d.detail,
                "correlation_id": cid,
            },
            out,
        )
        log.warning(
            "cli_end",
            extra={"status": "blocked_policy", "reason_code": d.reason_code, "detail": d.detail},
        )
        return EXIT_BLOCKED
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ]
        _emit({"error": "validation_error", "errors": errors, "correlation_id": cid}, out)
        log.error("cli_end", extra={"status": "permanent_failure", "errors": errors})
        return EXIT_ERROR
    except (
        NotFoundError,
        DuplicateRecordError,
        IngestionError,
        ProbeError,
        InvalidTransitionError,
        ValueError,
        RuntimeError,
    ) as exc:
        _emit({"error": type(exc).__name__, "detail": str(exc), "correlation_id": cid}, out)
        log.error("cli_end", extra={"status": "permanent_failure", "detail": str(exc)})
        return EXIT_ERROR
    finally:
        if store is not None:
            store.close()


def main(argv: Sequence[str] | None = None) -> int:
    code = run(argv, out=sys.stdout, err=sys.stderr)
    if argv is None:
        sys.exit(code)
    return code
