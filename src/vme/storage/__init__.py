"""SQLite persistence: connection, explicit migrations, repositories."""

from vme.storage.db import Store, connect, migrate
from vme.storage.repositories import (
    CandidateRepository,
    ClaimRepository,
    DuplicateRecordError,
    EditorialRepository,
    LlmCallRepository,
    MediaAssetRepository,
    NotFoundError,
    RankingRepository,
    ReviewEventRepository,
    RightsPolicyRepository,
    SourceRepository,
    TranscriptRepository,
)

__all__ = [
    "CandidateRepository",
    "ClaimRepository",
    "DuplicateRecordError",
    "EditorialRepository",
    "LlmCallRepository",
    "MediaAssetRepository",
    "NotFoundError",
    "RankingRepository",
    "ReviewEventRepository",
    "RightsPolicyRepository",
    "SourceRepository",
    "Store",
    "TranscriptRepository",
    "connect",
    "migrate",
]
