from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.conftest import NOW, make_policy, make_source
from vme.domain.models import BasisType, MediaAsset, SourceStatus
from vme.storage.db import Store, applied_versions, connect, migrate
from vme.storage.migrations import MIGRATIONS
from vme.storage.repositories import DuplicateRecordError, NotFoundError

SHA = "a" * 64


def test_migrate_is_idempotent_and_creates_parent_dirs(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "vme.sqlite3"
    conn = connect(db)
    assert migrate(conn) == [m.version for m in MIGRATIONS]
    assert migrate(conn) == []
    assert applied_versions(conn) == [m.version for m in MIGRATIONS]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_unknown_applied_version_is_refused(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.sqlite3")
    migrate(conn)
    conn.execute("INSERT INTO schema_migrations VALUES (99, 'future', 'now')")
    with pytest.raises(RuntimeError, match="unknown migration"):
        migrate(conn)


def test_policy_and_source_round_trip(store: Store) -> None:
    policy = make_policy(BasisType.CREATOR_AUTHORIZATION, notes="scope: shorts only")
    store.policies.add(policy)
    source = store.sources.add(make_source())
    assert source.rights_policy_id is None
    attached = store.sources.attach_policy(source.id, policy.id)
    assert attached.rights_policy_id == policy.id
    assert store.policies.get(policy.id) == policy
    assert store.sources.get(source.id) == attached
    assert store.sources.list() == [attached]


def test_duplicates_and_missing_records_are_visible_errors(store: Store) -> None:
    store.policies.add(make_policy())
    with pytest.raises(DuplicateRecordError):
        store.policies.add(make_policy())
    with pytest.raises(NotFoundError):
        store.sources.get("nope")
    with pytest.raises(NotFoundError):
        store.policies.get("nope")
    with pytest.raises(NotFoundError):
        store.media.get("nope")
    with pytest.raises(NotFoundError):
        store.sources.attach_policy("nope", "pol_owned")


def test_foreign_keys_are_enforced(store: Store) -> None:
    with pytest.raises(DuplicateRecordError):
        store.sources.add(make_source(policy_id="pol_missing"))
    store.sources.add(make_source())
    with pytest.raises(sqlite3.IntegrityError):
        store.sources.attach_policy("S001", "pol_missing")


def test_media_asset_round_trip_and_fingerprint_uniqueness(store: Store) -> None:
    store.sources.add(make_source())
    asset = MediaAsset(
        id="med_1",
        source_id="S001",
        sha256=SHA,
        path_or_object_key="/x/y.wav",
        duration_ms=1000,
        audio_codec="pcm_s16le",
        ingested_at=NOW,
    )
    store.media.add(asset)
    assert store.media.get("med_1") == asset
    assert store.media.find_by_fingerprint("S001", SHA) == asset
    assert store.media.find_by_fingerprint("S001", "b" * 64) is None
    assert store.media.list("S001") == [asset]
    with pytest.raises(DuplicateRecordError):
        store.media.add(asset.model_copy(update={"id": "med_2"}))
    with pytest.raises(DuplicateRecordError):
        store.media.add(asset.model_copy(update={"id": "med_3", "source_id": "ghost"}))


def test_transaction_rolls_back_on_error(store: Store) -> None:
    with pytest.raises(RuntimeError), store.transaction():
        store.sources.add(make_source())
        raise RuntimeError("boom")
    assert store.sources.list() == []
    assert store.sources.set_status  # still usable
    with pytest.raises(NotFoundError):
        store.sources.set_status("S001", SourceStatus.INGESTED)


def test_hand_edited_row_violating_d009_cannot_be_read(store: Store) -> None:
    store.policies.add(make_policy(BasisType.UNKNOWN))
    store.conn.execute("UPDATE rights_policies SET can_ingest = 1 WHERE id = 'pol_unknown'")
    with pytest.raises(ValidationError, match="D009"):
        store.policies.get("pol_unknown")
