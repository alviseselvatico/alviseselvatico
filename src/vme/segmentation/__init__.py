"""Deterministic semantic segmentation of transcripts into candidate spans."""

from vme.segmentation.segmenter import SegmentationConfig, created_by, segment_transcript
from vme.segmentation.service import segment_and_store

__all__ = ["SegmentationConfig", "created_by", "segment_and_store", "segment_transcript"]
