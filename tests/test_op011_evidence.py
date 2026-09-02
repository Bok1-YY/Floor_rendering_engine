import json
from pathlib import Path
from tools.goal_loop_v2.build_op011_evidence import main

ROOT = Path(__file__).resolve().parents[1]

def test_op011_evidence_is_registered_and_fail_closed():
    main()
    out = ROOT / "reports/op011_geometry_evidence_20260902/op011-evidence.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["opening_id"] == "OP011"
    assert data["source_kind"] == "glazed_interface"
    assert data["registration"]["max_endpoint_error_px"] <= 1.0
    assert data["host_wall_candidates"] == []
    assert data["effective_void"] is None
    assert data["jamb_before"] is None and data["jamb_after"] is None
    assert data["side_a_space_id"] is None and data["side_b_space_id"] is None
    assert data["semantic_promotion"] is False
    assert data["build_authorized"] is False and data["ready"] is False
