import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from Floor_engine_server.local_floor_semantics import (
    MODEL_PATH,
    floor_semantic_model_status,
    predict_floor_semantics,
)


@pytest.mark.skipif(not os.path.isfile(MODEL_PATH), reason="bundled CLIPSeg model missing")
def test_bundled_clipseg_runs_offline_and_returns_floor_probability():
    image = Image.new("RGB", (480, 320), (232, 230, 226))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 150, 479, 319), fill=(166, 129, 91))
    draw.rectangle((160, 80, 320, 260), fill=(45, 45, 48))

    result = predict_floor_semantics(image)

    assert floor_semantic_model_status()["available"] is True
    assert result.status == "ok"
    assert result.probability is not None
    assert result.probability.shape == (320, 480)
    assert 0.0 <= float(result.probability.min()) <= float(result.probability.max()) <= 1.0
    assert result.probability[290, 80] > result.probability[80, 80]
    assert result.probability[290, 80] > result.probability[190, 240]
