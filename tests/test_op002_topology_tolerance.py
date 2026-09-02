from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.op002_topology_tolerance import build_op002_topology_tolerance, validate_op002_topology_tolerance

ROOT = Path(__file__).resolve().parents[1]


def _document():
    path = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_topology_tolerance_is_source_neutral_and_fail_closed():
    candidate = build_op002_topology_tolerance(_document())
    assert candidate["selected_clearance_m"] == 1e-6
    assert candidate["source_dimension_effect"] == "none"
    assert candidate["score_effect"] == "none"
    assert candidate["stable_topology"]["side_probes_same_face"] is True
    assert candidate["source_geometry_confirmation"] is False
    assert candidate["build_authorized"] is False


def test_topology_tolerance_rejects_policy_and_promotion_drift():
    document = _document()
    candidate = build_op002_topology_tolerance(document)
    forged = deepcopy(candidate)
    forged["selected_clearance_m"] = 0.001
    with pytest.raises(ValueError, match="policy drift"):
        validate_op002_topology_tolerance(document, forged)
    promoted = deepcopy(candidate)
    promoted["cut_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_op002_topology_tolerance(document, promoted)
