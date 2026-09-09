"""SQLite persistence: connection, explicit migrations, repositories."""

from vme.storage.db import Store, connect, migrate
from vme.storage.repositories import (
    DuplicateRecordError,
    MediaAssetRepository,
    NotFoundError,
    RightsPolicyRepository,
    SourceRepository,
)

__all__ = [
    "DuplicateRecordError",
    "MediaAssetRepository",
    "NotFoundError",
    "RightsPolicyRepository",
    "SourceRepository",
    "Store",
    "connect",
    "migrate",
]
