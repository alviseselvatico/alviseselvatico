"""Download third-party speech fixtures into ``fixtures/speech/external/`` (git-ignored).

Third-party media is never committed (CLAUDE.md, D019). Every entry pins the URL, the
SHA-256 of the exact bytes and the rights evidence, so a test that runs on the file is
reproducible and the operator can see why the file may be used *for tests*. Nothing here
registers a source or grants a publishing right: ``docs/SOURCES.md`` stays the register.

Usage:
    uv run python scripts/fetch_speech_fixtures.py            # fetch all pinned fixtures
    uv run python scripts/fetch_speech_fixtures.py jfk        # one fixture by name
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "fixtures" / "speech" / "external"
USER_AGENT = "VME-fixture-fetch/0.1 (+https://github.com/alviseselvatico/alviseselvatico)"


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    url: str
    sha256: str
    rights_basis: str
    evidence: str
    expected_phrases: tuple[str, ...]
    convert_to_wav: bool = False  # 16 kHz mono PCM via ffmpeg (argument list, no shell)

    @property
    def download_path(self) -> Path:
        return DEST / f"{self.name}{Path(self.url).suffix or '.bin'}"

    @property
    def wav_path(self) -> Path:
        return DEST / f"{self.name}.wav"


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="jfk",
        url="https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav",
        sha256="59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e",
        rights_basis="PUBLIC_DOMAIN (candidate; operator to confirm before any non-test use)",
        evidence=(
            "Excerpt (11 s) of John F. Kennedy's inaugural address, 1961-01-20, a work of "
            "the United States federal government (17 U.S.C. §105). Copy distributed as a "
            "test sample by ggerganov/whisper.cpp (samples/jfk.wav) and openai/whisper "
            "(tests/jfk.flac). 16 kHz mono PCM."
        ),
        expected_phrases=("my fellow americans", "your country", "do for you"),
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - https
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def to_wav(src: Path, dst: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(dst)],  # noqa: S607
        check=True,
        timeout=300,
    )


def fetch(fixture: Fixture) -> Path:
    DEST.mkdir(parents=True, exist_ok=True)
    target = fixture.download_path
    if target.is_file() and sha256_file(target) == fixture.sha256:
        print(f"[ok] {fixture.name}: already present, sha256 verified")
    else:
        tmp = target.with_suffix(target.suffix + ".part")
        print(f"[..] {fixture.name}: downloading {fixture.url}")
        download(fixture.url, tmp)
        actual = sha256_file(tmp)
        if actual != fixture.sha256:
            tmp.unlink(missing_ok=True)
            msg = (
                f"{fixture.name}: sha256 mismatch\n  expected {fixture.sha256}\n  actual   {actual}"
            )
            raise SystemExit(msg)
        tmp.replace(target)
        print(f"[ok] {fixture.name}: sha256 verified")
    if fixture.convert_to_wav and target != fixture.wav_path:
        to_wav(target, fixture.wav_path)
        print(f"[ok] {fixture.name}: converted to {fixture.wav_path.name}")
    print(f"     rights: {fixture.rights_basis}")
    print(f"     evidence: {fixture.evidence}")
    return fixture.wav_path if fixture.wav_path.is_file() else target


def main(argv: list[str]) -> int:
    wanted = set(argv) or {f.name for f in FIXTURES}
    unknown = wanted - {f.name for f in FIXTURES}
    if unknown:
        print(f"unknown fixture(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    try:
        for fixture in FIXTURES:
            if fixture.name in wanted:
                fetch(fixture)
    except (urllib.error.URLError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
