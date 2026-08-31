"""Create and reopen-verify one IFC4 research model from the strict bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if os.fspath(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

import ifcopenshell  # noqa: E402
import ifcopenshell.guid  # noqa: E402
from ifcopenshell.api import context, feature, geometry, pset, project, root, unit  # noqa: E402
from ifcopenshell.util.element import get_pset  # noqa: E402
import numpy as np  # noqa: E402

try:  # Package import in the web/Nuitka process.
    from .contract import (
        canonical_json,
        floor_mesh,
        openings_for_wall,
        project_opening,
        validate_bundle,
        wall_mesh,
    )
except ImportError:  # Direct script execution in the source checkout.
    from tools.fastloop_research.contract import (  # type: ignore[no-redef]  # noqa: E402
        canonical_json,
        floor_mesh,
        openings_for_wall,
        project_opening,
        validate_bundle,
        wall_mesh,
    )


GUID_NAMESPACE = uuid.UUID("b2b8c0b1-d129-54f2-b350-d45d28f9de88")
FIXED_TIMESTAMP = "2026-08-31T00:00:00"
PSET_NAME = "Pset_ResearchModel"


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_guid(key: str) -> str:
    return ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, key).hex)


def _set_guid(entity: Any, key: str) -> None:
    if hasattr(entity, "GlobalId"):
        entity.GlobalId = _stable_guid(key)


def _identity_placement(model: Any, product: Any, translation: Sequence[float] | None = None) -> None:
    matrix = np.identity(4)
    if translation is not None:
        matrix[:3, 3] = list(map(float, translation))
    geometry.edit_object_placement(model, product=product, matrix=matrix, is_si=True)


def _assign_mesh(
    model: Any,
    body: Any,
    product: Any,
    payload: Mapping[str, Any],
    *,
    force_faceted_brep: bool = False,
) -> None:
    representation = geometry.add_mesh_representation(
        model,
        context=body,
        vertices=[[
            tuple(float(value) for value in vertex)
            for vertex in payload["vertices"]
        ]],
        faces=[[
            tuple(int(index) for index in face)
            for face in payload["faces"]
        ]],
        unit_scale=1.0,
        force_faceted_brep=force_faceted_brep,
    )
    geometry.assign_representation(model, product=product, representation=representation)
    _identity_placement(model, product)


def _rel_aggregates(model: Any, key: str, relating: Any, related: list[Any]) -> Any:
    return model.create_entity(
        "IfcRelAggregates",
        GlobalId=_stable_guid(f"rel-aggregates:{key}"),
        OwnerHistory=None,
        Name=None,
        Description=None,
        RelatingObject=relating,
        RelatedObjects=tuple(related),
    )


def _rel_contained(model: Any, key: str, relating: Any, related: list[Any]) -> Any:
    return model.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=_stable_guid(f"rel-contained:{key}"),
        OwnerHistory=None,
        Name=None,
        Description=None,
        RelatedElements=tuple(related),
        RelatingStructure=relating,
    )


def _research_pset(model: Any, product: Any, bundle: Mapping[str, Any], stable_id: str) -> None:
    research = pset.add_pset(model, product=product, name=PSET_NAME)
    pset.edit_pset(
        model,
        pset=research,
        properties={
            "ResearchOnly": True,
            "ConstructionGrade": False,
            "StableId": stable_id,
            "SourceHash": bundle["source_hash"],
            "StructureHash": bundle["structure_hash"],
            "AssumptionsJson": canonical_json(bundle["assumptions"]).decode("utf-8"),
            "UnresolvedIssueCount": len(bundle["unresolved_issues"]),
        },
    )


def _oriented_box(
    wall: Mapping[str, Any],
    opening: Mapping[str, Any],
    *,
    normal_depth_m: float,
    tangent_inset_m: float = 0.0,
) -> dict[str, Any]:
    a = tuple(map(float, wall["centerline_m"][0]))
    b = tuple(map(float, wall["centerline_m"][1]))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = (dx * dx + dy * dy) ** 0.5
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    projection = project_opening(wall, opening)
    start = projection["start_m"] + tangent_inset_m
    end = projection["end_m"] - tangent_inset_m
    if end <= start:
        _fail(f"opening {opening['id']}: tangent inset consumes filling width")
    half_n = normal_depth_m * 0.5
    ring = [
        (a[0] + start * tx - half_n * nx, a[1] + start * ty - half_n * ny),
        (a[0] + end * tx - half_n * nx, a[1] + end * ty - half_n * ny),
        (a[0] + end * tx + half_n * nx, a[1] + end * ty + half_n * ny),
        (a[0] + start * tx + half_n * nx, a[1] + start * ty + half_n * ny),
    ]
    z0, z1 = float(opening["sill_m"]), float(opening["head_m"])
    vertices = [[x, y, z0] for x, y in ring] + [[x, y, z1] for x, y in ring]
    faces = [
        [0, 3, 2, 1],
        [4, 5, 6, 7],
        [0, 1, 5, 4],
        [1, 2, 6, 5],
        [2, 3, 7, 6],
        [3, 0, 4, 7],
    ]
    return {"vertices": vertices, "faces": faces}


def _write_ifc(model: Any, output: Path) -> None:
    if output.exists():
        _fail(f"refusing to overwrite IFC: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    model.header.file_description.description = ("ViewDefinition [DesignTransferView_V1.0]",)
    model.header.file_name.name = output.name
    model.header.file_name.time_stamp = FIXED_TIMESTAMP
    model.header.file_name.author = ("FastLoop Research Kernel",)
    model.header.file_name.organization = ("Floor Engine Non-Commercial Research",)
    model.header.file_name.preprocessor_version = f"IfcOpenShell {getattr(ifcopenshell, 'version', 'unknown')}"
    model.header.file_name.originating_system = "FastLoop deterministic research-model kernel"
    model.header.file_name.authorization = "ResearchOnly=true; not construction documentation"
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        model.write(os.fspath(temporary))
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build(bundle_path: Path, output: Path, report_path: Path) -> dict[str, Any]:
    bundle = validate_bundle(json.loads(bundle_path.read_text(encoding="utf-8")))
    if report_path.exists():
        _fail(f"refusing to overwrite IFC report: {report_path}")
    model = project.create_file(version="IFC4")
    project_entity = root.create_entity(model, ifc_class="IfcProject", name="Floor Engine Research Model")
    site = root.create_entity(model, ifc_class="IfcSite", name="Research Site")
    building = root.create_entity(model, ifc_class="IfcBuilding", name="Research Building")
    storey = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Research Storey 01")
    storey.Elevation = 0.0
    for entity, key in (
        (project_entity, "project"),
        (site, "site"),
        (building, "building"),
        (storey, "storey-01"),
    ):
        _set_guid(entity, key)

    length = unit.add_si_unit(model, unit_type="LENGTHUNIT")
    area = unit.add_si_unit(model, unit_type="AREAUNIT")
    volume = unit.add_si_unit(model, unit_type="VOLUMEUNIT")
    project_entity.UnitsInContext = model.create_entity("IfcUnitAssignment", Units=(length, area, volume))
    model_context = context.add_context(model, context_type="Model")
    body = context.add_context(
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_context,
    )
    for entity in (site, building, storey):
        _identity_placement(model, entity)

    slab = root.create_entity(model, ifc_class="IfcSlab", predefined_type="FLOOR", name="SLAB-RESEARCH-FLOOR")
    _set_guid(slab, "slab:floor")
    _assign_mesh(
        model,
        body,
        slab,
        floor_mesh(bundle["outer_boundary_m"], float(bundle["assumptions"]["floor_slab_thickness_m"])),
    )
    _research_pset(model, slab, bundle, "floor")

    wall_entities: list[Any] = []
    wall_by_id: dict[str, Any] = {}
    wall_contract_by_id = {wall["id"]: wall for wall in bundle["wall_branch_graph"]["walls"]}
    for wall in bundle["wall_branch_graph"]["walls"]:
        entity = root.create_entity(model, ifc_class="IfcWall", predefined_type="NOTDEFINED", name=f"WALL-{wall['id']}")
        _set_guid(entity, f"wall:{wall['id']}")
        _assign_mesh(model, body, entity, wall_mesh(wall, openings_for_wall(bundle, wall["id"])))
        _research_pset(model, entity, bundle, wall["id"])
        wall_entities.append(entity)
        wall_by_id[wall["id"]] = entity

    space_entities: list[Any] = []
    for space in bundle["spaces"]:
        entity = root.create_entity(model, ifc_class="IfcSpace", name=space["label"])
        entity.LongName = space["id"]
        _set_guid(entity, f"space:{space['id']}")
        _identity_placement(model, entity, [float(space["point_m"][0]), float(space["point_m"][1]), 0.0])
        _research_pset(model, entity, bundle, space["id"])
        space_entities.append(entity)

    opening_entities: list[Any] = []
    filling_entities: list[Any] = []
    for opening in bundle["opening_contract"]["openings"]:
        wall_contract = wall_contract_by_id[opening["owning_wall_id"]]
        wall_entity = wall_by_id[opening["owning_wall_id"]]
        feature_entity = root.create_entity(
            model,
            ifc_class="IfcOpeningElement",
            predefined_type="OPENING",
            name=f"OPENING-{opening['id']}",
        )
        _set_guid(feature_entity, f"opening:{opening['id']}")
        _assign_mesh(
            model,
            body,
            feature_entity,
            _oriented_box(
                wall_contract,
                opening,
                normal_depth_m=float(wall_contract["thickness_m"]) + 0.02,
            ),
            force_faceted_brep=True,
        )
        feature.add_feature(model, feature=feature_entity, element=wall_entity)
        _research_pset(model, feature_entity, bundle, opening["id"])

        ifc_class = "IfcWindow" if opening["kind"] == "window" else "IfcDoor"
        filling = root.create_entity(model, ifc_class=ifc_class, name=f"{ifc_class[3:].upper()}-{opening['id']}")
        _set_guid(filling, f"filling:{ifc_class}:{opening['id']}")
        filling.OverallHeight = float(opening["head_m"]) - float(opening["sill_m"])
        filling.OverallWidth = float(opening["width_m"])
        _assign_mesh(
            model,
            body,
            filling,
            _oriented_box(wall_contract, opening, normal_depth_m=0.03, tangent_inset_m=0.01),
            force_faceted_brep=True,
        )
        feature.add_filling(model, opening=feature_entity, element=filling)
        _research_pset(model, filling, bundle, opening["id"])
        opening_entities.append(feature_entity)
        filling_entities.append(filling)

    annotation = root.create_entity(model, ifc_class="IfcAnnotation", name="RESEARCH-ONLY-NOTICE")
    annotation.Description = "ResearchOnly=true; not verified construction documentation."
    _set_guid(annotation, "annotation:research-only")
    _identity_placement(model, annotation)
    _research_pset(model, annotation, bundle, "research-notice")

    for entity, stable_id in (
        (project_entity, "project"),
        (site, "site"),
        (building, "building"),
        (storey, "storey-01"),
    ):
        _research_pset(model, entity, bundle, stable_id)

    _rel_aggregates(model, "project-site", project_entity, [site])
    _rel_aggregates(model, "site-building", site, [building])
    _rel_aggregates(model, "building-storey", building, [storey])
    _rel_aggregates(model, "storey-spaces", storey, space_entities)
    _rel_contained(model, "storey-products", storey, [slab, *wall_entities, *filling_entities, annotation])

    # API-created property/feature relationships also receive deterministic
    # GUIDs. Creation order is deterministic for a canonical bundle.
    reserved: set[str] = set()
    for index, entity in enumerate(model.by_type("IfcRoot")):
        _set_guid(entity, f"root:{entity.is_a()}:{index}:{getattr(entity, 'Name', '')}")
        if entity.GlobalId in reserved:
            _fail(f"deterministic IFC GUID collision at {entity.is_a()}#{entity.id()}")
        reserved.add(entity.GlobalId)

    _write_ifc(model, output)
    reopened = ifcopenshell.open(os.fspath(output))
    expected_counts = {
        "IfcProject": 1,
        "IfcSite": 1,
        "IfcBuilding": 1,
        "IfcBuildingStorey": 1,
        "IfcSlab": 1,
        "IfcWall": len(wall_entities),
        "IfcSpace": len(space_entities),
        "IfcOpeningElement": len(opening_entities),
        "IfcDoor": len([item for item in bundle["opening_contract"]["openings"] if item["kind"] != "window"]),
        "IfcWindow": len([item for item in bundle["opening_contract"]["openings"] if item["kind"] == "window"]),
    }
    actual_counts = {key: len(reopened.by_type(key)) for key in expected_counts}
    if actual_counts != expected_counts:
        _fail(f"IFC entity count mismatch after reopen: expected={expected_counts}, got={actual_counts}")
    root_guids = [entity.GlobalId for entity in reopened.by_type("IfcRoot")]
    if not all(root_guids) or len(root_guids) != len(set(root_guids)):
        _fail("IFC root GUIDs are missing or duplicated after reopen")
    research_products = [
        *reopened.by_type("IfcProject"),
        *reopened.by_type("IfcSite"),
        *reopened.by_type("IfcBuilding"),
        *reopened.by_type("IfcBuildingStorey"),
        *reopened.by_type("IfcSlab"),
        *reopened.by_type("IfcWall"),
        *reopened.by_type("IfcSpace"),
        *reopened.by_type("IfcOpeningElement"),
        *reopened.by_type("IfcDoor"),
        *reopened.by_type("IfcWindow"),
    ]
    missing_pset = []
    for entity in research_products:
        properties = get_pset(entity, PSET_NAME)
        if not properties or properties.get("ResearchOnly") is not True or not properties.get("AssumptionsJson"):
            missing_pset.append(f"{entity.is_a()}#{entity.id()}")
    if missing_pset:
        _fail(f"research Pset missing or incomplete after reopen: {missing_pset}")
    report = {
        "schema": "research-ifc-report-v1",
        "status": "pass",
        "ifc_schema": reopened.schema,
        "ifcopenshell_version": getattr(ifcopenshell, "version", "unknown"),
        "source_hash": bundle["source_hash"],
        "structure_hash": bundle["structure_hash"],
        "output": os.fspath(output.resolve()),
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "entity_counts": actual_counts,
        "root_guids_unique": True,
        "research_pset_entities_checked": len(research_products),
        "research_only": True,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    report = build(
        args.bundle.expanduser().resolve(),
        args.output.expanduser().resolve(),
        args.report.expanduser().resolve(),
    )
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
