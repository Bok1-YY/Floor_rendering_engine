import pytest
from tools.goal_loop_v2.registration import validate_pixel_metric_segment
from tools.goal_loop_v2.registration_repair import build_registration_repair_manifest, validate_repair_manifest

M=((2.0,0.0,10.0),(0.0,-2.0,20.0),(0.0,0.0,1.0))

def test_inverse_registration_accepts_matching_axis():
    result=validate_pixel_metric_segment(M,[[1,2],[1,5]],[[12,16],[12,10]],tolerance_px=.01)
    assert result["max_endpoint_error_px"] == pytest.approx(0)

def test_inverse_registration_rejects_axis_swap():
    with pytest.raises(ValueError,match="registration mismatch"):
        validate_pixel_metric_segment(M,[[1,2],[4,2]],[[12,16],[12,10]],tolerance_px=1)

def test_repair_manifest_rejects_stale_pixel_packet_without_mutating_source():
    m = build_registration_repair_manifest(
        opening_id="OP002", source_document_sha256="a"*64, source_structure_hash="b"*64,
        metric_segment_m=[[4.42,10.69],[4.42,9.80]], pixel_packet_sha256="c"*64,
        pixel_segment=[[965,960],[1098,960]], expected_pixel_segment=[[1101,912],[1101,1034]],
        max_endpoint_error_px=144.694,
    )
    validate_repair_manifest(m)
    assert m["disposition"] == "pixel_evidence_rejected_stale_or_wrong_frame"
    assert m["governing_geometry"] == "metric_source_contract"
    assert not m["source_mutation_authorized"] and not m["build_authorized"]

def test_repair_manifest_rejects_authorization_escalation():
    m = build_registration_repair_manifest(
        opening_id="OP002", source_document_sha256="a"*64, source_structure_hash="b"*64,
        metric_segment_m=[[0,0],[1,1]], pixel_packet_sha256="c"*64,
        pixel_segment=[[0,0],[1,1]], expected_pixel_segment=[[0,0],[1,1]], max_endpoint_error_px=0,
    )
    m["build_authorized"] = True
    with pytest.raises(ValueError, match="cannot authorize"):
        validate_repair_manifest(m)
