# Independent exterior-root and minimal-reachability audit — sample 1308

Read-only audit. This report does not promote semantic facts, mutate `adjacency_truth`, or authorize Blender/IFC.

## Governing source

- Source: `data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json`
- Source structure hash: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Outer boundary status: `confirmed`
- Existing root: `ROOT-EXTERIOR`, `status=unresolved`, `opening_id=null`

## Exterior-root finding

No source-supported exterior opening can be promoted from the current contract.

The only opening explicitly carrying `build_kind=entrance` is `OP001`, but its governing host is `ATOM-WB016-02`. That atom is an internal horizontal wall segment (`y≈5.63 m`, x≈2.18–4.40 m), not a segment of the confirmed outer boundary. Its `side_a_space_id` and `side_b_space_id` are null, and its source contract currently has `traversable=false`. The label `entrance` therefore cannot establish an exterior root.

The confirmed outer boundary is a polygon with the north edge at `y=15.308 m`, east edge at `x=9.17 m`, and a south balcony projection down to `y=-2.070434532 m`. No current opening has a source segment demonstrably intersecting the outer boundary and a bounded interior space on the opposite side. `OP009` and `OP010` are `glazed_access_door` candidates on balcony-facing internal/front edges, but neither has a confirmed host, effective void, side spaces, or root path.

Result:

```text
entrance_root = unresolved
root_opening_id = null
exterior_to_component_path = not demonstrated
```

## Minimal reachability result

Because there is no confirmed root and no confirmed traversable edge, the current graph cannot prove reachability for any of the 16 spaces. The candidate adjacency edges (`OP002`, `OP003`, `OP004`, `OP009`) are not usable as paths: each lacks at least one of effective void, bounded side spaces, host ownership, and barrier-removed path trace. `OP002` has the strongest geometry and corrected registration, but remains candidate-only; registration alone is not traversability.

The minimum graph closure required before S07 can pass is:

```text
ROOT-EXTERIOR
  -> one source-supported entrance opening
  -> bounded interior space
  -> confirmed traversable opening edges
  -> all 16 space IDs reachable
```

Required proof for each edge is: exact source endpoints, host wall atom, effective void/jamb, bounded regions on both sides, and a deterministic path trace after barrier removal. A label, nearest-room guess, door-swing sketch, or Gemini narrative is insufficient.

## Negative decisions retained

- `OP001`: entrance-looking mark, but not an exterior root without boundary intersection and two-sided proof.
- `OP002`: registration-correct candidate; not a confirmed traversable edge.
- `OP003`, `OP004`, `OP009`: candidate opening geometry only; no reachability proof.
- `OP005` and `PORTAL-WB011-WB006-01`: demoted evidence-only/rejected interpretations remain excluded.
- `OP011`: unresolved; excluded from reachability.

## Gate decision

```text
S07 = FAIL
entrance_confirmation = false
reachability_confirmation = false
semantic_promotion = false
build_authorized = false
ready = false
```

Next evidence task: identify a source segment that actually crosses the confirmed outer boundary (or explicitly obtain an authorized source policy for an off-frame entrance), then close the root edge before attempting a full reachability traversal.
