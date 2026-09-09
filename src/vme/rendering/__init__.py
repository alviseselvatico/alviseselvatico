"""Deterministic vertical rendering: RenderPlan -> FFmpeg -> validated MP4 (D004, D013, D014)."""

from vme.rendering.ffmpeg import RenderError, RenderSettings, find_font
from vme.rendering.plan import TEMPLATE_VERSION, PlanConfig, build_render_plan, chunk_captions
from vme.rendering.service import render_plan_to_file

__all__ = [
    "TEMPLATE_VERSION",
    "PlanConfig",
    "RenderError",
    "RenderSettings",
    "build_render_plan",
    "chunk_captions",
    "find_font",
    "render_plan_to_file",
]
