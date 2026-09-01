from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
import pytest
from tests.test_op002_dedup import _inputs
from tests.test_research_structure_v2 import v2_fixture
from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.source_correction import apply_authorized_source_correction,apply_source_corrections

H=lambda value:hashlib.sha256(canonical_json(value)).hexdigest()

def _write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8");return path
def _filehash(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def _wrapper(tmp_path):
    document,evidence,manifest=_inputs();result,_=apply_source_corrections(document,evidence,manifest)
    paths={"source_document":_write(tmp_path/"source.json",document),"evidence":_write(tmp_path/"evidence.json",evidence),"manifest":_write(tmp_path/"manifest.json",manifest),"result_candidate":_write(tmp_path/"result.json",result)}
    exact={"source_document":{"path":str(paths["source_document"]),"file_sha256":_filehash(paths["source_document"]),"structure_hash":document["structure_hash"]},"evidence":{"path":str(paths["evidence"]),"file_sha256":_filehash(paths["evidence"]),"canonical_sha256":H(evidence)},"manifest":{"path":str(paths["manifest"]),"file_sha256":_filehash(paths["manifest"]),"canonical_sha256":H(manifest)},"result_candidate":{"path":str(paths["result_candidate"]),"file_sha256":_filehash(paths["result_candidate"]),"structure_hash":result["structure_hash"]}}
    wrapper={"schema":"goal-loop-v2-authorized-source-correction-v1","authority":"independent_reference_reviewer","verdict":"authorize_exact_source_correction","application_authorized":False,"build_authorized":False,"scope":"generic dedup","exact_inputs":exact,"constraints":["exact only"]}
    return wrapper,paths,result

def test_exact_authorized_wrapper_replays_pending_correction_and_stays_not_ready(tmp_path):
    wrapper,_,expected=_wrapper(tmp_path);result,report=apply_authorized_source_correction(wrapper)
    assert canonical_json(result)==canonical_json(expected)
    assert report["canonical_result_equal"] is True and report["ready"] is False
    assert result["adjacency_truth"]["status"]=="unresolved"
    assert sum(row["build_disposition"] in {"cut","place_in_preexisting_gap"} for row in result["opening_contract"]["openings"])==1

@pytest.mark.parametrize("field,value",[("schema","evil"),("authority","builder"),("verdict","authorize_build"),("application_authorized",True),("build_authorized",True)])
def test_wrapper_authority_and_flags_are_exact(tmp_path,field,value):
    wrapper,_,_=_wrapper(tmp_path);wrapper[field]=value
    with pytest.raises(ValueError,match="invalid authorized"):apply_authorized_source_correction(wrapper)

@pytest.mark.parametrize("target,field",[("source_document","file_sha256"),("source_document","structure_hash"),("evidence","file_sha256"),("evidence","canonical_sha256"),("manifest","file_sha256"),("manifest","canonical_sha256"),("result_candidate","file_sha256"),("result_candidate","structure_hash")])
def test_fake_or_stale_exact_input_hashes_are_rejected(tmp_path,target,field):
    wrapper,_,_=_wrapper(tmp_path);wrapper["exact_inputs"][target][field]="f"*64
    with pytest.raises(ValueError):apply_authorized_source_correction(wrapper)

def test_tampered_bytes_and_forged_result_candidate_are_rejected(tmp_path):
    wrapper,paths,result=_wrapper(tmp_path);paths["evidence"].write_text("{}",encoding="utf-8")
    with pytest.raises(ValueError,match="byte hash mismatch"):apply_authorized_source_correction(wrapper)
    wrapper,paths,result=_wrapper(tmp_path);result["outer_boundary"]["status"]="candidate";from tools.fastloop_research.v21_contract import compute_v21_structure_hash;result["structure_hash"]=compute_v21_structure_hash(result);_write(paths["result_candidate"],result);wrapper["exact_inputs"]["result_candidate"].update(file_sha256=_filehash(paths["result_candidate"]),structure_hash=result["structure_hash"])
    with pytest.raises(ValueError,match="differs from exact result candidate"):apply_authorized_source_correction(wrapper)

def test_v2_source_input_is_rejected_and_function_does_not_write_partial_outputs(tmp_path):
    wrapper,paths,_=_wrapper(tmp_path);legacy=v2_fixture();_write(paths["source_document"],legacy);wrapper["exact_inputs"]["source_document"].update(file_sha256=_filehash(paths["source_document"]),structure_hash=legacy["structure_hash"])
    sentinel=tmp_path/"must-not-exist.json"
    with pytest.raises(Exception):apply_authorized_source_correction(wrapper)
    assert not sentinel.exists()
