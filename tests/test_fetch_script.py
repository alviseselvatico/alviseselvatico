from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load() -> object:
    spec = importlib.util.spec_from_file_location(
        "fetch", ROOT / "scripts" / "fetch_speech_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses with slots resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def test_manifest_entries_are_pinned_with_evidence_and_ignored_by_git() -> None:
    mod = _load()
    fixtures = mod.FIXTURES  # type: ignore[attr-defined]
    assert fixtures
    for f in fixtures:
        assert f.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", f.sha256)
        assert f.evidence and f.rights_basis and f.expected_phrases
        assert f.download_path.parent == ROOT / "fixtures" / "speech" / "external"
    ignore = (ROOT / ".gitignore").read_text()
    assert "fixtures/speech/external/" in ignore
    assert "!fixtures/speech/sample_en.wav" in ignore


def test_unknown_fixture_name_is_a_usage_error() -> None:
    mod = _load()
    assert mod.main(["does-not-exist"]) == 2  # type: ignore[attr-defined]
