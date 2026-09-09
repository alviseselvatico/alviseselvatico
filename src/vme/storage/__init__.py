"""SQLite persistence: connection, explicit migrations, repositories."""

from vme.storage.db import Store, connect, migrate
from vme.storage.repositories import (
    CandidateRepository,
    DuplicateRecordError,
    LlmCallRepository,
    MediaAssetRepository,
    NotFoundError,
    RankingRepository,
    RightsPolicyRepository,
    SourceRepository,
    TranscriptRepository,
)

__all__ = [
    "CandidateRepository",
    "DuplicateRecordError",
    "LlmCallRepository",
    "MediaAssetRepository",
    "NotFoundError",
    "RankingRepository",
    "RightsPolicyRepository",
    "SourceRepository",
    "Store",
    "TranscriptRepository",
    "connect",
    "migrate",
]
