"""SQLite persistence: connection, explicit migrations, repositories."""

from vme.storage.db import Store, connect, migrate
from vme.storage.repositories import (
    BenchmarkRepository,
    CandidateRepository,
    ClaimRepository,
    DuplicateRecordError,
    EditorialRepository,
    LabelRepository,
    LlmCallRepository,
    MediaAssetRepository,
    NotFoundError,
    RankingRepository,
    RenderRepository,
    ReviewEventRepository,
    RightsPolicyRepository,
    SourceRepository,
    TranscriptRepository,
)

__all__ = [
    "BenchmarkRepository",
    "CandidateRepository",
    "ClaimRepository",
    "DuplicateRecordError",
    "EditorialRepository",
    "LabelRepository",
    "LlmCallRepository",
    "MediaAssetRepository",
    "NotFoundError",
    "RankingRepository",
    "RenderRepository",
    "ReviewEventRepository",
    "RightsPolicyRepository",
    "SourceRepository",
    "Store",
    "TranscriptRepository",
    "connect",
    "migrate",
]
