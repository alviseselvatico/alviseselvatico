"""``vme`` CLI: ``source``, ``policy``, ``media``, ``transcribe``, ``transcript``,
``segment``, ``candidate``, ``db migrate``.

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
from vme.domain.models import BasisType, RightsPolicy, Source, SourceKind, new_id, utc_now
from vme.ingestion.probe import ProbeError
from vme.ingestion.register import IngestionError, register_local_media
from vme.logs import configure_logging, display_path, get_logger, new_correlation_id
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
