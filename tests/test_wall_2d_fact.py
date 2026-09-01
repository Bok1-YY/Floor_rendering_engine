from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_research_structure_v21 import v21_fixture_with_gap
from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.source_contract_report import _score_source_contract
from tools.goal_loop_v2.wall_2d_fact import (
    apply_authorized_wall_2d_fact,
    build_wall_2d_candidate,
    validate_wall_2d_candidate,
    wall_2d_snapshots,
)


ROOT=Path(__file__).resolve().parents[1]


def _file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _inputs(tmp_path):
    tmp_path.mkdir(parents=True,exist_ok=True);document=v21_fixture_with_gap();source=tmp_path/"source.json";source.write_text(json.dumps(document,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    snapshots=wall_2d_snapshots(document)
    branches=[{"id":row["id"],"status":"source_confirmed_geometry","evidence_refs":["VIEW-EVIDENCE"],"centerline_m":deepcopy(row["centerline_m"]),"raster_evidence":{"median_dark_fraction":.5}} for row in snapshots["branches"]]
    atom_lookup={row["id"]:row for row in document["wall_graph"]["atoms"]}
    atoms=[{"id":row["id"],"branch_id":row["branch_id"],"status":"source_confirmed_geometry","evidence_refs":["VIEW-EVIDENCE"],"centerline_m":deepcopy(row["centerline_m"]),"thickness_m":row["thickness_m"],"base_m":atom_lookup[row["id"]]["base_m"],"height_m":atom_lookup[row["id"]]["height_m"],"start_node_id":row["start_node_id"],"end_node_id":row["end_node_id"],"raster_evidence":{"median_dark_fraction":.5}} for row in snapshots["atoms"]]
    junctions=[{"id":row["id"],"kind":"endpoint","status":"source_confirmed_geometry","axis_point_m":deepcopy(row["axis_point_m"]),"incident_count":row["incident_count"],"near_atoms":[],"solid_union_policy":"not_certified","termination_kind":"not_certified"} for row in snapshots["junctions"]]
    summary={"branches_total":len(branches),"branches_confirmed":len(branches),"atoms_total":len(atoms),"atoms_confirmed":len(atoms),"junctions_total":len(junctions),"junctions_confirmed":len(junctions)}
    audit={"schema":"fixture-wall-graph-audit-v1","source_file":"fixture.png","source_sha256":"a"*64,"input_json_sha256":_file_hash(source),"method":{"scope_exclusions":["z validation","semantics","build"]},"summary":summary,"branches":branches,"atoms":atoms,"junctions":junctions,"topology_intersections":[]}
    paths={"audit":tmp_path/"wall-graph-audit-v1.json","report":tmp_path/"REPORT.md","branches":tmp_path/"branches-audit.json","atoms":tmp_path/"atoms-audit.json","junctions":tmp_path/"junctions-audit.json"}
    for role,value in (("audit",audit),("branches",branches),("atoms",atoms),("junctions",junctions)):paths[role].write_text(json.dumps(value,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    paths["report"].write_text("# Independent 2D wall audit\n\nNo Z, jamb, semantic, adjacency, solid-union, or build claim.\n",encoding="utf-8")
    return document,source,paths,audit


def _wrapper(candidate):return {"schema":"wall-2d-geometry-fact-verdict-v1","candidate":candidate,"candidate_hash":candidate["candidate_hash"],"authority":"independent_reference_reviewer","verdict":"authorize_exact_wall_2d_geometry_fact","build_authorized":False}


def test_pending_candidate_and_authorized_fact_never_mutate_document_or_build_status(tmp_path):
    document,source,paths,_=_inputs(tmp_path);before=canonical_json(document);candidate=build_wall_2d_candidate(document,source,paths)
    with pytest.raises(ValueError):apply_authorized_wall_2d_fact(document,source,paths,candidate)
    fact,report=apply_authorized_wall_2d_fact(document,source,paths,_wrapper(candidate))
    assert canonical_json(document)==before and fact["document_mutated"] is False
    assert fact["ready"] is report["ready"] is False and fact["build_authorized"] is False
    assert any(row["status"]=="candidate" for row in document["wall_graph"]["branches"])


@pytest.mark.parametrize("group",["branch_ids","atom_ids","junction_ids"])
def test_partial_and_endpoint_only_coverage_cannot_be_authorized(tmp_path,group):
    document,source,paths,_=_inputs(tmp_path);candidate=build_wall_2d_candidate(document,source,paths);forged=deepcopy(candidate);forged["fact"]["coverage"][group].pop();from tools.goal_loop_v2.wall_2d_fact import compute_candidate_hash;forged["candidate_hash"]=compute_candidate_hash(forged)
    with pytest.raises(ValueError,match="coverage is partial"):
        validate_wall_2d_candidate(document,forged)


@pytest.mark.parametrize("attack",["thickness","angle","intersection"])
def test_wrong_2d_thickness_angle_and_intersection_evidence_fail_closed(tmp_path,attack):
    document,source,paths,audit=_inputs(tmp_path)
    if attack=="thickness":audit["atoms"][0]["thickness_m"]+=.05
    if attack=="angle":audit["branches"][0]["centerline_m"][1][1]+=.5
    if attack=="intersection":audit["topology_intersections"]=[{"branch_a":"BRANCH-SOUTH","branch_b":"BRANCH-EAST","status":"source_confirmed_intersection"}]
    if attack in {"thickness","angle"}:
        role="atoms" if attack=="thickness" else "branches";paths[role].write_text(json.dumps(audit[role],sort_keys=True,indent=2)+"\n",encoding="utf-8")
    paths["audit"].write_text(json.dumps(audit,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="differs|does not intersect"):
        build_wall_2d_candidate(document,source,paths)


def test_stale_evidence_and_changed_wall_coordinates_do_not_partially_apply(tmp_path):
    document,source,paths,_=_inputs(tmp_path);candidate=build_wall_2d_candidate(document,source,paths);before=canonical_json(document);paths["report"].write_text("forged",encoding="utf-8")
    with pytest.raises(ValueError,match="actual evidence recomputation"):
        apply_authorized_wall_2d_fact(document,source,paths,_wrapper(candidate))
    assert canonical_json(document)==before

    document,source,paths,audit=_inputs(tmp_path/"second");audit["branches"][0]["centerline_m"][0][0]+=.1;paths["branches"].write_text(json.dumps(audit["branches"],sort_keys=True,indent=2)+"\n",encoding="utf-8");paths["audit"].write_text(json.dumps(audit,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="branch trace differs"):
        build_wall_2d_candidate(document,source,paths)


@pytest.mark.parametrize("attack",["z","jamb","semantic","ready"])
def test_fact_schema_rejects_z_jamb_semantic_injection_and_false_ready(tmp_path,attack):
    document,source,paths,_=_inputs(tmp_path);candidate=build_wall_2d_candidate(document,source,paths);forged=deepcopy(candidate)
    if attack=="ready":forged["ready"]=True
    else:forged["fact"][{"z":"height_m","jamb":"jambs","semantic":"adjacency"}[attack]]={"claimed":True}
    from tools.goal_loop_v2.wall_2d_fact import compute_candidate_hash;forged["candidate_hash"]=compute_candidate_hash(forged)
    with pytest.raises(ValueError):validate_wall_2d_candidate(document,forged)


def test_source_score_s05_accepts_only_complete_authorized_sidecar_while_document_stays_candidate(tmp_path):
    document,source,paths,_=_inputs(tmp_path);candidate=build_wall_2d_candidate(document,source,paths);fact,_=apply_authorized_wall_2d_fact(document,source,paths,_wrapper(candidate));contract=json.loads((ROOT/"docs"/"goal_loop_v2"/"goal-contract.json").read_text(encoding="utf-8"));contract["samples"]=["fixture-v2"]
    report_without,_=_score_source_contract(document,contract,{"ready":False},{"lineage_type":"test"})
    report_with,_=_score_source_contract(document,contract,{"ready":False},{"lineage_type":"test"},fact)
    assert next(row for row in report_without["checks"] if row["id"]=="S05_WALL_GRAPH")["status"]=="fail"
    assert next(row for row in report_with["checks"] if row["id"]=="S05_WALL_GRAPH")["status"]=="pass"
    assert any(row["status"]=="candidate" for row in document["wall_graph"]["atoms"])
