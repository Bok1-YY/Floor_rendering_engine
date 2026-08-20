import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from Floor_engine_server import whole_home_ifc_gold as ifc_gold
from Floor_engine_server.whole_home_ifc_gold import (
    GOLD_THRESHOLDS,
    compare_cad_model_to_ifc_truth,
    default_fzk_paths,
    derive_compressed_raster_variant,
    derive_ifc_gold_case,
    run_cad_gold_case,
    run_raster_gold_case,
)


def _closed(points):
    return [*points, points[0]]


def test_gold_comparison_uses_independent_footprints_and_openings():
    truth = {
        "walls": [{"footprint_polygon": _closed([[0, 0], [4, 0], [4, .2], [0, .2]])}],
        "spaces": [{"polygon": _closed([[0, .2], [4, .2], [4, 3], [0, 3]])}],
        "openings": [{"kind": "door", "center": [2, .1], "width_m": 1.0}],
    }
    model = {
        "cad_to_model": {"x": 0, "z": 0},
        "wall_assemblies": [{
            "id": "wall-a", "review_status": "accepted",
            "footprint_polygon": _closed([[0, 0], [4, 0], [4, .2], [0, .2]]),
            "centerline": [[0, .1], [4, .1]],
        }],
        "walls": [],
        "physical_spaces": [{"polygon": _closed([[0, .2], [4, .2], [4, 3], [0, 3]])}],
        "openings": [{
            "kind": "door", "wall_assembly_id": "wall-a",
            "offset_m": 1.5, "width_m": 1.0,
        }],
    }
    metrics = compare_cad_model_to_ifc_truth(model, truth)
    assert metrics["wall_footprint_iou"] == 1
    assert metrics["wall_boundary_p95_m"] == 0
    assert metrics["room_footprint_iou"] == 1
    assert metrics["opening_precision"] == 1
    assert metrics["opening_recall"] == 1
    assert metrics["wall_assembly_coverage"] == 1


def test_gold_thresholds_are_not_toy_tolerances():
    assert GOLD_THRESHOLDS == {
        "wall_footprint_iou_min": 0.98,
        "wall_boundary_p95_m_max": 0.05,
        "room_footprint_iou_min": 0.95,
        "opening_precision_min": 0.90,
        "opening_recall_min": 0.90,
        "opening_center_p95_m_max": 0.20,
        "opening_width_p95_m_max": 0.05,
        "wall_assembly_coverage_min": 1.0,
    }


def test_compressed_raster_variant_is_deterministic_and_checksum_locked(
    tmp_path: Path,
):
    from PIL import Image, ImageDraw

    case = tmp_path / "case"
    case.mkdir()
    source = case / "input_dimensioned.png"
    image = Image.new("RGB", (200, 120), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 180, 100), fill=(242, 242, 238), outline=(43, 47, 52), width=8)
    image.save(source)
    import hashlib
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (case / "case_manifest.json").write_text(json.dumps({
        "case_id": "deterministic-compressed",
        "artifacts": {source.name: {"sha256": source_sha, "size_bytes": source.stat().st_size}},
    }), encoding="utf-8")
    first = derive_compressed_raster_variant(case)
    first_bytes = (case / "input_compressed.png").read_bytes()
    second = derive_compressed_raster_variant(case)
    assert first == second
    assert first_bytes == (case / "input_compressed.png").read_bytes()
    manifest = json.loads((case / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["input_compressed.png"]["sha256"] == first["sha256"]
    assert manifest["raster_variants"]["compressed"]["transform"]["jpeg_quality"] == 98


def test_physical_slice_dxf_keeps_complex_wall_and_normalizes_room_seams(
    tmp_path: Path,
):
    wall_points = [(0, 0), (4, 0), (4, .2), (.2, .2), (.2, 3), (0, 3)]
    wall = Polygon(wall_points)
    mesh = ifc_gold.Mesh(
        entity_id=101, entity_type="IfcWall", name="L wall",
        vertices=tuple((x, y, 0.0) for x, y in wall_points),
        faces=((0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)),
    )
    left_room = Polygon([(0, .2), (2, .2), (2, 3), (0, 3)])
    right_room = Polygon([(2, .2), (4, .2), (4, 3), (2, 3)])
    target = tmp_path / "recovered.dxf"
    ifc_gold._write_dxf(target, {
        "wall_meshes": [mesh],
        "wall_polygons": {101: wall},
        "space_polygons": {201: left_room, 202: right_room},
        "openings": [],
        "extraction_warnings": [{"code": "ifc_physical_storey_slice_recovered"}],
    })

    ezdxf, *_ = ifc_gold._dependency_modules()
    document = ezdxf.readfile(target)
    entities = list(document.modelspace())
    wall_entity = next(row for row in entities if row.dxf.layer == "A-WALL-FOOTPRINT")
    recovered_wall = Polygon([(point[0], point[1]) for point in wall_entity.get_points()])
    assert recovered_wall.symmetric_difference(wall).area < 1e-9
    assert recovered_wall.area < wall.minimum_rotated_rectangle.area * .5

    rooms = [
        Polygon([(point[0], point[1]) for point in row.get_points()])
        for row in entities if row.dxf.layer == "A-ROOM-BOUNDARY"
    ]
    assert len(rooms) == 2
    assert rooms[0].intersection(rooms[1]).area == 0
    assert sum(room.area for room in rooms) / (left_room.area + right_room.area) > .9998

    repeated = tmp_path / "recovered-repeated.dxf"
    ifc_gold._write_dxf(repeated, {
        "wall_meshes": [mesh],
        "wall_polygons": {101: wall},
        "space_polygons": {201: left_room, 202: right_room},
        "openings": [],
        "extraction_warnings": [{"code": "ifc_physical_storey_slice_recovered"}],
    })
    assert target.read_bytes() == repeated.read_bytes()


def test_physical_slice_wall_repair_accepts_only_provable_thin_bands():
    thin_band = ifc_gold.Mesh(
        entity_id=1, entity_type="IfcWall", name="split band",
        vertices=(
            (0, 0, 0), (1, 0, 0), (1, .1, 0), (0, .1, 0),
            (2, 0, 0), (4, 0, 0), (4, .1, 0), (2, .1, 0),
        ),
        faces=((0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)),
    )
    restored = ifc_gold._projected_slice_wall_polygon(thin_band)
    assert restored.symmetric_difference(
        Polygon([(0, 0), (4, 0), (4, .1), (0, .1)]),
    ).area < 1e-9

    square_panels = ifc_gold.Mesh(
        entity_id=2, entity_type="IfcWall", name="facade panels",
        vertices=(
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0),
        ),
        faces=((0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)),
    )
    with pytest.raises(ifc_gold.IfcGoldError, match="not a provable thin wall band"):
        ifc_gold._projected_slice_wall_polygon(square_panels)


def test_installed_fzk_case_derives_and_runs_through_production_parser(tmp_path: Path):
    ifc_path, _ = default_fzk_paths()
    if not ifc_path.is_file():
        pytest.skip("pinned FZK IFC is not installed")
    output = tmp_path / "fzk"
    derivation = derive_ifc_gold_case(ifc_path, output, case_id="ifcbench_fzk_house")
    assert derivation["counts"]["walls"] >= 8
    assert derivation["counts"]["spaces"] >= 5
    assert derivation["counts"]["openings"] >= 10
    for name in (
        "case_manifest.json", "truth_geometry.json", "truth_geometry_manifest.json",
        "input_double_line.dxf", "input_dimensioned.png", "truth_gray_model.obj",
        "truth_gray_preview.png",
    ):
        assert (output / name).stat().st_size > 100
    repeated = derive_ifc_gold_case(
        ifc_path, tmp_path / "fzk-repeat", case_id="ifcbench_fzk_house")
    assert repeated["artifacts"] == derivation["artifacts"]
    result = run_cad_gold_case(output)
    # The pinned IFC declares six semantic spaces, but its lower bedroom,
    # foyer and living room share one physical face with no separating wall.
    # Geometry must therefore remain fail-closed instead of recreating the old
    # text-anchor Voronoi split.  Wall/opening truth still provides a strong
    # deterministic regression for the production parser.
    assert result["status"] == "failed", json.dumps(result, ensure_ascii=False, indent=2)
    assert result["metrics"]["wall_footprint_iou"] >= .98
    assert result["metrics"]["room_footprint_iou"] >= .95
    assert result["metrics"]["opening_precision"] == 1
    assert result["metrics"]["opening_recall"] == 1
    assert {row.get("code") for row in result["production_parse"]["hard_errors"]} == {
        "cad_physical_boundary_missing_for_enclosed_room_labels",
    }
    raster = run_raster_gold_case(output)
    assert raster["status"] == "passed", json.dumps(raster, ensure_ascii=False, indent=2)
    assert raster["metrics"]["scale_anchor_count"] == 2
    assert raster["metrics"]["opening_recall"] == 1
