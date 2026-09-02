from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.op002_adjudication_packet_v2 import build_op002_adjudication_packet_v2, validate_op002_adjudication_packet_v2

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
EVIDENCE = ROOT / "reports" / "op002_vertical_evidence_20260901" / "op002-vertical-evidence.json"


def _document(): return json.loads(SOURCE.read_text(encoding="utf-8"))


def _gemini_result(tmp_path):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    value = {
        "http_status": 200,
        "model": "gemini-3.6-flash",
        "image_sha256": [evidence["artifacts"]["full_overlay"]["sha256"], evidence["artifacts"]["crop_overlay"]["sha256"]],
        "parsed_result": {"opening_id":"OP002","geometry_agreement":"agree","observed_kind":"hinged_door","pair_agreement":"agree","traversable":"yes","complete":True},
        "failure": None,
    }
    path = tmp_path / "result.json"; path.write_text(json.dumps(value), encoding="utf-8"); return path


def test_v2_packet_binds_complete_gemini_but_stays_unresolved(tmp_path):
    result = _gemini_result(tmp_path)
    packet = build_op002_adjudication_packet_v2(_document(), EVIDENCE, result)
    assert packet["gemini_review_status"] == "complete_agree"
    assert "GEMINI_COMPLETE_REVIEW_MISSING" not in packet["remaining_blockers"]
    assert packet["remaining_blockers"] == ["SOURCE_OPENING_STATUS_CANDIDATE","OTHER_SIDE_FACE_MULTI_ANCHOR","ROOM_POLYGONS_NOT_SOURCE_CONFIRMED","HUMAN_REVIEW_PENDING"]
    assert packet["pair_confirmation"] is False and packet["build_authorized"] is False


def test_v2_packet_rejects_forged_review_and_promotion(tmp_path):
    import tools.goal_loop_v2.op002_adjudication_packet_v2 as module
    result = _gemini_result(tmp_path); document = _document()
    packet = build_op002_adjudication_packet_v2(document, EVIDENCE, result)
    forged = deepcopy(packet); forged["gemini_result"]["pair_agreement"] = "conflict"; forged["candidate_hash"] = module._hash({k:v for k,v in forged.items() if k!="candidate_hash"})
    with pytest.raises(ValueError, match="evidence drift"): validate_op002_adjudication_packet_v2(document, EVIDENCE, result, forged)
    promoted = deepcopy(packet); promoted["pair_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"): validate_op002_adjudication_packet_v2(document, EVIDENCE, result, promoted)
