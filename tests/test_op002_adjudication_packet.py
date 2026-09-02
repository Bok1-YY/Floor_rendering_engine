from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.op002_adjudication_packet import build_op002_adjudication_packet, validate_op002_adjudication_packet

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
EVIDENCE = ROOT / "reports" / "op002_vertical_evidence_20260901" / "op002-vertical-evidence.json"


def _document():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def test_op002_packet_consolidates_evidence_without_promotion():
    packet = build_op002_adjudication_packet(_document(), EVIDENCE)
    assert packet["review_pair_candidate"] == ["bedroom_01", "bedroom_corridor"]
    assert packet["geometry_findings"]["bedroom_01_isolated_by_topology_closure"] is True
    assert packet["geometry_findings"]["bedroom_corridor_present_on_other_side"] is True
    assert packet["geometry_findings"]["other_side_is_multi_anchor"] is True
    assert packet["gemini_review_status"] == "advisory_failed_or_missing"
    assert packet["pair_confirmation"] is False
    assert packet["build_authorized"] is False


def test_op002_packet_rejects_rehashed_pair_and_promotion_drift():
    import tools.goal_loop_v2.op002_adjudication_packet as module

    document = _document()
    packet = build_op002_adjudication_packet(document, EVIDENCE)
    forged = deepcopy(packet)
    forged["review_pair_candidate"] = ["wc", "kitchen"]
    forged["candidate_hash"] = module._hash({key: value for key, value in forged.items() if key != "candidate_hash"})
    with pytest.raises(ValueError, match="evidence drift"):
        validate_op002_adjudication_packet(document, EVIDENCE, forged)
    promoted = deepcopy(packet)
    promoted["pair_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_op002_adjudication_packet(document, EVIDENCE, promoted)
