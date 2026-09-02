"""Single source of truth for opening jamb-support policy."""
from __future__ import annotations
import math
from typing import Any,Mapping
def minimum_jamb_support_m(document:Mapping[str,Any])->float:
    contract=document.get('opening_contract')
    if not isinstance(contract,Mapping):raise ValueError('opening contract missing for jamb policy')
    try:value=float(contract['minimum_jamb_support_m'])
    except (KeyError,TypeError,ValueError) as exc:raise ValueError('minimum jamb support policy missing/nonnumeric') from exc
    if not math.isfinite(value) or value<=0 or value>1:raise ValueError('minimum jamb support policy out of range')
    return value
def jamb_policy_binding(document:Mapping[str,Any])->dict[str,Any]:
    return {'source_structure_hash':document.get('structure_hash'),'minimum_jamb_support_m':minimum_jamb_support_m(document),'source_path':'opening_contract.minimum_jamb_support_m','wall_thickness_is_not_policy':True}
__all__=['minimum_jamb_support_m','jamb_policy_binding']
