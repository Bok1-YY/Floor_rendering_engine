from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_registered_gap_review_bundle as module


def _rehash(value):
    value["candidate_hash"] = hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != "candidate_hash"})).hexdigest()


def test_current_registered_reviews_accept_nine_and_derive_scale_effect():
    result = module.build()
    assert result["accepted_for_isolated_xy_variant"] == ["OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010"]
    assert result["op001_scale_registration_effect_demonstrated"] is True
    assert result["selected_review_cost_usd"] == pytest.approx(0.0051597)
    assert result["historical_op001_v2"]["parsed"]["recommendation"] == "reject_xy_variant"
    assert all(row["parsed"]["recommendation"] == "accept_xy_variant" for row in result["rows"])


@pytest.mark.parametrize("attack", ["parsed", "image", "metric", "raw", "acceptance", "scale", "promotion"])
def test_result_and_bundle_attacks_fail(tmp_path, attack):
    if attack in {"parsed", "image", "metric", "raw"}:
        base = tmp_path / "results"
        for opening_id in module.EXPECTED_INCLUDED:
            target = base / opening_id / "result.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(module.BASE / opening_id / "result.json", target)
        target = base / "OP004/result.json"
        value = json.loads(target.read_text(encoding="utf-8"))
        if attack == "parsed":
            value["parsed"] = {}
        elif attack == "image":
            value["image_bindings"].reverse()
        elif attack == "metric":
            value["metric_window"]["ortho_scale_m"] = 99
        else:
            value["raw_response_sha256"] = "0" * 64
        target.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            module.build(base=base)
        return
    result = deepcopy(module.build())
    if attack == "acceptance":
        result["accepted_for_isolated_xy_variant"].append("OP011")
    elif attack == "scale":
        result["op001_scale_registration_effect_demonstrated"] = False
    else:
        result["build_authorized"] = True
    _rehash(result)
    with pytest.raises(ValueError):
        module.validate(result)
