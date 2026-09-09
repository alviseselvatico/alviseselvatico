from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import BasisType, MediaAsset, TranscriptKind
from vme.ingestion.register import register_local_media
from vme.rights.gate import RightsBlockedError
from vme.storage.db import Store
from vme.transcription.base import TranscriptionError
from vme.transcription.service import transcribe_media


def _seed_asset(
    store: Store, path: Path, artifacts_dir: Path, basis: BasisType = BasisType.OWNED
) -> MediaAsset:
    store.sources.add(make_source())
    policy = make_policy(basis)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    return register_local_media(store, "S001", path, artifacts_dir=artifacts_dir, now=NOW)


@requires_ffmpeg
def test_transcribe_creates_raw_transcript_v1_then_v2(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    asset = _seed_asset(store, audio_wav, artifacts_dir)
    stt = FakeSpeechToText()
    t1 = transcribe_media(store, asset.id, stt, artifacts_dir=artifacts_dir, now=NOW)
    assert t1.kind is TranscriptKind.RAW and t1.version == 1 and t1.derived_from_id is None
    assert t1.provider == "fake" and t1.model_alias == "fake-1" and t1.language == "en"
    assert t1.raw_text.startswith("This is the first sentence")
    assert len(t1.segments) == 8 and len(t1.words()) == sum(len(s.split()) for s in stt.sentences)
    assert store.transcripts.get(t1.id) == t1
    assert stt.calls == [audio_wav.resolve()]
    t2 = transcribe_media(store, asset.id, stt, artifacts_dir=artifacts_dir, now=NOW)
    assert t2.version == 2 and [t.id for t in store.transcripts.list(asset.id)] == [t1.id, t2.id]


@requires_ffmpeg
def test_transcribe_blocked_when_policy_expired_after_ingest(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    asset = _seed_asset(store, audio_wav, artifacts_dir)
    stt = FakeSpeechToText()
    # make it precise: attach an expired policy and check reason
    expired = make_policy(BasisType.OWNED, id="pol_exp", expiry_at=NOW - timedelta(days=1))
    store.policies.add(expired)
    store.sources.attach_policy("S001", "pol_exp")
    with pytest.raises(RightsBlockedError) as excinfo:
        transcribe_media(store, asset.id, stt, artifacts_dir=artifacts_dir, now=NOW)
    assert excinfo.value.decision.reason_code == "policy_expired"
    assert stt.calls == [] and store.transcripts.list(asset.id) == []


@requires_ffmpeg
def test_transcribe_refuses_when_file_changed_or_missing(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    asset = _seed_asset(store, audio_wav, artifacts_dir)
    audio_wav.write_bytes(audio_wav.read_bytes() + b"\x00")
    with pytest.raises(TranscriptionError, match="no longer matches"):
        transcribe_media(store, asset.id, FakeSpeechToText(), artifacts_dir=artifacts_dir, now=NOW)
    audio_wav.unlink()
    with pytest.raises(TranscriptionError, match="missing"):
        transcribe_media(store, asset.id, FakeSpeechToText(), artifacts_dir=artifacts_dir, now=NOW)


@requires_ffmpeg
def test_transcribe_refuses_empty_provider_output(
    store: Store, audio_wav: Path, artifacts_dir: Path
) -> None:
    asset = _seed_asset(store, audio_wav, artifacts_dir)
    with pytest.raises(TranscriptionError, match="no segments"):
        transcribe_media(
            store, asset.id, FakeSpeechToText([]), artifacts_dir=artifacts_dir, now=NOW
        )
