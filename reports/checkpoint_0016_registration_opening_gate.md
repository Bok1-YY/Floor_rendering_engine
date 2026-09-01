# Goal-Loop checkpoint 0016 — registration/opening gate

Sample `1308` remains below the Blender/IFC build gate. Source score is
`65/100`: S01–S05 pass; S06, S07 and S08 fail.

Completed evidence layers:

- wall 2D fact sidecar (21 branches / 35 atoms / 49 junctions);
- opening evidence candidate (12 openings / 16 spaces);
- OP001–OP010 geometry crops and overlays;
- OP002 registration repair (old horizontal packet rejected; corrected vertical packet has 0 px endpoint error);
- OP007/OP008 independent geometry audit (distinct openings);
- deterministic S06 gap inventory;
- deterministic exterior-boundary search (no opening intersects confirmed boundary);
- exterior-root reachability audit (no source-supported root);
- score-neutral off-frame entrance policy;
- human entry annotation protocol.

Safety state remains `semantic_promotion=false`, `build_authorized=false`, and
`ready=false`. No Blender or IFC artifact is authorized until opening semantics,
root/adjacency, and provenance gates pass.
