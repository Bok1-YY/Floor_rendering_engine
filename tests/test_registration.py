import pytest
from tools.goal_loop_v2.registration import validate_pixel_metric_segment

M=((2.0,0.0,10.0),(0.0,-2.0,20.0),(0.0,0.0,1.0))

def test_inverse_registration_accepts_matching_axis():
    result=validate_pixel_metric_segment(M,[[1,2],[1,5]],[[12,16],[12,10]],tolerance_px=.01)
    assert result["max_endpoint_error_px"] == pytest.approx(0)

def test_inverse_registration_rejects_axis_swap():
    with pytest.raises(ValueError,match="registration mismatch"):
        validate_pixel_metric_segment(M,[[1,2],[4,2]],[[12,16],[12,10]],tolerance_px=1)
