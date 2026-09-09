"""FFmpeg command construction and execution. Argument lists only, never shell strings.

Text goes through ``drawtext=textfile=`` files written in a private work directory, so no
user or model text is ever spliced into a filter graph (ARCHITECTURE §11).
"""

from __future__ import annotations

import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from vme.domain.models import RenderPlan, TimelineItem, TimelineKind

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


_LINE_HEIGHT = 1.3  # drawtext line height relative to font size (line_spacing included)
_MIN_FONT = 28
_GAP = 60


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenderSettings:
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    font_path: Path = Path(_FONT_CANDIDATES[0])
    preset: str = "medium"
    crf: int = 20
    fps: int = 30
    timeout_s: int = 900


def find_font(explicit: str | Path | None = None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        msg = f"configured font not found: {p.name} (VME_RENDER_FONT)"
        raise RenderError(msg)
    for c in _FONT_CANDIDATES:
        if Path(c).is_file():
            return Path(c)
    msg = "no TrueType font found; set VME_RENDER_FONT to a .ttf file"
    raise RenderError(msg)


def wrap_text(text: str, width: int) -> str:
    lines: list[str] = []
    for para in text.strip().splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=width) or [""])
    return "\n".join(lines)


def _esc(value: str) -> str:
    """Escape a value for use inside a filter option (paths, colours)."""
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _sec(ms: int) -> str:
    return f"{ms / 1000:.3f}"


class _Graph:
    def __init__(self, plan: RenderPlan, settings: RenderSettings, workdir: Path) -> None:
        self.plan = plan
        self.settings = settings
        self.workdir = workdir
        self.inputs: list[str] = []
        self.filters: list[str] = []
        self._n_inputs = 0
        self._n_text = 0
        self.font = _esc(str(settings.font_path))

    def add_input(self, args: list[str]) -> int:
        self.inputs.extend(args)
        idx = self._n_inputs
        self._n_inputs += 1
        return idx

    def textfile(self, text: str) -> str:
        path = self.workdir / f"text_{self._n_text:03d}.txt"
        self._n_text += 1
        path.write_text(text, encoding="utf-8")
        return _esc(str(path))

    def drawtext(
        self, text: str, size: int, y: str, *, enable: str | None = None, box: bool = True
    ) -> str:
        parts = [
            f"fontfile='{self.font}'",
            f"textfile='{self.textfile(text)}'",
            f"fontsize={size}",
            f"fontcolor={self.plan.overlay_config.text_color}",
            "x=(w-text_w)/2",
            f"y={y}",
            "line_spacing=10",
            "text_align=center",
        ]
        if box:
            parts += ["box=1", "boxcolor=black@0.55", "boxborderw=22"]
        if enable:
            parts.append(f"enable='{enable}'")
        return "drawtext=" + ":".join(parts)

    def _fit(self, text: str, size: int, wrap: int, max_h: int) -> tuple[str, int, int]:
        """Wrap and shrink deterministically until the block fits ``max_h`` pixels."""
        while True:
            wrapped = wrap_text(text, wrap)
            lines = wrapped.count("\n") + 1
            height = round(lines * size * _LINE_HEIGHT)
            if height <= max_h or size <= _MIN_FONT:
                return wrapped, size, height
            size = max(_MIN_FONT, size - 4)
            wrap = wrap + 2

    def card(self, item: TimelineItem, i: int) -> None:
        w, h, fps = self.plan.width, self.plan.height, self.settings.fps
        ov = self.plan.overlay_config
        dur = _sec(item.duration_ms)
        v = self.add_input(
            ["-f", "lavfi", "-t", dur, "-i", f"color=c={ov.background_color}:s={w}x{h}:r={fps}"]
        )
        a = self.add_input(["-f", "lavfi", "-t", dur, "-i", "anullsrc=r=48000:cl=stereo"])
        chain = [f"[{v}:v]format=yuv420p"]
        usable = h - 2 * ov.safe_margin_px
        title = item.title.strip()
        body = item.body.strip()
        blocks: list[tuple[str, int, int]] = []
        if title:
            blocks.append(self._fit(title, ov.hook_font_size, ov.wrap_chars, usable // 2))
        if body:
            room = usable - (blocks[0][2] + _GAP if blocks else 0)
            blocks.append(self._fit(body, ov.body_font_size, ov.wrap_chars + 10, room))
        total = sum(b[2] for b in blocks) + (_GAP if len(blocks) == 2 else 0)
        y = max(ov.safe_margin_px, (h - total) // 2)
        for text, size, height in blocks:
            chain.append(self.drawtext(text, size, str(y), box=False))
            y += height + _GAP
        chain.append(f"setsar=1[v{i}]")
        self.filters.append(",".join(chain))
        self.filters.append(f"[{a}:a]aresample=48000[a{i}]")

    def source(
        self, item: TimelineItem, i: int, source_path: Path, has_video: bool, has_audio: bool
    ) -> None:
        w, h, fps = self.plan.width, self.plan.height, self.settings.fps
        ov, cc = self.plan.overlay_config, self.plan.caption_config
        assert item.source_start_ms is not None and item.source_end_ms is not None  # noqa: S101
        src = self.add_input(
            [
                "-ss",
                _sec(item.source_start_ms),
                "-to",
                _sec(item.source_end_ms),
                "-i",
                str(source_path),
            ]
        )
        dur = _sec(item.duration_ms)
        if has_video:
            self.filters.append(
                f"[{src}:v]fps={fps},split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=luma_radius=30:luma_power=2:chroma_radius=15:chroma_power=2[bgb{i}];"
                f"[fg{i}]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs{i}];"
                f"[bgb{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:shortest=1[base{i}]"
            )
        else:
            bgv = self.add_input(
                ["-f", "lavfi", "-t", dur, "-i", f"color=c={ov.background_color}:s={w}x{h}:r={fps}"]
            )
            self.filters.append(f"[{bgv}:v]format=yuv420p[base{i}]")
        chain = [f"[base{i}]format=yuv420p"]
        for cap in item.captions:
            chain.append(
                self.drawtext(
                    wrap_text(cap.text, cc.max_chars),
                    cc.font_size,
                    f"h-{cc.margin_bottom_px}-text_h",
                    enable=f"between(t,{_sec(cap.start_ms)},{_sec(cap.end_ms)})",
                )
            )
        if ov.attribution_text:
            chain.append(
                self.drawtext(
                    ov.attribution_text,
                    ov.attribution_font_size,
                    f"h-{ov.safe_margin_px}-text_h",
                    box=False,
                )
            )
        chain.append(f"trim=duration={dur},setsar=1[v{i}]")
        self.filters.append(",".join(chain))
        if has_audio:
            self.filters.append(f"[{src}:a]aresample=48000,atrim=duration={dur}[a{i}]")
        else:
            sil = self.add_input(["-f", "lavfi", "-t", dur, "-i", "anullsrc=r=48000:cl=stereo"])
            self.filters.append(f"[{sil}:a]aresample=48000[a{i}]")


def build_command(
    plan: RenderPlan,
    source_path: Path,
    out_path: Path,
    workdir: Path,
    settings: RenderSettings,
    *,
    has_video: bool,
    has_audio: bool,
) -> list[str]:
    workdir.mkdir(parents=True, exist_ok=True)
    g = _Graph(plan, settings, workdir)
    for i, item in enumerate(plan.timeline):
        if item.kind is TimelineKind.CARD:
            g.card(item, i)
        else:
            g.source(item, i, source_path, has_video, has_audio)
    n = len(plan.timeline)
    streams = "".join(f"[v{i}][a{i}]" for i in range(n))
    g.filters.append(f"{streams}concat=n={n}:v=1:a=1[vout][aout]")
    return [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-v",
        "error",
        *g.inputs,
        "-filter_complex",
        ";".join(g.filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        settings.preset,
        "-crf",
        str(settings.crf),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(settings.fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def run_ffmpeg(argv: list[str], timeout_s: int) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except FileNotFoundError as exc:
        msg = f"ffmpeg binary not found: {argv[0]!r}"
        raise RenderError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"ffmpeg timed out after {timeout_s}s"
        raise RenderError(msg) from exc
    if completed.returncode != 0:
        msg = f"ffmpeg failed (exit {completed.returncode}): {completed.stderr.strip()[-800:]}"
        raise RenderError(msg)
