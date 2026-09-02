from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tools.goal_loop_v2.blender_opening_vertical_research_display import BLENDER_EXE, build_specs


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
PLAN = ROOT / "reports/op004_vertical_display_plan_20260903/plan.json"
SCRIPT = ROOT / "tools/goal_loop_v2/blender_opening_vertical_research_display.py"


def _png_dimensions(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", raw[16:24])


def test_op004_specs_are_intact_and_nonsemantic() -> None:
    specs = build_specs(SOURCE, PLAN)
    assert specs["opening_id"] == "OP004"
    assert len(specs["walls"]) == 35
    assert len(specs["guides"]) == 2
    assert specs["head_guide_binding"] == "unbound_research_default"
    assert specs["sill_m"] is None
    assert all(len(item["vertices"]) == 8 and len(item["faces"]) == 6 for item in [*specs["walls"], *specs["guides"]])


def test_help_from_temp_cwd(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0
    assert "--plan" in result.stdout and "--out" in result.stdout


@pytest.mark.skipif(not BLENDER_EXE.is_file(), reason="Blender 5.2 executable is not installed")
def test_op004_factory_build_and_glb_roundtrip(tmp_path: Path) -> None:
    output = tmp_path / "op004"
    result = subprocess.run(
        [str(BLENDER_EXE), "--factory-startup", "--background", "--python", str(SCRIPT), "--", "--source", str(SOURCE), "--plan", str(PLAN), "--out", str(output)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert validation["pass"] is True
    assert validation["actual_wall_count"] == 35
    assert validation["actual_guide_count"] == 2
    assert validation["opening_cuts"] == 0
    assert validation["opening_elements"] == 0
    assert validation["forbidden_objects"] == []
    assert validation["sill_m"] is None
    assert validation["build_authorized"] is False
    assert manifest["wall_count"] == 35
    assert manifest["plan_file_sha256"] == hashlib.sha256(PLAN.read_bytes()).hexdigest()
    assert manifest["source_document_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert manifest["guide_count"] == 2
    assert manifest["head_guide_binding"] == "unbound_research_default"
    assert manifest["artifact_path_mode"] == "relative_to_manifest"
    assert len(manifest["artifacts"]) == 7
    for artifact in manifest["artifacts"]:
        path = output / artifact["relative_path"]
        assert path.is_file() and path.stat().st_size == artifact["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    for name in ("top", "northeast", "front_closeup"):
        assert _png_dimensions(output / f"1308_OP004_layer3b_{name}.png") == (1200, 1200)
    glb = output / "1308_OP004_layer3b_vertical_research_v001.glb"
    verify_script = tmp_path / "verify_glb.py"
    verify_output = tmp_path / "verify.json"
    verify_script.write_text(
        "import bpy,json,sys\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.import_scene.gltf(filepath=sys.argv[-2])\n"
        "walls=[o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith('GEO-WALL-')]\n"
        "guides=[o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith('GEO-RESEARCH-OP004-')]\n"
        "r={'walls':len(walls),'guides':len(guides),'meta':len([o for o in bpy.data.objects if o.type=='EMPTY' and o.name.startswith('META-')]),'cameras':len([o for o in bpy.data.objects if o.type=='CAMERA']),'lights':len([o for o in bpy.data.objects if o.type=='LIGHT']),'bad_walls':[o.name for o in walls if o.get('opening_cut') is not False],'bad_guides':[o.name for o in guides if o.get('opening_geometry') is not False or o.get('source_fact') is not False]}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(r))\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [str(BLENDER_EXE), "--factory-startup", "--background", "--python", str(verify_script), "--", str(glb), str(verify_output)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert "Traceback" not in imported.stdout + imported.stderr
    assert json.loads(verify_output.read_text(encoding="utf-8")) == {
        "walls": 35,
        "guides": 2,
        "meta": 1,
        "cameras": 0,
        "lights": 0,
        "bad_walls": [],
        "bad_guides": [],
    }
