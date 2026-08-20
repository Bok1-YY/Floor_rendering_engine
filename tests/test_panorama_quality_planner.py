# -*- coding: utf-8 -*-
import json

import pytest
from PIL import Image

from Floor_engine_server import panorama_quality_planner as planner
from Floor_engine_server.pure_render_pano_atlas import (
    create_direct_paid_preview,
    validate_direct_paid_preview,
)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, raw):
        self.raw = raw

    def json(self):
        return {
            "candidates": [{"content": {"parts": [{"text": json.dumps(self.raw)}]}}],
        }


def _raw_plan(route: str) -> dict:
    if route == planner.PERSPECTIVE_ROUTE:
        sector_ids = [
            "front", "front-right", "right", "back-right",
            "back", "back-left", "left", "front-left",
        ]
        sectors = [{
            "id": value,
            "label": value,
            "contract": f"Keep {value} continuous with its adjacent sectors and the same room shell.",
        } for value in sector_ids]
        faces = []
    else:
        sectors = []
        faces = [{
            "id": value,
            "label": value,
            "contract": f"Keep {value} rectilinear and continuous along all adjacent cube edges.",
        } for value in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")]
    return {
        "camera_contract": "相机保持单一水平球心，建筑竖线在自然视角中始终笔直且不鼓曲。",
        "room_shell": "建立同一尺度、同一时刻的正交房间壳体，未知区域只做可信延伸。",
        "spatial_contract": "门窗、家具和通道都有固定世界位置，并能被所有相邻方向共同解释。",
        "sector_contract": sectors,
        "cube_face_contract": faces,
        "object_registry": [{
            "id": "sofa-1", "identity": "唯一主沙发", "location": "正前偏右",
            "visibility": "跨相邻方向时保持相同轮廓，不复制或截断",
        }],
        "floor_plane_contract": "Make the floor dark walnut with glossy wide planks.",
        "pole_and_seam_contract": "天顶、天底均完整，左右环缝放在低复杂度墙面并连续闭合。",
        "lighting_contract": "同一主光方向、曝光、色温和阴影逻辑连续覆盖所有方向。",
        "risk_flags": ["侧面墙体可能鼓曲", "沙发可能在相邻方向重复"],
        "final_direction": (
            "Use one level optical centre and one coherent room shell. Keep all walls straight and every "
            "object unique across adjacent views. Make the floor dark walnut with glossy wide planks. "
            "Maintain continuous exposure, lighting, poles and longitude seam around the sphere."
        ),
    }


@pytest.mark.parametrize("route", [planner.PERSPECTIVE_ROUTE, planner.DIRECT_ROUTE])
def test_multimodal_planner_returns_hashed_route_contract_and_protects_floor(
        tmp_path, monkeypatch, route):
    source = tmp_path / f"{route}.png"
    Image.new("RGB", (96, 64), "tan").save(source)
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(_raw_plan(route))

    planner._clear_plan_cache_for_tests()
    monkeypatch.setattr(planner, "load_config", lambda: {"tls_verify": True})
    monkeypatch.setattr(planner.requests, "post", fake_post)
    plan = planner.plan_panorama_quality(
        route=route, source_path=str(source), source_hash="a" * 64,
        params={"room_type": "客厅", "floor_tone": "不应暴露给规划上下文"},
        api_key="secret", model="gemini-3.6-flash",
    )

    assert plan["status"] == "planned"
    assert plan["planner_call_count"] == 1
    assert planner.validate_quality_plan(plan)
    assert "dark walnut" not in plan["final_direction"]
    assert "deterministic spherical floor projection" in plan["final_direction"]
    assert plan["validation"]["floor_material_rewrite"] is False
    assert captured["url"].endswith("/gemini-3.6-flash:generateContent")
    parts = captured["json"]["contents"][0]["parts"]
    assert any("inlineData" in part for part in parts)
    context = next(part["text"] for part in parts if part.get("text", "").startswith("{"))
    assert "floor_tone" not in context

    cached = planner.plan_panorama_quality(
        route=route, source_path=str(source), source_hash="a" * 64,
        params={"room_type": "客厅", "floor_tone": "不应暴露给规划上下文"},
        api_key="secret", model="gemini-3.6-flash",
    )
    assert cached["cache_hit"] is True
    assert cached["planner_call_count"] == 0
    assert cached["plan_hash"] == plan["plan_hash"]


def test_missing_gemini_key_uses_visible_versioned_fallback(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "gray").save(source)
    planner._clear_plan_cache_for_tests()

    plan = planner.plan_panorama_quality(
        route=planner.PERSPECTIVE_ROUTE, source_path=str(source),
        source_hash="b" * 64, params={}, api_key="", model="gemini-3.6-flash",
    )

    assert plan["status"] == "local_fallback"
    assert plan["planner_call_count"] == 0
    assert "Gemini API Key" in plan["error"]
    assert len(plan["sector_contract"]) == 8
    assert planner.validate_quality_plan(plan)


def test_direct_preview_uses_one_shared_plan_for_both_engines_and_rejects_tampering(tmp_path):
    source = tmp_path / "floor.png"
    Image.new("RGB", (32, 32), "tan").save(source)
    plan = planner.local_quality_plan(
        route=planner.DIRECT_ROUTE, source_hash="c" * 64,
        params={"room_type": "客厅"}, reason="test",
    )
    engines = [{
        "key": "b2_atlas", "label": "B2", "provider": "fal",
        "endpoint": "fal-ai/nano-banana-2/edit", "model_id": "b2",
    }, {
        "key": "gpt_atlas", "label": "GPT", "provider": "fal",
        "endpoint": "openai/gpt-image-2/edit", "model_id": "gpt-image-2",
    }]
    row = create_direct_paid_preview(
        source_path=str(source), source_hash="c" * 64,
        params={"workflow_mode": "球面效果图", "room_type": "客厅"},
        engines=engines, estimated_costs={}, quality_plan=plan, now=100,
    )

    assert row["quality_plan_hash"] == plan["plan_hash"]
    assert all(plan["plan_hash"][:16] in prompt for prompt in row["prompts"].values())
    assert all("SILENT INTERNAL PREFLIGHT" in prompt for prompt in row["prompts"].values())
    validate_direct_paid_preview(
        row, preview_hash=row["preview_hash"], source_hash="c" * 64, now=101)

    row["quality_plan"]["final_direction"] += " tampered"
    with pytest.raises(ValueError, match="tampered"):
        validate_direct_paid_preview(
            row, preview_hash=row["preview_hash"], source_hash="c" * 64, now=101)


def test_repair_prompt_reuses_plan_and_includes_actual_failed_yaw():
    plan = planner.local_quality_plan(
        route=planner.PERSPECTIVE_ROUTE, source_hash="d" * 64, params={}, reason="test")
    prompt = planner.compile_panorama_repair_prompt(
        "Repair only the mask.", plan,
        {"failures": ["architecture_views"], "checks": [{
            "check_id": "architecture_view", "status": "fail",
            "view_id": "right", "yaw_deg": 90, "value": 7.2, "threshold": 4.0,
        }]},
        "architecture",
    )

    assert "yaw_deg=90" in prompt
    assert "value=7.2" in prompt
    assert plan["plan_hash"][:16] in prompt
    assert "Preserve every unmasked pixel" in prompt
