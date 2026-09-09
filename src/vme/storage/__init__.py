"""SQLite persistence: connection, explicit migrations, repositories."""

from vme.storage.db import Store, connect, migrate
from vme.storage.repositories import (
    CandidateRepository,
    DuplicateRecordError,
    MediaAssetRepository,
    NotFoundError,
    RightsPolicyRepository,
    SourceRepository,
    TranscriptRepository,
)

__all__ = [
    "CandidateRepository",
    "DuplicateRecordError",
    "MediaAssetRepository",
    "NotFoundError",
    "RightsPolicyRepository",
    "SourceRepository",
    "Store",
    "TranscriptRepository",
    "connect",
    "migrate",
]
