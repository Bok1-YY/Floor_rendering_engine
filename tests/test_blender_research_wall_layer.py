import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2.blender_research_wall_layer import wall_box_geometry


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
SCRIPT = ROOT / "tools/goal_loop_v2/blender_research_wall_layer.py"
BLENDER = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")


def test_all_source_atoms_have_box_geometry():
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert len(document["wall_graph"]["atoms"]) == 35
    for atom in document["wall_graph"]["atoms"]:
        vertices, faces = wall_box_geometry(atom, 2.8)
        assert len(vertices) == 8 and len(faces) == 6
        assert min(vertex[2] for vertex in vertices) == 0
        assert max(vertex[2] for vertex in vertices) == 2.8


def test_zero_length_and_invalid_dimensions_are_rejected():
    with pytest.raises(ValueError, match="zero length"):
        wall_box_geometry({"centerline_m": [[0, 0], [0, 0]], "thickness_m": 0.1}, 2.8)
    with pytest.raises(ValueError, match="positive"):
        wall_box_geometry({"centerline_m": [[0, 0], [1, 0]], "thickness_m": 0}, 2.8)


def test_diagonal_wall_preserves_thickness():
    vertices, _ = wall_box_geometry({"centerline_m": [[0, 0], [1, 1]], "thickness_m": 0.2}, 2.8)
    assert math.isclose(math.dist(vertices[0], vertices[3]), 0.2, abs_tol=1e-9)
    assert math.isclose(math.dist(vertices[1], vertices[2]), 0.2, abs_tol=1e-9)


def test_help_from_temporary_cwd(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--expected-source-sha256" in result.stdout and "--collection-name" in result.stdout


@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender 5.2 executable is not installed")
def test_factory_startup_build_and_glb_roundtrip(tmp_path):
    output = tmp_path / "wall-layer"
    build = subprocess.run(
        [str(BLENDER), "--factory-startup", "--background", "--python", str(SCRIPT), "--", "--source", str(SOURCE), "--out", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    validation = json.loads((output / "wall_layer_structural_validation.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert validation["pass"] is True and validation["actual_wall_objects"] == 35
    assert validation["geometry_errors"] == [] and validation["opening_cuts"] == 0
    assert manifest["formal_build_authorized"] is False
    expected_files = ["1308_research_wall_layer_checkpoint_v000.blend", "1308_research_wall_layer_v001.blend", "1308_research_wall_layer_v001.glb", "1308_wall_layer_top.png", "1308_wall_layer_northeast.png", "1308_wall_layer_northwest.png"]
    assert all((output / name).is_file() and (output / name).stat().st_size > 0 for name in expected_files)

    verify_script = tmp_path / "verify_glb.py"
    verify_output = tmp_path / "verify_glb.json"
    verify_script.write_text(
        "import bpy,json,sys\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.import_scene.gltf(filepath=sys.argv[-2])\n"
        "walls=[o for o in bpy.data.objects if o.name.startswith('GEO-WALL-')]\n"
        "meta=[o for o in bpy.data.objects if o.name.startswith('META-')]\n"
        "bad=[o.name for o in walls if len(o.data.vertices)!=24 or len(o.data.polygons)!=12]\n"
        "result={'walls':len(walls),'meta':len(meta),'cameras':len([o for o in bpy.data.objects if o.type=='CAMERA']),'lights':len([o for o in bpy.data.objects if o.type=='LIGHT']),'actions':len(bpy.data.actions),'bad_topology':bad,'extras_ok':all(o.get('research_only') is True and o.get('not_for_construction') is True and o.get('wall_atom_id') for o in walls)}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [str(BLENDER), "--factory-startup", "--background", "--python", str(verify_script), "--", str(output / "1308_research_wall_layer_v001.glb"), str(verify_output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    roundtrip = json.loads(verify_output.read_text(encoding="utf-8"))
    assert roundtrip == {"walls": 35, "meta": 1, "cameras": 0, "lights": 0, "actions": 0, "bad_topology": [], "extras_ok": True}
