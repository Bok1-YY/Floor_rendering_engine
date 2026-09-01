# Independent opening geometry audit — sample 1308

Status: `evidence_only_pending_review`

This audit is a geometry-only read of `reference-op002-dedup-authorized-v21.json`,
the authorized wall-2D fact, and the opening-wall-space candidate. It does not
promote semantic labels, adjacency, door/window type, height, or Blender build
authorization.

## Deterministic measurements

| opening | source segment (m) | midpoint | nearest wall atom | distance | geometry finding |
|---|---|---:|---|---:|---|
| OP001 | (3.432346,5.628690)–(4.398380,5.628690) | (3.915363,5.628690) | ATOM-WB016-02 | 0.0156 m | Horizontal segment lies near WB016; its right end approaches the WB006 return, but the segment/return relation is not a closed cut proof. |
| OP002 | (4.423994,10.690147)–(4.423994,9.802938) | (4.423994,10.246543) | ATOM-WB006-02 | 0.0000 m | Exact collinear overlap with WB006-02; this supports host geometry only. It does not prove door semantics or traversability. |
| OP007 | (1.350251,4.028804)–(1.350251,3.272494) | (1.350251,3.650649) | ATOM-WB019-01 | 0.0000 m | Exact collinear overlap with WB019-01. No side-space or jamb evidence is present in the source contract. |
| OP008 | (1.134358,4.254242)–(2.085754,4.254242) | (1.610056,4.254242) | ATOM-WB018-01 | 0.0000 m | Exact collinear overlap with WB018-01. It is not geometrically a duplicate of OP007: different axis and host branch. |

## Contradictions and limits

1. OP001's nominal length is 0.966034 m, while its effective confirmed void is
   0.931648 m and ends at x=4.363994. The 0.034386 m discrepancy is an
   endpoint clipping operation, not evidence that the original symbol had that
   width. The source contract also records the WB006 return continuity blocker;
   therefore OP001 must remain unresolved for build purposes.
2. OP002 is the only audited target whose segment has exact zero distance to its
   current host atom. Its prior horizontal interpretation is explicitly
   rejected, and must not be reintroduced as a second portal.
3. OP007 and OP008 are spatially distinct and have distinct exact host atoms.
   A claim that they are the same bathroom door is unsupported by this geometry
   audit. Conversely, geometry alone cannot establish either opening's two
   spaces, hinge, swing, sill, or head.
4. All four records lack complete source-backed `side_a_space_id` and
   `side_b_space_id` pairs (only OP002 carries candidate pair labels). The
   opening-to-wall evidence candidate therefore remains candidate-only.

## Gate decision

`semantic_promotion=false`, `build_authorized=false`, `ready=false`.

Recommended next evidence: independent pixel crop/overlay for each opening,
then a separately reviewed wall-face/jamb and two-sided space assignment. Do not
generate Blender/IFC geometry from this report.

