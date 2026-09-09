"""Build a ``RenderPlan`` from an approved draft. Pure given the stored inputs.

Template ``letterbox_blur_v0``: hook card (hook + commentary_before) -> one source item per
excerpt span with burned-in captions from the transcript's word timestamps -> optional
outro card (commentary_after + CTA). Attribution overlay when the policy requires it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from vme.domain.models import (
    Caption,
    CaptionConfig,
    DraftStatus,
    OverlayConfig,
    RenderPlan,
    RightsPolicy,
    TimelineItem,
    TimelineKind,
    Word,
    new_id,
    utc_now,
)
from vme.editorial.service import policy_for_candidate
from vme.logs import get_logger
from vme.rights.gate import Action, require
from vme.storage.db import Store

log = get_logger("rendering.plan")

TEMPLATE_VERSION = "letterbox_blur_v0.1.0"


class PlanError(RuntimeError):
    pass


_WORDS_PER_SECOND = 2.6  # comfortable on-screen reading pace
_CARD_PADDING_MS = 800
_MAX_CARD_MS = 9000


def card_duration_ms(*texts: str, minimum_ms: int) -> int:
    """Deterministic reading time for a card, never below ``minimum_ms``."""
    words = sum(len(t.split()) for t in texts)
    reading = round((words / _WORDS_PER_SECOND) * 1000) + _CARD_PADDING_MS
    return min(_MAX_CARD_MS, max(minimum_ms, reading))


@dataclass(frozen=True, slots=True)
class PlanConfig:
    width: int = 1080
    height: int = 1920
    hook_ms: int = 2500
    outro_ms: int = 3000
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)


def chunk_captions(
    words: list[Word], start_ms: int, end_ms: int, cfg: CaptionConfig
) -> list[Caption]:
    """Group the words inside ``[start_ms, end_ms)`` into short caption lines."""
    inside = [w for w in words if w.start_ms >= start_ms and w.end_ms <= end_ms]
    captions: list[Caption] = []
    group: list[Word] = []

    def flush() -> None:
        if not group:
            return
        text = " ".join(w.text for w in group)
        c_start = max(0, group[0].start_ms - start_ms)
        c_end = min(end_ms - start_ms, max(group[-1].end_ms - start_ms, c_start + 200))
        if c_end > c_start:
            captions.append(Caption(start_ms=c_start, end_ms=c_end, text=text))
        group.clear()

    for w in inside:
        candidate_text = " ".join(x.text for x in [*group, w])
        span = w.end_ms - group[0].start_ms if group else 0
        if group and (
            len(group) >= cfg.max_words
            or len(candidate_text) > cfg.max_chars
            or span > cfg.max_duration_ms
        ):
            flush()
        group.append(w)
        if w.text.endswith((".", "?", "!")):
            flush()
    flush()
    return captions


def _attribution(policy: RightsPolicy) -> str | None:
    if policy.requires_attribution and policy.attribution_text:
        return policy.attribution_text.strip()
    return None


def build_render_plan(
    store: Store,
    draft_id: str,
    config: PlanConfig | None = None,
    *,
    artifacts_dir: Path | None = None,
    now: datetime | None = None,
) -> RenderPlan:
    config = config or PlanConfig()
    now = now or utc_now()
    draft = store.editorial.get(draft_id)
    if draft.status is not DraftStatus.APPROVED:
        msg = f"draft {draft_id!r} is {draft.status.value}; only approved drafts can be rendered"
        raise PlanError(msg)
    candidate = store.candidates.get(draft.candidate_id)
    policy = policy_for_candidate(store, candidate)
    require(policy, Action.CLIP, now=now)
    require(policy, Action.RENDER_TRANSFORM, now=now)
    assert policy is not None  # noqa: S101 - require() raised otherwise
    transcript = store.transcripts.get(candidate.transcript_id)
    words = transcript.words()

    timeline: list[TimelineItem] = [
        TimelineItem(
            kind=TimelineKind.CARD,
            duration_ms=card_duration_ms(
                draft.hook, draft.commentary_before, minimum_ms=config.hook_ms
            ),
            title=draft.hook,
            body=draft.commentary_before,
        )
    ]
    for span in draft.excerpt_plan:
        timeline.append(
            TimelineItem(
                kind=TimelineKind.SOURCE,
                duration_ms=span.end_ms - span.start_ms,
                source_start_ms=span.start_ms,
                source_end_ms=span.end_ms,
                captions=chunk_captions(words, span.start_ms, span.end_ms, config.captions),
            )
        )
    if draft.commentary_after or draft.cta:
        timeline.append(
            TimelineItem(
                kind=TimelineKind.CARD,
                duration_ms=card_duration_ms(
                    draft.commentary_after, draft.cta or "", minimum_ms=config.outro_ms
                ),
                title=draft.commentary_after,
                body=draft.cta or "",
            )
        )
    overlay = config.overlay.model_copy(update={"attribution_text": _attribution(policy)})
    plan = RenderPlan(
        id=new_id("rpl"),
        editorial_version_id=draft.id,
        template_version=TEMPLATE_VERSION,
        width=config.width,
        height=config.height,
        timeline=timeline,
        caption_config=config.captions,
        overlay_config=overlay,
        created_at=now,
    )
    with store.transaction():
        store.renders.add_plan(plan)
    if artifacts_dir is not None:
        out = artifacts_dir / "render_plans" / f"{plan.id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))
    log.info(
        "render_plan_created",
        extra={
            "editorial_version_id": draft.id,
            "render_plan_id": plan.id,
            "template_version": plan.template_version,
            "items": len(timeline),
            "captions": sum(len(i.captions) for i in timeline),
            "total_duration_ms": plan.total_duration_ms,
            "attribution": overlay.attribution_text is not None,
        },
    )
    return plan
