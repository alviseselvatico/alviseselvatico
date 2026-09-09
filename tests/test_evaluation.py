from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import NOW, make_policy, make_source, requires_ffmpeg
from tests.fake_llm import FakeLlm
from tests.fake_stt import FakeSpeechToText
from vme.domain.models import BasisType, LabelDecision, PerformanceBucket
from vme.evaluation.benchmark import BenchmarkError, compare_benchmarks, run_benchmark
from vme.evaluation.metrics import (
    calibration_by_bucket,
    ndcg_at_k,
    pairwise_agreement,
    precision_at_k,
)
from vme.ingestion.register import register_local_media
from vme.labeling.service import add_label
from vme.labeling.taxonomy import load_taxonomy
from vme.ranking.features import PrefilterConfig
from vme.ranking.schemas import COMPONENTS, PENALTIES
from vme.ranking.scoring import SCORING_VERSION, compute_score
from vme.ranking.service import RankingConfig, rank_transcript
from vme.ranking.weights import load_weights
from vme.segmentation.segmenter import SegmentationConfig
from vme.segmentation.service import segment_and_store
from vme.storage.db import Store
from vme.transcription.service import transcribe_media

ROOT = Path(__file__).resolve().parents[1]
TAX = load_taxonomy()


def test_precision_and_ndcg() -> None:
    assert precision_at_k([True, False, True, True], 2) == 0.5
    assert precision_at_k([True, False, True, True], 10) == 0.75
    assert precision_at_k([], 3) is None and precision_at_k([True], 0) is None
    assert ndcg_at_k([3, 2, 1], 3) == pytest.approx(1.0)  # perfect order
    assert ndcg_at_k([0, 0, 0], 3) is None  # nothing relevant: undefined, not 0
    worse = ndcg_at_k([1, 2, 3], 3)
    assert worse is not None and worse < 1.0


def test_pairwise_agreement() -> None:
    # system agrees on every strictly ordered pair
    assert pairwise_agreement([90, 50, 10], [3, 2, 0]) == 1.0
    # fully reversed
    assert pairwise_agreement([10, 50, 90], [3, 2, 0]) == 0.0
    # human ties are skipped, system ties count half
    assert pairwise_agreement([50, 50], [1, 0]) == 0.5
    assert pairwise_agreement([50, 60], [1, 1]) is None


def test_calibration_buckets() -> None:
    cal = calibration_by_bucket(
        [5, 25, 45, 65, 95, 100], [False, False, True, True, True, True], buckets=5
    )
    assert [b["n"] for b in cal] == [1, 1, 1, 1, 2]
    assert cal[0]["approval_rate"] == 0.0 and cal[4]["approval_rate"] == 1.0


def test_scoring_regression_fixture_matches_current_weights() -> None:
    """Changing weights or the formula must fail here, never silently (Phase 1 exit criterion)."""
    fixture = json.loads((ROOT / "fixtures" / "golden" / "scoring_regression_v1.json").read_text())
    weights = load_weights()
    assert fixture["weights_version"] == weights.version
    assert fixture["scoring_version"] == SCORING_VERSION
    assert len(fixture["cases"]) >= 10
    for case in fixture["cases"]:
        assert set(case["components"]) == set(COMPONENTS) and set(case["penalties"]) == set(
            PENALTIES
        )
        got = compute_score(case["components"], case["penalties"], weights)
        assert (got.raw, got.penalty, got.final) == (
            case["expected"]["raw"], case["expected"]["penalty"], case["expected"]["final"]
        ), case  # fmt: skip


SENTENCES = [f"Sentence {i} shares a concrete market observation worth quoting." for i in range(12)]


def _ranked(store: Store, audio: Path, artifacts: Path) -> tuple[str, str, list[str]]:
    store.sources.add(make_source())
    policy = make_policy(BasisType.OWNED)
    store.policies.add(policy)
    store.sources.attach_policy("S001", policy.id)
    asset = register_local_media(store, "S001", audio, artifacts_dir=artifacts, now=NOW)
    t = transcribe_media(
        store, asset.id, FakeSpeechToText(SENTENCES, pause_ms=200), artifacts_dir=artifacts, now=NOW
    )
    segment_and_store(
        store, t.id, SegmentationConfig(min_ms=4000, target_ms=6000, max_ms=9000), now=NOW
    )
    result = rank_transcript(
        store,
        t.id,
        FakeLlm(),
        load_weights(),
        RankingConfig(
            finalists_k=2, prefilter=PrefilterConfig(min_ms=1000, max_ms=60000, min_words=5)
        ),
        now=NOW,
    )
    return t.id, result.batch.id, [r.candidate_id for r in result.runs]


@requires_ffmpeg
def test_benchmark_run_and_compare(store: Store, audio_wav: Path, artifacts_dir: Path) -> None:
    tid, batch_id, ordered = _ranked(store, audio_wav, artifacts_dir)
    with pytest.raises(BenchmarkError, match="no labeled"):
        run_benchmark(store, batch_id, now=NOW)
    # humans agree with the system: top two approved (high), rest rejected
    add_label(
        store,
        ordered[0],
        reviewer="a",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        expected_performance=PerformanceBucket.HIGH,
        now=NOW,
    )
    add_label(
        store,
        ordered[1],
        reviewer="a",
        decision=LabelDecision.APPROVE,
        taxonomy=TAX,
        expected_performance=PerformanceBucket.MEDIUM,
        now=NOW,
    )
    for cid in ordered[2:]:
        add_label(
            store,
            cid,
            reviewer="a",
            decision=LabelDecision.REJECT,
            taxonomy=TAX,
            rejection_reasons=["low_novelty"],
            now=NOW,
        )
    good = run_benchmark(store, batch_id, now=NOW)
    m = good.metrics
    assert m["n_labeled"] == len(ordered) and m["precision_at_3"] == pytest.approx(2 / 3)
    assert m["pairwise_agreement"] == 1.0 and m["ndcg_at_5"] == pytest.approx(1.0)
    assert m["meets_exit_size"] is False and good.weights_version.startswith("viral_v0")
    assert store.benchmarks.get(good.id) == good and store.benchmarks.list(tid) == [good]

    # a second batch whose humans disagree -> compare flags a regression
    batch2 = rank_transcript(
        store,
        tid,
        FakeLlm(strong_boost=-0.3),
        load_weights(),
        RankingConfig(
            finalists_k=2, prefilter=PrefilterConfig(min_ms=1000, max_ms=60000, min_words=5)
        ),
        now=NOW,
    ).batch
    # relabel by a second reviewer so the majority flips for the top candidate
    add_label(
        store,
        ordered[0],
        reviewer="b",
        decision=LabelDecision.REJECT,
        taxonomy=TAX,
        rejection_reasons=["weak_hook"],
        now=NOW,
    )
    add_label(
        store,
        ordered[0],
        reviewer="c",
        decision=LabelDecision.REJECT,
        taxonomy=TAX,
        rejection_reasons=["weak_hook"],
        now=NOW,
    )
    worse = run_benchmark(store, batch2.id, now=NOW)
    cmp = compare_benchmarks(store, good.id, worse.id)
    assert cmp.same_versions and cmp.regressions and cmp.to_json()["regressed"]
    assert any(k.startswith("precision_at_") for k in cmp.deltas)
    assert compare_benchmarks(store, good.id, good.id).regressions == []
