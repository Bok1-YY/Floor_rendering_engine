# -*- coding: utf-8 -*-
import json

from Floor_engine_server import api, cinematic_planner
from Floor_engine_server.models import TaskParams, task_params_to_kwargs
from Floor_engine_server.prompts import save_task_files_html


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_supported_workflows_are_only_new_scene_modes():
    assert cinematic_planner.supports_cinematic("纯效果图 (生成全新空间)")
    assert cinematic_planner.supports_cinematic("宠物友好 (动物独处/主宠互动)")
    assert cinematic_planner.supports_cinematic("参照模式 (风格参照图生新图)")
    assert cinematic_planner.supports_cinematic("Omakase (AI 代笔场景)")
    assert not cinematic_planner.supports_cinematic("地板替换 (保持原图换地板)")
    assert not cinematic_planner.supports_cinematic("墙板模式 (护墙板)")
    assert not cinematic_planner.supports_cinematic("自由创作 (自定义提示词/多图)")


def test_context_excludes_floor_physical_specifications():
    params = TaskParams(
        workflow_mode="宠物友好 (动物独处/主宠互动)",
        model_choice="Pro",
        image_path="x.png",
        floor_tone="深胡桃色",
        floor_size="180 x 1220 mm",
        seam_type="常规倒角缝",
        glossiness="高光",
        pet_type="金毛犬",
        pet_action="主宠互动",
        avoid_items=["任何人物出镜", "任何宠物出镜", "任何地毯"],
    )
    context = cinematic_planner.build_cinematic_context(params, "warm reference")

    assert context["pet_type"] == "金毛犬"
    assert context["reference_style_notes"] == "warm reference"
    assert "floor_tone" not in context
    assert "floor_size" not in context
    assert "seam_type" not in context
    assert "glossiness" not in context
    assert context["avoid_items"] == ["任何地毯"]


def test_planner_uses_structured_output_and_returns_direction(monkeypatch):
    captured = {}
    raw_plan = {
        "situation": "The dog expects the child to throw a toy.",
        "camera_position": "Observer at seated eye height beside the doorway.",
        "visual_flow": "Floor boards lead toward the dog and then the child.",
        "action": "The dog is shifting its weight before moving.",
        "practical_light": "A single side window.",
        "color_thesis": "Warm wood and soft neutral textiles.",
        "realism_constraints": ["no posing", "natural contact shadows"],
        "final_direction": (
            "A physically plausible low observer position catches the dog shifting its weight "
            "toward the child, with natural contact shadows and a broad visible floor foreground."
        ),
    }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        content = json.dumps(raw_plan)
        return FakeResponse(payload={
            "candidates": [{"content": {"parts": [{"text": content}]}}],
        })

    monkeypatch.setattr(cinematic_planner, "load_config", lambda: {"tls_verify": True})
    monkeypatch.setattr(cinematic_planner.requests, "post", fake_post)
    params = TaskParams(
        workflow_mode="宠物友好 (动物独处/主宠互动)",
        model_choice="Pro",
        image_path="x.png",
        pet_type="金毛犬",
        pet_action="主宠互动",
    )

    direction, plan, err = cinematic_planner.plan_cinematic_scene(
        params,
        api_key="secret",
        model="gemini-3.6-flash",
    )

    assert err is None
    assert direction == raw_plan["final_direction"]
    assert plan["camera_position"].startswith("Observer")
    assert captured["url"].endswith("/gemini-3.6-flash:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "secret"
    generation = captured["json"]["generationConfig"]
    assert "temperature" not in generation
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseSchema"]["required"][-1] == "final_direction"


def test_planner_failure_is_returned_without_exception(monkeypatch):
    monkeypatch.setattr(cinematic_planner, "load_config", lambda: {})
    monkeypatch.setattr(
        cinematic_planner.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=503, text="temporarily unavailable"),
    )
    params = {
        "workflow_mode": "纯效果图 (生成全新空间)",
        "avoid_items": [],
    }
    direction, plan, err = cinematic_planner.plan_cinematic_scene(
        params,
        api_key="secret",
        model="gemini-3.6-flash",
    )
    assert direction == ""
    assert plan == {}
    assert "HTTP 503" in err


def test_image_thinking_is_high_only_for_cinematic_b2():
    assert api._image_thinking_config(
        "gemini-3.1-flash-image",
        cinematic_mode=True,
        include_thoughts=False,
    ) == {"thinkingLevel": "HIGH"}
    assert api._image_thinking_config(
        "gemini-3.1-flash-lite-image",
        cinematic_mode=True,
        include_thoughts=False,
    ) == {}
    assert api._image_thinking_config(
        "gemini-3-pro-image",
        cinematic_mode=True,
        include_thoughts=True,
    ) == {"includeThoughts": True}


def test_cinematic_prompt_is_inserted_before_floor_constraints(swatch_image):
    plan_text = (
        "Observe the room from a believable seated height as the dog pauses mid-step; "
        "use side-window light, natural contact shadows and restrained optical softness."
    )
    params = TaskParams(
        workflow_mode="宠物友好 (动物独处/主宠互动)",
        model_choice="Pro",
        image_path=swatch_image,
        pet_type="金毛犬",
        pet_action="宠物独处",
        cinematic_enabled=True,
        cinematic_plan_text=plan_text,
    )
    combined = save_task_files_html(**task_params_to_kwargs(params))[2]

    assert "**[CINEMATIC REALISM — DIRECTOR PLAN]**" in combined
    assert plan_text in combined
    assert combined.index(plan_text) < combined.index("**[FLOOR COLOR — MANDATORY EXACT MATCH]**")


def test_replacement_ignores_cinematic_flag(swatch_image):
    params = TaskParams(
        workflow_mode="地板替换 (保持原图换地板)",
        model_choice="Pro",
        image_path=swatch_image,
        cinematic_enabled=True,
        cinematic_plan_text="This must never be inserted into replacement mode.",
    )
    combined = save_task_files_html(**task_params_to_kwargs(params))[2]

    assert "CINEMATIC REALISM" not in combined
    assert "This must never be inserted" not in combined


def test_pet_workflow_removes_conflicting_default_avoid_items(swatch_image):
    interactive = TaskParams(
        workflow_mode="宠物友好 (动物独处/主宠互动)",
        model_choice="Pro",
        image_path=swatch_image,
        pet_type="金毛犬",
        pet_action="主宠互动",
        avoid_items=["任何人物出镜", "任何宠物出镜", "任何地毯"],
    )
    interactive_prompt = save_task_files_html(**task_params_to_kwargs(interactive))[2]

    assert "any people or human figures" not in interactive_prompt
    assert "any animals or pets" not in interactive_prompt
    assert "rugs, carpets of any size or style" in interactive_prompt

    pet_only = TaskParams(
        workflow_mode="宠物友好 (动物独处/主宠互动)",
        model_choice="Pro",
        image_path=swatch_image,
        pet_type="金毛犬",
        pet_action="宠物独处",
        avoid_items=["任何人物出镜", "任何宠物出镜"],
    )
    pet_only_prompt = save_task_files_html(**task_params_to_kwargs(pet_only))[2]

    assert "any people or human figures" in pet_only_prompt
    assert "any animals or pets" not in pet_only_prompt
