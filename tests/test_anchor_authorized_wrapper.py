from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_reference_confirmation import (
    _anchor_decision,
    _anchor_document,
    _authorize,
    _candidate,
)
from tools.goal_loop_v2.reference_confirmation import (
    apply_authorized_verdict,
    build_verdict_candidate,
    compute_candidate_hash,
)


def _source_fact_wrapper(document=None, status="source_confirmed"):
    document = document or _anchor_document()
    candidate = build_verdict_candidate(document, ["b" * 64], [_anchor_decision(status)])
    return document, {
        "schema": "reference-confirmation-verdict-v1",
        "candidate": candidate,
        "candidate_hash": candidate["candidate_hash"],
        "authority": "independent_reference_reviewer",
        "verdict": "authorize_exact_source_fact",
        "build_authorized": False,
    }


def test_exact_source_fact_wrapper_promotes_only_bound_anchor_and_stays_not_ready():
    document, wrapper = _source_fact_wrapper()
    before = deepcopy(document)
    result, report = apply_authorized_verdict(document, wrapper)
    assert document == before
    assert next(row for row in result["source"]["anchors"] if row["id"] == "ANCHOR-SCALE")["status"] == "source_confirmed"
    assert report["promotion_ids"] == ["ANCHOR-SCALE"]
    assert report["ready"] is False
    restored = deepcopy(result)
    restored["structure_hash"] = document["structure_hash"]
    next(row for row in restored["source"]["anchors"] if row["id"] == "ANCHOR-SCALE")["status"] = "source_candidate"
    assert restored == document


def test_geometry_and_source_fact_wrapper_permissions_do_not_cross():
    document, source_wrapper = _source_fact_wrapper()
    geometry_wrapper = deepcopy(source_wrapper)
    geometry_wrapper["verdict"] = "authorize_exact_reference_geometry"
    with pytest.raises(ValueError, match="cannot authorize source facts"):
        apply_authorized_verdict(document, geometry_wrapper)

    geometry_document = _anchor_document()
    geometry_candidate = _candidate(geometry_document)
    source_wrapper = _authorize(geometry_candidate)
    source_wrapper["verdict"] = "authorize_exact_source_fact"
    with pytest.raises(ValueError, match="source-confirmed anchor operations only"):
        apply_authorized_verdict(geometry_document, source_wrapper)


def test_source_fact_wrapper_rejects_human_status_even_if_candidate_schema_allows_it():
    document, wrapper = _source_fact_wrapper(status="human_confirmed")
    with pytest.raises(ValueError, match="source-confirmed anchor operations only"):
        apply_authorized_verdict(document, wrapper)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "evil"),
        ("authority", "builder"),
        ("verdict", "authorize_build"),
        ("build_authorized", True),
    ],
)
def test_source_fact_wrapper_exact_fields_are_fail_closed(field, value):
    document, wrapper = _source_fact_wrapper()
    wrapper[field] = value
    before = deepcopy(document)
    with pytest.raises(ValueError, match="invalid independently authorized"):
        apply_authorized_verdict(document, wrapper)
    assert document == before


def test_source_fact_wrapper_rejects_candidate_and_wrapper_hash_tampering_without_partial_mutation():
    document, wrapper = _source_fact_wrapper()
    before = deepcopy(document)
    wrapper["candidate"]["decisions"][0]["operations"][0]["anchor_id"] = "ANCHOR-ENTRY"
    with pytest.raises(ValueError, match="candidate hash drift"):
        apply_authorized_verdict(document, wrapper)
    assert document == before

    document, wrapper = _source_fact_wrapper()
    before = deepcopy(document)
    wrapper["candidate_hash"] = "f" * 64
    with pytest.raises(ValueError, match="authorized candidate hash mismatch"):
        apply_authorized_verdict(document, wrapper)
    assert document == before


def test_rehashed_candidate_with_semantic_operation_cannot_cross_source_fact_gate():
    document, wrapper = _source_fact_wrapper()
    candidate = wrapper["candidate"]
    candidate["decisions"][0]["decision"] = "confirm_geometry"
    candidate["decisions"][0]["issue_id"] = "ISSUE-GEOMETRY"
    candidate["candidate_hash"] = compute_candidate_hash(candidate)
    wrapper["candidate_hash"] = candidate["candidate_hash"]
    with pytest.raises(ValueError, match="requires source-fact|source-confirmed anchor operations only"):
        apply_authorized_verdict(document, wrapper)


def test_rehashed_source_fact_allowlist_cannot_include_unused_semantic_entity():
    document, wrapper = _source_fact_wrapper()
    candidate = wrapper["candidate"]
    candidate["decisions"][0]["allowed_entity_ids"].append("SPACE-LIVING")
    candidate["candidate_hash"] = compute_candidate_hash(candidate)
    wrapper["candidate_hash"] = candidate["candidate_hash"]
    with pytest.raises(ValueError, match="allowlist must exactly equal"):
        apply_authorized_verdict(document, wrapper)
