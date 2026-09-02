from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tools.goal_loop_v2.blender_combined_gap_layer import (
    BLENDER_EXE,
    BRANCH_ID,
    build_piece_specs,
    validate_combined_plan_for_blender,
)
from tools.fastloop_research.v21_contract import validate_v21_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
PLAN = ROOT / "reports/combined_gap_plan_20260903/plan.json"
SCRIPT = ROOT / "tools/goal_loop_v2/blender_combined_gap_layer.py"


def _png_dimensions(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    return struct.unpack(">II", raw[16:24])


def test_combined_piece_count_and_host_replacements() -> None:
    specs = build_piece_specs(SOURCE, PLAN)
    pieces = specs["pieces"]
    counts = Counter(piece["source_atom_id"] for piece in pieces)
    assert len(pieces) == 43
    assert sum(piece["is_gap_host_piece"] for piece in pieces) == 17
    assert sum(not piece["is_gap_host_piece"] for piece in pieces) == 26
    assert counts == Counter(specs["expected_source_atom_piece_counts"])
    assert specs["included_opening_ids"] == [
        "OP001",
        "OP002",
        "OP003",
        "OP004",
        "OP006",
        "OP007",
        "OP008",
        "OP009",
        "OP010",
    ]
    assert specs["excluded_opening_ids"] == [
        "OP005",
        "OP011",
        "PORTAL-WB011-WB006-01",
        "OP012",
    ]
    assert list(specs["gap_windows_by_opening"]) == specs["included_opening_ids"]
    assert all(window["resolution_px"] == [1200, 1200] for window in specs["gap_windows_by_opening"].values())
    assert all(len(piece["vertices"]) == 8 and len(piece["faces"]) == 6 for piece in pieces)
    op003_pieces = [piece for piece in pieces if piece["opening_id"] == "OP003"]
    assert len(op003_pieces) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_wall_piece_count", 44),
        ("semantic_promotion", True),
        ("build_authorized", True),
        ("portable_bundle", True),
    ],
)
def test_rehashed_combined_plan_tampering_is_rejected(field: str, value) -> None:
    source = validate_v21_document(json.loads(SOURCE.read_text(encoding="utf-8")))
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan[field] = value
    payload = {key: item for key, item in plan.items() if key != "candidate_hash"}
    plan["candidate_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError):
        validate_combined_plan_for_blender(
            plan,
            document=source,
            source_file_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        )


def test_nested_gap_overlap_with_rehashed_nested_and_outer_hash_is_rejected() -> None:
    source = validate_v21_document(json.loads(SOURCE.read_text(encoding="utf-8")))
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    row = plan["plans"][1]
    gap_lo, gap_hi = sorted(row["host_parameters"])
    row["remaining_host_pieces"][0]["host_parameter_interval"] = [0.0, (gap_lo + gap_hi) / 2.0]
    nested = {key: item for key, item in row.items() if key != "variant_hash"}
    row["variant_hash"] = hashlib.sha256(
        json.dumps(nested, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {key: item for key, item in plan.items() if key != "candidate_hash"}
    plan["candidate_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError):
        validate_combined_plan_for_blender(
            plan,
            document=source,
            source_file_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        )


def test_help_from_temporary_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "--plan" in completed.stdout
    assert "--wall-height" in completed.stdout


@pytest.mark.skipif(not BLENDER_EXE.is_file(), reason="Blender 5.2 executable is not installed")
def test_factory_build_cold_blend_and_glb_roundtrip(tmp_path: Path) -> None:
    output = tmp_path / "combined"
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
    assert "combined_xy_gap_research_complete" in combined_output

    validation_path = output / "validation.json"
    manifest_path = output / "artifact_manifest.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert validation["pass"] is True
    assert validation["actual_wall_piece_count"] == 43
    assert validation["actual_untouched_piece_count"] == 26
    assert validation["actual_host_piece_count"] == 17
    assert validation["non_host_count_errors"] == []
    assert validation["host_count_errors"] == []
    assert validation["topology_errors"] == []
    assert validation["property_errors"] == []
    assert validation["gap_overlap_errors"] == []
    assert validation["opening_elements"] == 0
    assert validation["wall_height_m"] == pytest.approx(2.8)
    assert validation["unit_system"] == "METRIC"
    assert validation["length_unit"] == "METERS"
    assert validation["build_authorized"] is False
    assert validation["ready"] is False
    assert validation["full_top_metric_window"]["resolution_px"] == [1200, 1200]
    assert validation["full_top_metric_window"]["ortho_scale_m"] > 0

    assert manifest["schema"] == "blender-combined-gap-layer-artifact-manifest-v1"
    assert manifest["wall_piece_count"] == 43
    assert manifest["opening_elements"] == 0
    assert manifest["artifact_path_mode"] == "relative_to_manifest"
    assert manifest["evidence_plan_portable"] is False
    assert manifest["artifact_files_relocatable_with_manifest"] is True
    assert manifest["build_authorized"] is False
    assert len(manifest["artifacts"]) == 16
    for artifact in manifest["artifacts"]:
        assert "relative_path" in artifact and "path" not in artifact
        path = output / artifact["relative_path"]
        assert path.is_file()
        assert path.stat().st_size == artifact["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]

    for filename in (
        "1308_combined_xy_gap_top.png",
        "1308_combined_xy_gap_northeast.png",
        "1308_combined_xy_gap_northwest.png",
    ):
        assert _png_dimensions(output / filename) == (1200, 1200)
    for opening_id in validation["included_opening_ids"]:
        assert _png_dimensions(output / f"1308_combined_xy_gap_{opening_id}_closeup_top.png") == (1200, 1200)
        assert validation["gap_closeup_metric_windows"][opening_id]["resolution_px"] == [1200, 1200]

    final_blend = output / "1308_combined_xy_gap_research_v001.blend"
    glb = output / "1308_combined_xy_gap_research_v001.glb"
    verify_blend_script = tmp_path / "verify_blend.py"
    verify_blend_output = tmp_path / "verify_blend.json"
    verify_blend_script.write_text(
        "import bpy,json,sys\n"
        f"branch={BRANCH_ID!r}\n"
        "walls=[o for o in bpy.data.objects if o.type=='MESH' and o.get('goal_loop_branch_id')==branch and o.get('goal_loop_role')=='wall_piece']\n"
        "cams=[o for o in bpy.data.objects if o.type=='CAMERA' and o.get('goal_loop_branch_id')==branch]\n"
        "meta=[o for o in bpy.data.objects if o.type=='EMPTY' and o.get('goal_loop_branch_id')==branch]\n"
        "startup={n:bool(bpy.data.objects.get(n) and bpy.data.objects[n].hide_render) for n in ('Cube','Light','Camera')}\n"
        "result={'walls':len(walls),'cameras':len(cams),'meta':len(meta),'startup_hidden':startup,'scene_branch':bpy.context.scene.get('goal_loop_branch_id'),'build_authorized':bpy.context.scene.get('build_authorized')}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    cold_blend = subprocess.run(
        [
            str(BLENDER_EXE),
            "--background",
            str(final_blend),
            "--python",
            str(verify_blend_script),
            "--",
            str(verify_blend_output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert cold_blend.returncode == 0, cold_blend.stdout + cold_blend.stderr
    assert "Traceback" not in cold_blend.stdout + cold_blend.stderr
    assert json.loads(verify_blend_output.read_text(encoding="utf-8")) == {
        "walls": 43,
        "cameras": 4,
        "meta": 1,
        "startup_hidden": {"Cube": True, "Light": True, "Camera": True},
        "scene_branch": BRANCH_ID,
        "build_authorized": False,
    }

    verify_glb_script = tmp_path / "verify_glb.py"
    verify_glb_output = tmp_path / "verify_glb.json"
    verify_glb_script.write_text(
        "import bpy,json,sys\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.import_scene.gltf(filepath=sys.argv[-2])\n"
        "walls=[o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith('GEO-WALL-')]\n"
        "meta=[o for o in bpy.data.objects if o.type=='EMPTY' and o.name.startswith('META-')]\n"
        "counts={}\n"
        "for o in walls: counts[o.get('source_atom_id')]=counts.get(o.get('source_atom_id'),0)+1\n"
        "result={'walls':len(walls),'meta':len(meta),'cameras':len([o for o in bpy.data.objects if o.type=='CAMERA']),'lights':len([o for o in bpy.data.objects if o.type=='LIGHT']),'bad_faces':[o.name for o in walls if len(o.data.polygons)!=12],'bad_extras':[o.name for o in walls if o.get('research_only') is not True or o.get('build_authorized') is not False or o.get('semantic_promotion') is not False],'branch_ids':sorted(set(o.get('goal_loop_branch_id') for o in walls)),'source_atom_ids':len(counts)}\n"
        "open(sys.argv[-1],'w',encoding='utf-8').write(json.dumps(result))\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [
            str(BLENDER_EXE),
            "--factory-startup",
            "--background",
            "--python",
            str(verify_glb_script),
            "--",
            str(glb),
            str(verify_glb_output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert "Traceback" not in imported.stdout + imported.stderr
    assert json.loads(verify_glb_output.read_text(encoding="utf-8")) == {
        "walls": 43,
        "meta": 1,
        "cameras": 0,
        "lights": 0,
        "bad_faces": [],
        "bad_extras": [],
        "branch_ids": [BRANCH_ID],
        "source_atom_ids": 35,
    }
