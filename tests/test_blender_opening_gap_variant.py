import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2.blender_opening_gap_variant import BLENDER_EXE, build_piece_specs


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
PLANS = ROOT / "reports/opening_gap_variant_plans_20260902/plans.json"
SCRIPT = ROOT / "tools/goal_loop_v2/blender_opening_gap_variant.py"
IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")


def test_piece_counts_and_only_host_is_split():
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_ids = {atom["id"] for atom in document["wall_graph"]["atoms"]}
    for opening_id in IDS:
        specs = build_piece_specs(document, PLANS, opening_id)
        assert len(specs["pieces"]) == specs["expected_wall_object_count"]
        assert specs["expected_wall_object_count"] == (35 if opening_id == "OP003" else 36)
        host = specs["host_atom_id"]
        assert {piece["source_atom_id"] for piece in specs["pieces"]} == source_ids
        assert all(piece["source_atom_id"] == host or piece["host_parameter_interval"] == [0.0, 1.0] for piece in specs["pieces"])
        assert all(len(piece["vertices"]) == 8 and len(piece["faces"]) == 6 for piece in specs["pieces"])


def test_excluded_or_unknown_opening_is_rejected():
    for opening_id in ("OP005", "OP011", "OP012", "UNKNOWN"):
        with pytest.raises(ValueError):
            build_piece_specs(SOURCE, PLANS, opening_id)


def test_help_from_temporary_cwd(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--opening-id" in result.stdout and "--wall-height" in result.stdout


@pytest.mark.skipif(not BLENDER_EXE.is_file(), reason="Blender 5.2 executable is not installed")
def test_op004_factory_build_and_glb_roundtrip(tmp_path):
    output = tmp_path / "OP004"
    build = subprocess.run(
        [str(BLENDER_EXE), "--factory-startup", "--background", "--python", str(SCRIPT), "--", "--plans", str(PLANS), "--source", str(SOURCE), "--opening-id", "OP004", "--out", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert "Traceback" not in build.stdout + build.stderr, build.stdout + build.stderr
    validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert validation["pass"] is True and validation["actual_wall_piece_count"] == 36
    assert validation["actual_host_piece_count"] == 2
    assert validation["gap_overlap_errors"] == [] and validation["topology_errors"] == []
    assert validation["closeup_camera_ortho_scale_m"] == pytest.approx(max(2.4, validation["gap_width_m"] * 2.5))
    assert validation["closeup_render_resolution_px"] == [1200, 1200]
    assert manifest["one_opening_only"] is True and manifest["build_authorized"] is False
    assert manifest["gap_center_m"] == validation["gap_center_m"]
    assert all((output / name).is_file() and (output / name).stat().st_size > 0 for name in ("1308_OP004_gap_variant_v001.blend", "1308_OP004_gap_variant_v001.glb", "1308_OP004_gap_variant_top.png", "1308_OP004_gap_variant_northeast.png", "1308_OP004_gap_variant_closeup_top.png"))

    verify_script = tmp_path / "verify_glb.py"
    verify_output = tmp_path / "verify.json"
    verify_script.write_text(
        "import bpy,json,sys\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.import_scene.gltf(filepath=sys.argv[-2])\n"
        "walls=[o for o in bpy.data.objects if o.name.startswith('GEO-WALL-')]\n"
        "meta=[o for o in bpy.data.objects if o.name.startswith('META-')]\n"
        "result={'walls':len(walls),'meta':len(meta),'cameras':len([o for o in bpy.data.objects if o.type=='CAMERA']),'lights':len([o for o in bpy.data.objects if o.type=='LIGHT']),'bad': [o.name for o in walls if len(o.data.polygons)!=12], 'extras':all(o.get('opening_id')=='OP004' and o.get('research_only') is True for o in walls)}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    imported = subprocess.run([str(BLENDER_EXE), "--factory-startup", "--background", "--python", str(verify_script), "--", str(output / "1308_OP004_gap_variant_v001.glb"), str(verify_output)], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert json.loads(verify_output.read_text(encoding="utf-8")) == {"walls": 36, "meta": 1, "cameras": 0, "lights": 0, "bad": [], "extras": True}
