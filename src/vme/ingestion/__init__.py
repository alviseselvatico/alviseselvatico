"""Local media ingestion: validation, fingerprint, ffprobe metadata, registration.

This package never decides rights; it asks the rights gate (ARCHITECTURE §3).
"""

from vme.ingestion.fingerprint import sha256_file
from vme.ingestion.probe import ProbeError, ProbeResult, probe_media
from vme.ingestion.register import IngestionError, register_local_media

__all__ = [
    "IngestionError",
    "ProbeError",
    "ProbeResult",
    "probe_media",
    "register_local_media",
    "sha256_file",
]
