import copy

import pytest

from Floor_engine_server import whole_home_engine
from Floor_engine_server.whole_home_professional import (
    ProfessionalContractError,
    build_floorplan_graph,
    build_marketing_proposal,
    confirm_construction_profile,
    default_construction_profile,
    generate_scene_recipe,
    modern_warm_natural_style_pack,
    professional_capabilities,
    review_scene_recipe,
    validate_floorplan_graph,
)


def _project(*, verified=True):
    walls = [
        {"id": "w1", "start": {"x": 0, "z": 0}, "end": {"x": 10, "z": 0},
         "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
        {"id": "w2", "start": {"x": 10, "z": 0}, "end": {"x": 10, "z": 8},
         "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
        {"id": "w3", "start": {"x": 10, "z": 8}, "end": {"x": 0, "z": 8},
         "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
        {"id": "w4", "start": {"x": 0, "z": 8}, "end": {"x": 0, "z": 0},
         "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
        {"id": "w5", "start": {"x": 6, "z": 0}, "end": {"x": 6, "z": 8},
         "thickness_m": .12, "height_m": 2.8, "kind": "interior", "source": "human", "confidence": 1},
    ]
    rooms = [
        {"id": "living", "label": "客厅", "room_type": "living_room", "selected": True,
         "semantic_status": "complete", "source": "human", "confidence": 1,
         "polygon": [{"x": .2, "z": .2}, {"x": 5.8, "z": .2}, {"x": 5.8, "z": 7.8}, {"x": .2, "z": 7.8}],
         "area_m2": 42.56},
        {"id": "bedroom", "label": "主卧", "room_type": "primary_bedroom", "selected": True,
         "semantic_status": "complete", "source": "human", "confidence": 1,
         "polygon": [{"x": 6.2, "z": .2}, {"x": 9.8, "z": .2}, {"x": 9.8, "z": 7.8}, {"x": 6.2, "z": 7.8}],
         "area_m2": 27.36},
    ]
    return {
        "project_id": "home-professional", "revision": 4, "source_type": "floorplan",
        "input_grade": "raster_human_locked", "verified": verified,
        "source_registration": {
            "source_hash": "1" * 64, "registration_hash": "2" * 64,
        },
        "model": {
            "coordinate_system": "metres-y-up", "width_m": 10, "depth_m": 8,
            "wall_height_m": 2.8, "walls": walls, "rooms": rooms, "openings": [],
            "uncertainties": [], "input_grade": "raster_human_locked",
            "model_facts_hash": "3" * 64,
        },
    }


def _profile(project):
    return confirm_construction_profile(project, {
        "wall_height_m": 2.8,
        "interior_door_height_m": 2.1,
        "window_sill_height_m": .9,
        "window_head_height_m": 2.1,
        "floor_finish_thickness_m": .015,
        "ceiling_drop_m": .08,
        "skirting_height_m": .08,
    }, reviewer="sales-1", now=10)


def test_floorplan_graph_is_render_independent_and_hash_locked():
    project = _project()
    project["captures"] = [{"capture_id": "one"}]
    first = build_floorplan_graph(project)
    project["captures"] = [{"capture_id": "two"}]
    second = build_floorplan_graph(project)
    assert first == second
    assert first["review"]["status"] == "locked"
    assert first["topdown_camera_contract"] == {
        "position_axis": "+Y", "view_direction": "-Y", "screen_up": "-Z",
    }
    tampered = copy.deepcopy(first)
    tampered["walls"][0]["end"]["x"] += 1
    with pytest.raises(ProfessionalContractError) as error:
        validate_floorplan_graph(tampered)
    assert error.value.code == "floorplan_graph_hash_mismatch"


def test_construction_profile_requires_every_vertical_assumption():
    project = _project()
    draft = default_construction_profile(project)
    assert draft["status"] == "assumptions_pending"
    assert all(not row["confirmed"] for row in draft["fields"].values())
    with pytest.raises(ProfessionalContractError) as error:
        confirm_construction_profile(project, {"wall_height_m": 2.8}, reviewer="sales")
    assert error.value.code == "construction_profile_fields_missing"
    confirmed = _profile(project)
    assert confirmed["status"] == "confirmed"
    assert confirmed["reviewer"] == "sales-1"


@pytest.mark.parametrize("variant_index", [1, 2, 3])
def test_scene_recipe_candidates_are_deterministic_and_audited(variant_index):
    project = _project()
    profile = _profile(project)
    first = generate_scene_recipe(project, profile, variant_index=variant_index, now=20)
    second = generate_scene_recipe(project, profile, variant_index=variant_index, now=20)
    assert first == second
    assert first["style_pack_id"] == "modern_warm_natural_v1"
    assert first["instances"]
    assert first["quality"]["status"] == "passed", first["quality"]
    assert first["recipe_hash"]
    assert first["scene_hash"]


def test_scene_recipe_cannot_lock_before_geometry_or_profile_confirmation():
    project = _project(verified=False)
    profile = _profile(project)
    recipe = generate_scene_recipe(project, profile, now=20)
    with pytest.raises(ProfessionalContractError) as error:
        review_scene_recipe(recipe, reviewer="sales", note="looks good", lock=True,
                            project_verified=False, construction_confirmed=True, now=30)
    assert error.value.code == "geometry_not_verified"
    reviewed = review_scene_recipe(recipe, reviewer="sales", note="looks good", lock=False,
                                   project_verified=False, construction_confirmed=True, now=30)
    assert reviewed["status"] == "reviewed"


def test_marketing_proposal_never_claims_delivery_before_three_certified_panos():
    project = _project()
    recipe = generate_scene_recipe(project, _profile(project), now=20)
    recipe = review_scene_recipe(recipe, reviewer="sales", note="approved", lock=True,
                                 project_verified=True, construction_confirmed=True, now=30)
    project["pano_captures"] = [
        {"capture_id": f"pano-{index}", "gate": {"gate_pass": True},
         "review": {"passed": True}, "scene_hash": recipe["scene_hash"]}
        for index in range(1, 4)
    ]
    proposal = build_marketing_proposal(project, recipe)
    assert proposal["status"] == "ready"
    assert len(proposal["deliverables"]["certified_master_panoramas"]) == 3
    assert any("不是施工图" in row for row in proposal["disclaimers"])
    project["pano_captures"][0]["scene_hash"] = "old-scene"
    stale_mix = build_marketing_proposal(project, recipe)
    assert stale_mix["status"] == "draft"
    assert stale_mix["blockers"] == ["certified_panorama_count_below_3"]


def test_capabilities_and_style_pack_publish_narrow_product_scope():
    capabilities = professional_capabilities()
    style = modern_warm_natural_style_pack()
    assert capabilities["product_mode"] == "raster_first_renovation_sales_proposal"
    assert capabilities["construction_or_pricing_authority"] is False
    assert capabilities["advanced_inputs"] == ["dwg", "dxf"]
    assert style["style_pack_id"] == "modern_warm_natural_v1"
    assert all(asset["license"] for asset in style["assets"])


def test_locked_scene_recipe_drives_structure_material_and_qa_contracts():
    project = _project()
    recipe = generate_scene_recipe(project, _profile(project), now=20)
    recipe = review_scene_recipe(
        recipe, reviewer="sales", note="checked", lock=True,
        project_verified=True, construction_confirmed=True, now=30)
    capture = {
        "capture_id": "capture-living", "room_id": "living",
        "camera": {
            "id": "camera-living", "room_id": "living", "focal_length_mm": 24,
            "position": {"x": 2, "y": 1.55, "z": 2},
            "target": {"x": 3, "y": 1.2, "z": 4},
        },
        "rgb_path": "clay.png", "depth_path": "depth.png",
        "normal_path": "normal.png", "edge_path": "edge.png",
        "semantic_path": "semantic.png", "structure_path": "structure.jpg",
    }
    run = {
        "material_mode": "style_pack", "scene_recipe_snapshot": recipe,
        "style": "ignored-freeform-style", "lighting": "locked-lighting",
        "prompt": "young family", "floor_path": "",
    }
    structure_prompt, structure_paths = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="structure")
    material_prompt, material_paths = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="material")
    assert "locked SceneRecipe" in structure_prompt
    assert recipe["scene_hash"] in structure_prompt
    assert "light natural warm-oak floor" in material_prompt
    assert all("floor" not in path for path in material_paths)
    assert structure_paths == [
        "clay.png", "depth.png", "normal.png", "edge.png", "semantic.png"]

    contract = whole_home_engine.build_room_generation_contract(
        project, {**capture, "scene_recipe_snapshot": recipe})
    expected_ids = {
        row["instance_id"] for row in recipe["instances"] if row["room_id"] == "living"
    }
    assert expected_ids <= {row["id"] for row in contract["fixed_objects"]}
    checks = whole_home_engine._whole_home_qa_constraints(contract, "final", "style_pack")
    assert next(row for row in checks if row["constraint_id"] == "C103")["category"] == "scene_recipe"
