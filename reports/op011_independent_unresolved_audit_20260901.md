# OP011 independent source-geometry audit — 2026-09-01

## Scope and independence

This is a read-only audit of OP011 in the current authorized coordinate source
document. It does not mutate the source document, promote semantics, or authorize
Blender/IFC construction. Historical interpretations and other agents' outputs
are not used as evidence for a promotion decision.

## Governing input

- File: `data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json`
- SHA-256: `DB4FA7A656B8A0267494D4E299A436503D219BA57F06F168DD5150427F299EB1`
- Opening contract: `opening-contract-v2.1`
- Opening: `OP011`
- Anchor: `ANCHOR-OP011`
- Evidence reference: `VIEW-CANONICAL`

## Observed source facts

The source record describes OP011 as a vertical `glazed_interface` segment:

```text
metric start = (0.764777, 3.134322)
metric end   = (0.764777, 2.341651)
nominal width = 0.7927 m
source status = confirmed
```

The contract explicitly contains no `host`, no `effective_void`, no jamb records,
and no `side_a_space_id` or `side_b_space_id`. It also has no swing direction.
The build disposition is `exclude_pending_resolution`, `build_kind` is null, and
the opening status is `unresolved`.

## Promotion checks

OP011 cannot be promoted from the current evidence because the source record does
not establish any of the following:

1. A unique owning wall atom and wall-face ownership.
2. A physical cut/opening versus a glazed, non-traversable interface.
3. Door/window/portal classification.
4. Jambs, effective void, sill/head heights, or construction depth.
5. The two bounded spaces on either side.
6. Traversability or an adjacency edge/root path.
7. An independent, complete image review that agrees with the geometry.

The metric segment and its nominal width are coordinate facts only. They are not
enough to infer a door, a window, or a connection. In particular, converting the
segment into a wall cut would fabricate a build operation and could create a false
adjacency edge.

## Decision

```text
geometry_coordinate_fact = retained
semantic_status = unresolved
traversable = false (not proven; conservative contract value)
semantic_promotion = false
build_authorized = false
ready = false
```

The correct next evidence, if OP011 is revisited, is a new source-registered local
crop/full overlay plus independent image review and wall/space ownership checks.
Until those are available, OP011 must remain unresolved and excluded from all
Blender/IFC wall cuts and adjacency construction.
