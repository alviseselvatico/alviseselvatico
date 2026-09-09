from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.conftest import NOW, make_policy
from tests.fake_llm import score_for
from vme.domain.models import BasisType, Candidate
from vme.ranking.features import PrefilterConfig, prefilter_reason, rights_risk
from vme.ranking.schemas import COMPONENTS, PENALTIES, CandidateScore
from vme.ranking.scoring import compute_score
from vme.ranking.weights import Weights, load_weights


def test_default_weights_load_and_satisfy_contract() -> None:
    w = load_weights()
    assert w.version.startswith("viral_v0")
    assert set(w.components) == set(COMPONENTS) and set(w.penalties) == set(PENALTIES)
    assert abs(sum(w.components.values()) - 100) < 1e-9


def test_weights_file_override_and_validation(tmp_path: Path) -> None:
    base = load_weights().model_dump()
    base["version"] = "custom"
    base["components"]["hook_strength"] += 1  # sum 101
    bad = tmp_path / "w.json"
    bad.write_text(json.dumps(base))
    with pytest.raises(ValidationError, match="sum to 100"):
        load_weights(bad)
    base["components"]["hook_strength"] -= 1
    base["penalties"]["rights_risk"] = 150
    bad.write_text(json.dumps(base))
    with pytest.raises(ValidationError, match=r"within \[0, 100\]"):
        load_weights(bad)
    base["penalties"]["rights_risk"] = 10
    del base["components"]["novelty"]
    bad.write_text(json.dumps(base))
    with pytest.raises(ValidationError, match="cover exactly"):
        load_weights(bad)


def test_score_math_matches_contract() -> None:
    w = Weights(
        version="t",
        components=dict.fromkeys(COMPONENTS, 100 / len(COMPONENTS)),
        penalties=dict.fromkeys(PENALTIES, 10.0),
    )
    all_one = dict.fromkeys(COMPONENTS, 1.0)
    no_risk = dict.fromkeys(PENALTIES, 0.0)
    assert compute_score(all_one, no_risk, w).final == 100.0
    half = dict.fromkeys(COMPONENTS, 0.5)
    b = compute_score(half, dict.fromkeys(PENALTIES, 0.5), w)
    assert b.raw == 50.0 and b.penalty == 25.0 and b.final == 25.0
    # clamp at zero, never negative
    assert (
        compute_score(dict.fromkeys(COMPONENTS, 0.0), dict.fromkeys(PENALTIES, 1.0), w).final == 0.0
    )
    with pytest.raises(ValueError, match="outside"):
        compute_score({**all_one, "novelty": 1.5}, no_risk, w)
    with pytest.raises(ValueError, match="missing penalties"):
        compute_score(all_one, {}, w)


def test_candidate_score_schema_rejects_out_of_range_and_extra() -> None:
    good = score_for("hello world")
    assert set(good.components()) == set(COMPONENTS)
    data = good.model_dump()
    data["hook_strength"] = 1.2
    with pytest.raises(ValidationError, match="hook_strength"):
        CandidateScore.model_validate(data)
    data = good.model_dump()
    data["final_score"] = 90  # the LLM must not output the aggregate
    with pytest.raises(ValidationError):
        CandidateScore.model_validate(data)
    data = good.model_dump()
    data["recommended_end_ms"] = 0
    with pytest.raises(ValidationError, match="recommended_end_ms"):
        CandidateScore.model_validate(data)
    schema = CandidateScore.model_json_schema()
    assert "rights_risk" not in schema["properties"]  # never asked to the LLM
    assert "minimum" not in json.dumps(schema)  # plain schema, ranges validated in code


def test_rights_risk_is_derived_from_policy() -> None:
    assert rights_risk(make_policy(BasisType.OWNED)) == 0.0
    assert rights_risk(make_policy(BasisType.PUBLIC_DOMAIN_VERIFIED)) == 0.1
    assert rights_risk(make_policy(BasisType.TRANSFORMATIVE_REVIEW_REQUIRED)) == 0.6
    assert rights_risk(make_policy(BasisType.UNKNOWN)) == 1.0


def _cand(duration_ms: int, words: int) -> Candidate:
    return Candidate(
        id="c",
        transcript_id="t",
        start_ms=0,
        end_ms=duration_ms,
        candidate_text=" ".join(["w"] * words),
        created_by="test",
        created_at=NOW,
    )


def test_prefilter_reasons() -> None:
    cfg = PrefilterConfig(min_ms=8000, max_ms=90000, min_words=15)
    assert prefilter_reason(_cand(20000, 40), cfg) is None
    assert prefilter_reason(_cand(5000, 40), cfg).startswith("too_short")  # type: ignore[union-attr]
    assert prefilter_reason(_cand(100000, 40), cfg).startswith("too_long")  # type: ignore[union-attr]
    assert prefilter_reason(_cand(20000, 5), cfg).startswith("too_few_words")  # type: ignore[union-attr]
