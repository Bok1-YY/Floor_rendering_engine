from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tools.goal_loop_v2.blender_op002_vertical_research_display import (
    BLENDER_EXE,
    BRANCH_ID,
    build_display_specs,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
PLAN = ROOT / "reports/op002_vertical_display_plan_20260903/plan.json"
SCRIPT = ROOT / "tools/goal_loop_v2/blender_op002_vertical_research_display.py"


def _png_dimensions(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    return struct.unpack(">II", raw[16:24])


def test_specs_have_intact_walls_and_two_nonsemantic_guides() -> None:
    specs = build_display_specs(SOURCE, PLAN)
    assert len(specs["walls"]) == 35
    assert len({wall["source_atom_id"] for wall in specs["walls"]}) == 35
    assert len(specs["guides"]) == 2
    assert [guide["role"] for guide in specs["guides"]] == [
        "nonsemantic_xy_locator",
        "nonsemantic_head_assumption_guide",
    ]
    assert all(len(item["vertices"]) == 8 and len(item["faces"]) == 6 for item in [*specs["walls"], *specs["guides"]])
    assert specs["opening_cuts"] == 0
    assert specs["sill_level_m"] is None
    assert specs["opening_geometry_created"] is False
    assert specs["door_leaf_created"] is False
    assert specs["ifc_opening_created"] is False
    assert specs["build_authorized"] is False


def test_help_from_temporary_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "--plan" in completed.stdout and "--out" in completed.stdout


@pytest.mark.skipif(not BLENDER_EXE.is_file(), reason="Blender 5.2 executable is not installed")
def test_factory_build_cold_blend_and_glb_roundtrip(tmp_path: Path) -> None:
    output = tmp_path / "display"
    completed = subprocess.run(
        [
            str(BLENDER_EXE),
            "--factory-startup",
            "--background",
            "--python",
            str(SCRIPT),
            "--",
            "--source",
            str(SOURCE),
            "--plan",
            str(PLAN),
            "--out",
            str(output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    combined_output = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined_output
    assert "Traceback" not in combined_output, combined_output
    assert "op002_layer3b_vertical_research_complete" in combined_output
    validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert validation["pass"] is True
    assert validation["actual_wall_count"] == 35
    assert validation["distinct_source_atom_count"] == 35
    assert validation["actual_guide_count"] == 2
    assert validation["wall_topology_errors"] == []
    assert validation["wall_property_errors"] == []
    assert validation["guide_topology_errors"] == []
    assert validation["guide_property_errors"] == []
    assert validation["guide_bound_errors"] == []
    assert validation["forbidden_objects"] == []
    assert validation["opening_cuts"] == 0
    assert validation["opening_elements"] == 0
    assert validation["sill_level_m"] is None
    assert validation["sill_geometry_created"] is False
    assert validation["door_leaf_created"] is False
    assert validation["ifc_opening_created"] is False
    assert validation["build_authorized"] is False
    assert validation["ready"] is False
    assert manifest["wall_count"] == 35
    assert manifest["guide_count"] == 2
    assert manifest["opening_cuts"] == 0
    assert manifest["opening_elements"] == 0
    assert manifest["artifact_path_mode"] == "relative_to_manifest"
    assert manifest["sill_level_m"] is None
    assert manifest["door_leaf_created"] is False
    assert manifest["ifc_opening_created"] is False
    assert len(manifest["artifacts"]) == 7
    for artifact in manifest["artifacts"]:
        path = output / artifact["relative_path"]
        assert path.is_file()
        assert path.stat().st_size == artifact["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    for filename in (
        "1308_op002_layer3b_top.png",
        "1308_op002_layer3b_northeast.png",
        "1308_op002_layer3b_front_closeup.png",
    ):
        assert _png_dimensions(output / filename) == (1200, 1200)

    blend = output / "1308_op002_layer3b_vertical_research_v001.blend"
    verify_blend = tmp_path / "verify_blend.py"
    verify_blend_out = tmp_path / "verify_blend.json"
    verify_blend.write_text(
        "import bpy,json,sys\n"
        f"branch={BRANCH_ID!r}\n"
        "walls=[o for o in bpy.data.objects if o.type=='MESH' and o.get('goal_loop_branch_id')==branch and o.get('goal_loop_role')=='intact_source_wall']\n"
        "guides=[o for o in bpy.data.objects if o.type=='MESH' and o.get('goal_loop_branch_id')==branch and str(o.get('goal_loop_role','')).startswith('nonsemantic_')]\n"
        "cams=[o for o in bpy.data.objects if o.type=='CAMERA' and o.get('goal_loop_branch_id')==branch]\n"
        "meta=[o for o in bpy.data.objects if o.type=='EMPTY' and o.get('goal_loop_branch_id')==branch]\n"
        "result={'walls':len(walls),'guides':len(guides),'cameras':len(cams),'meta':len(meta),'opening_cuts':bpy.context.scene.get('opening_geometry_authorized'),'build_authorized':bpy.context.scene.get('build_authorized')}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    cold_blend = subprocess.run(
        [
            str(BLENDER_EXE),
            "--background",
            str(blend),
            "--python",
            str(verify_blend),
            "--",
            str(verify_blend_out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert cold_blend.returncode == 0, cold_blend.stdout + cold_blend.stderr
    assert "Traceback" not in cold_blend.stdout + cold_blend.stderr
    assert json.loads(verify_blend_out.read_text(encoding="utf-8")) == {
        "walls": 35,
        "guides": 2,
        "cameras": 3,
        "meta": 1,
        "opening_cuts": False,
        "build_authorized": False,
    }

    glb = output / "1308_op002_layer3b_vertical_research_v001.glb"
    verify_glb = tmp_path / "verify_glb.py"
    verify_glb_out = tmp_path / "verify_glb.json"
    verify_glb.write_text(
        "import bpy,json,sys\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.import_scene.gltf(filepath=sys.argv[-2])\n"
        "walls=[o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith('GEO-WALL-')]\n"
        "guides=[o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith('GEO-RESEARCH-OP002-')]\n"
        "meta=[o for o in bpy.data.objects if o.type=='EMPTY' and o.name.startswith('META-')]\n"
        "result={'walls':len(walls),'guides':len(guides),'meta':len(meta),'cameras':len([o for o in bpy.data.objects if o.type=='CAMERA']),'lights':len([o for o in bpy.data.objects if o.type=='LIGHT']),'bad_faces':[o.name for o in [*walls,*guides] if len(o.data.polygons)!=12],'bad_walls':[o.name for o in walls if o.get('opening_cut') is not False],'bad_guides':[o.name for o in guides if o.get('opening_geometry') is not False or o.get('source_fact') is not False]}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [
            str(BLENDER_EXE),
            "--factory-startup",
            "--background",
            "--python",
            str(verify_glb),
            "--",
            str(glb),
            str(verify_glb_out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert "Traceback" not in imported.stdout + imported.stderr
    assert json.loads(verify_glb_out.read_text(encoding="utf-8")) == {
        "walls": 35,
        "guides": 2,
        "meta": 1,
        "cameras": 0,
        "lights": 0,
        "bad_faces": [],
        "bad_walls": [],
        "bad_guides": [],
    }
