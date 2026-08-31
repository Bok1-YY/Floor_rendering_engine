"""Cold-open/cold-import verifier executed by a fresh Blender process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

import bmesh
import bpy


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if os.fspath(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

from tools.fastloop_research.contract import stable_token, validate_bundle  # noqa: E402


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _load_bundle(path: Path) -> dict[str, Any]:
    return validate_bundle(json.loads(path.read_text(encoding="utf-8")))


def _manifold_report(obj: bpy.types.Object, *, weld_attribute_seams: bool = False) -> dict[str, Any]:
    if obj.type != "MESH" or obj.data is None:
        _fail(f"{obj.name}: expected mesh")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        raw = {
            "boundary_edges": sum(1 for edge in bm.edges if edge.is_boundary),
            "non_manifold_edges": sum(1 for edge in bm.edges if not edge.is_manifold),
            "loose_edges": sum(1 for edge in bm.edges if not edge.link_faces),
        }
        if weld_attribute_seams:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1.0e-6)
        boundary = sum(1 for edge in bm.edges if edge.is_boundary)
        non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
        loose = sum(1 for edge in bm.edges if not edge.link_faces)
    finally:
        bm.free()
    return {
        "name": obj.name,
        "vertices": len(obj.data.vertices),
        "faces": len(obj.data.polygons),
        "boundary_edges": boundary,
        "non_manifold_edges": non_manifold,
        "loose_edges": loose,
        "modifiers": [modifier.type for modifier in obj.modifiers],
        "attribute_seam_weld_m": 1.0e-6 if weld_attribute_seams else 0.0,
        "raw_import_edges": raw,
    }


def _expected(bundle: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "walls": {
            f"GEO-WALL-{stable_token(wall['id'])}"
            for wall in bundle["wall_branch_graph"]["walls"]
        },
        "openings": {
            f"OPENING-{stable_token(opening['id'])}"
            for opening in bundle["opening_contract"]["openings"]
        },
        "spaces": {f"SPACE-{stable_token(space['id'])}" for space in bundle["spaces"]},
    }


def _verify_names(bundle: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(bundle)
    actual_walls = {obj.name for obj in bpy.data.objects if obj.name.startswith("GEO-WALL-")}
    actual_openings = {obj.name for obj in bpy.data.objects if obj.name.startswith("OPENING-")}
    actual_spaces = {obj.name for obj in bpy.data.objects if obj.name.startswith("SPACE-")}
    if actual_walls != expected["walls"]:
        _fail(f"wall name set mismatch: expected={sorted(expected['walls'])}, got={sorted(actual_walls)}")
    if actual_openings != expected["openings"]:
        _fail("opening semantic name set mismatch")
    if actual_spaces != expected["spaces"]:
        _fail("space semantic name set mismatch")
    return {
        "walls": sorted(actual_walls),
        "openings": sorted(actual_openings),
        "spaces": sorted(actual_spaces),
    }


def _verify_blend(input_path: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    if not bpy.data.filepath or Path(bpy.data.filepath).resolve() != input_path.resolve():
        _fail("Blender did not cold-open the requested scene.blend")
    scene = bpy.context.scene
    if scene.unit_settings.system != "METRIC" or not abs(scene.unit_settings.scale_length - 1.0) <= 1.0e-12:
        _fail("scene unit contract is not metres")
    if scene.get("up_axis") != "Z" or scene.get("structure_hash") != bundle["structure_hash"]:
        _fail("scene source binding or Z-up contract is missing")
    names = _verify_names(bundle)
    if bpy.data.objects.get("GEO-FLOOR") is None:
        _fail("GEO-FLOOR is missing")
    mesh_names = {obj.name for obj in bpy.data.objects if obj.type == "MESH"}
    expected_mesh_names = set(names["walls"]) | {"GEO-FLOOR"}
    if mesh_names != expected_mesh_names:
        _fail(f"unexpected mesh objects: {sorted(mesh_names ^ expected_mesh_names)}")
    manifold = [_manifold_report(bpy.data.objects[name]) for name in sorted(mesh_names)]
    if any(item["boundary_edges"] or item["non_manifold_edges"] or item["loose_edges"] for item in manifold):
        _fail("one or more Blender meshes are not closed manifold")
    if any("BOOLEAN" in item["modifiers"] for item in manifold):
        _fail("Boolean modifier found in final research geometry")
    cameras = {obj.name for obj in bpy.data.objects if obj.type == "CAMERA"}
    if cameras != {"CAM-TOP", "CAM-NE", "CAM-NW"}:
        _fail(f"fixed camera set mismatch: {sorted(cameras)}")
    if any(bpy.data.objects[name].data.type != "ORTHO" for name in cameras):
        _fail("all verification cameras must be orthographic")
    if len(bpy.data.materials) != 0 or any(obj.type == "LIGHT" for obj in bpy.data.objects):
        _fail("graymodel scene must not contain materials or lights")
    return {
        "object_names": names,
        "mesh_count": len(mesh_names),
        "manifold": manifold,
        "cameras": sorted(cameras),
        "materials": len(bpy.data.materials),
        "lights": len([obj for obj in bpy.data.objects if obj.type == "LIGHT"]),
    }


def _assert_factory_before_glb() -> None:
    expected = {"Camera": "CAMERA", "Cube": "MESH", "Light": "LIGHT"}
    actual = {obj.name: obj.type for obj in bpy.data.objects}
    if bpy.data.filepath or actual != expected:
        _fail(f"GLB verifier expected untouched factory startup, found {actual}")


def _verify_glb(input_path: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    _assert_factory_before_glb()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=os.fspath(input_path))
    names = _verify_names(bundle)
    mesh_names = {obj.name for obj in bpy.data.objects if obj.type == "MESH"}
    expected_mesh_names = set(names["walls"]) | {"GEO-FLOOR"}
    if mesh_names != expected_mesh_names:
        _fail(f"GLB mesh name set mismatch: expected={sorted(expected_mesh_names)}, got={sorted(mesh_names)}")
    if any(obj.type in {"CAMERA", "LIGHT"} for obj in bpy.data.objects):
        _fail("GLB unexpectedly contains cameras or lights")
    manifold = [
        _manifold_report(bpy.data.objects[name], weld_attribute_seams=True)
        for name in sorted(mesh_names)
    ]
    if any(item["boundary_edges"] or item["non_manifold_edges"] or item["loose_edges"] for item in manifold):
        _fail("cold-imported GLB contains non-manifold geometry")
    return {
        "object_names": names,
        "mesh_count": len(mesh_names),
        "manifold": manifold,
        "cameras": 0,
        "lights": 0,
    }


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("blend", "glb"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def _script_argv() -> list[str]:
    if "--" not in sys.argv:
        _fail("missing Blender argument separator '--'")
    return sys.argv[sys.argv.index("--") + 1 :]


def main() -> int:
    args = _parse_args(_script_argv())
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file() or input_path.stat().st_size <= 0:
        _fail(f"input is missing or empty: {input_path}")
    bundle = _load_bundle(args.bundle.expanduser().resolve())
    details = _verify_blend(input_path, bundle) if args.mode == "blend" else _verify_glb(input_path, bundle)
    report = {
        "schema": "research-blender-cold-verify-v1",
        "status": "pass",
        "mode": args.mode,
        "blender_version": bpy.app.version_string,
        "structure_hash": bundle["structure_hash"],
        "details": details,
    }
    _write_json(args.output.expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "fail", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise
