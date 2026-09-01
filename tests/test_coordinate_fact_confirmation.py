from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tests.test_reference_confirmation import _anchor_decision, _anchor_document, _authorize_source_fact, _document
from tools.fastloop_research.v21_contract import compute_v21_structure_hash
from tools.goal_loop_v2.reference_confirmation import (
    apply_authorized_verdict,
    build_verdict_candidate,
    compute_candidate_hash,
    compute_coordinate_anchor_geometry_hash,
    compute_evidence_binding,
)


def _document_for_coordinate():
    document=_document()
    scale=next(row for row in document["source"]["anchors"] if row["id"]=="ANCHOR-SCALE");scale["status"]="source_confirmed"
    entry=next(row for row in document["source"]["anchors"] if row["id"]=="ANCHOR-ENTRY");entry["status"]="source_candidate";entry.pop("coordinate_status",None)
    document["structure_hash"]=compute_v21_structure_hash(document);return document


def _evidence(tmp_path,document):
    audit=tmp_path/"coordinate-audit.json";report=tmp_path/"REPORT.md"
    scale_ids=[row["id"] for row in document["source"]["anchors"] if row["kind"]=="scale"]
    rows=[{"id":row["id"],"kind":row["kind"],"points_px":deepcopy(row["geometry"]["points_px"]),"bounds":"pass","evidence":"pass","coordinate_status":"source_confirmed_coordinate","semantic_status":row["status"]} for row in document["source"]["anchors"] if row["kind"]!="scale"]
    audit.write_text(json.dumps({"schema":"goal-loop-v2-coordinate-anchor-audit-v1","sample_id":document["project"]["sample_id"],"excluded_from_scope":scale_ids,"anchors":rows},sort_keys=True)+"\n",encoding="utf-8")
    report.write_text("# Independent coordinate audit\n\nANCHOR-ENTRY accepted.\n",encoding="utf-8")
    paths=[audit,report];bindings=[compute_evidence_binding(path) for path in paths]
    return paths,bindings


def _decision(document,anchor_ids=None,allowed=None):
    anchor_ids=["ANCHOR-ENTRY"] if anchor_ids is None else anchor_ids;allowed=list(anchor_ids) if allowed is None else allowed
    return {"id":"DECIDE-COORDINATES","issue_id":"S02_ORIENTATION_COORDINATE_CHAIN","evidence_refs":["COORDINATE-AUDIT","COORDINATE-REPORT"],"decision":"confirm_coordinate_fact","allowed_entity_ids":allowed,"operations":[{"operation":"confirm_source_coordinate","anchor_ids":anchor_ids,"coordinate_geometry_sha256":compute_coordinate_anchor_geometry_hash(document,anchor_ids),"status":"source_confirmed_coordinate"}]}


def _candidate(document,tmp_path,decision=None):
    paths,bindings=_evidence(tmp_path,document);candidate=build_verdict_candidate(document,[row["file_sha256"] for row in bindings],[decision or _decision(document)],evidence_bindings=bindings)
    return candidate,paths


def _wrapper(candidate,verdict="authorize_exact_coordinate_fact"):
    return {"schema":"reference-confirmation-verdict-v1","candidate":candidate,"candidate_hash":candidate["candidate_hash"],"authority":"independent_reference_reviewer","verdict":verdict,"build_authorized":False}


def test_coordinate_wrapper_changes_only_coordinate_status_and_is_not_build_ready(tmp_path):
    document=_document_for_coordinate();before=deepcopy(document);candidate,paths=_candidate(document,tmp_path);result,application=apply_authorized_verdict(document,_wrapper(candidate),paths)
    assert document==before
    entry=next(row for row in result["source"]["anchors"] if row["id"]=="ANCHOR-ENTRY")
    assert entry["status"]=="source_candidate" and entry["coordinate_status"]=="source_confirmed_coordinate"
    assert "coordinate_status" not in next(row for row in result["source"]["anchors"] if row["id"]=="ANCHOR-SCALE")
    assert application["promotion_ids"]==["ANCHOR-ENTRY:coordinate"] and application["ready"] is False
    restored=deepcopy(result);restored["structure_hash"]=document["structure_hash"];next(row for row in restored["source"]["anchors"] if row["id"]=="ANCHOR-ENTRY").pop("coordinate_status")
    assert restored==document


def test_semantic_and_coordinate_wrapper_namespaces_cannot_cross(tmp_path):
    document=_document_for_coordinate();coordinate,paths=_candidate(document,tmp_path)
    with pytest.raises(ValueError,match="source-confirmed anchor operations only"):
        apply_authorized_verdict(document,_wrapper(coordinate,"authorize_exact_source_fact"),paths)

    semantic_document=_anchor_document();semantic=build_verdict_candidate(semantic_document,["b"*64],[_anchor_decision()])
    with pytest.raises(ValueError,match="coordinate operations only"):
        apply_authorized_verdict(semantic_document,_wrapper(semantic),paths)


@pytest.mark.parametrize("anchor_ids",[[],["ANCHOR-ENTRY","ANCHOR-SCALE"],["ANCHOR-ENTRY","ANCHOR-ENTRY"]])
def test_coordinate_candidate_rejects_missing_extra_scale_and_duplicate_targets(tmp_path,anchor_ids):
    document=_document_for_coordinate();paths,bindings=_evidence(tmp_path,document)
    with pytest.raises(ValueError,match="decision identity|anchor IDs invalid|exact unconfirmed|unique existing"):
        build_verdict_candidate(document,[row["file_sha256"] for row in bindings],[_decision(document,anchor_ids)],evidence_bindings=bindings)


def test_coordinate_candidate_rejects_unused_allowlist_and_stale_source(tmp_path):
    document=_document_for_coordinate();paths,bindings=_evidence(tmp_path,document)
    with pytest.raises(ValueError,match="allowlist must exactly equal"):
        build_verdict_candidate(document,[row["file_sha256"] for row in bindings],[_decision(document,allowed=["ANCHOR-ENTRY","SPACE-LIVING"])],evidence_bindings=bindings)
    candidate,_=_candidate(document,tmp_path);stale=deepcopy(document);stale["source"]["anchors"][1]["geometry"]["points_px"][0][0]+=1;stale["structure_hash"]=compute_v21_structure_hash(stale)
    with pytest.raises(ValueError,match="stale"):
        apply_authorized_verdict(stale,_wrapper(candidate),paths)


def test_coordinate_wrapper_rejects_altered_geometry_and_evidence_without_partial_mutation(tmp_path):
    document=_document_for_coordinate();candidate,paths=_candidate(document,tmp_path);before=deepcopy(document)
    paths[0].write_text(json.dumps({"schema":"coordinate-audit-v1","accepted_anchor_ids":[]})+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="evidence"):
        apply_authorized_verdict(document,_wrapper(candidate),paths)
    assert document==before

    candidate,paths=_candidate(document,tmp_path);forged=deepcopy(candidate);forged["decisions"][0]["operations"][0]["anchor_ids"]=["ANCHOR-SCALE"];forged["candidate_hash"]=compute_candidate_hash(forged)
    with pytest.raises(ValueError,match="exact unconfirmed"):
        apply_authorized_verdict(document,_wrapper(forged),paths)
    assert document==before

    original_paths,original_bindings=_evidence(tmp_path,document)
    altered=deepcopy(document);altered["source"]["anchors"][1]["geometry"]["points_px"][0][0]+=1;altered["structure_hash"]=compute_v21_structure_hash(altered)
    altered_candidate=build_verdict_candidate(altered,[row["file_sha256"] for row in original_bindings],[_decision(altered)],evidence_bindings=original_bindings)
    with pytest.raises(ValueError,match="audit geometry differs"):
        apply_authorized_verdict(altered,_wrapper(altered_candidate),original_paths)
    assert altered["source"]["anchors"][1]["status"]=="source_candidate" and "coordinate_status" not in altered["source"]["anchors"][1]


def test_coordinate_candidate_requires_byte_and_canonical_bindings(tmp_path):
    document=_document_for_coordinate();paths,bindings=_evidence(tmp_path,document);bad=deepcopy(bindings);bad[0]["canonical_sha256"]="f"*64
    candidate=build_verdict_candidate(document,[row["file_sha256"] for row in bad],[_decision(document)],evidence_bindings=bad)
    with pytest.raises(ValueError,match="canonical hashes"):
        apply_authorized_verdict(document,_wrapper(candidate),paths)


def test_coordinate_geometry_hash_is_recomputed_from_exact_source_anchors(tmp_path):
    document=_document_for_coordinate();candidate,paths=_candidate(document,tmp_path);forged=deepcopy(candidate)
    forged["decisions"][0]["operations"][0]["coordinate_geometry_sha256"]="f"*64
    forged["candidate_hash"]=compute_candidate_hash(forged)
    with pytest.raises(ValueError,match="geometry hash differs"):
        apply_authorized_verdict(document,_wrapper(forged),paths)
