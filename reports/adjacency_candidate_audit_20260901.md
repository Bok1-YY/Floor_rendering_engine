# Independent S07 adjacency audit — sample 1308

Read-only audit. No semantic promotion or Blender/IFC authorization.

Authoritative input: `data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json`, structure hash `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`. Score input: `source-contract-score-detail-v5.json` (weighted score 65; S07 hard-fail).

## Current state

`adjacency_truth.status=unresolved`; `entrance_opening_id=null`. Four edges are listed, all `status=candidate`, each citing only `VIEW-CANONICAL`:

| Edge | Candidate relation | Missing closure |
|---|---|---|
| EDGE-OP002 | bedroom_01 ↔ common_core_circulation | OP002 host/effective void candidate |
| EDGE-OP003 | bedroom_01 ↔ west_toilet | no host/effective void |
| EDGE-OP004 | north_toilet ↔ bedroom_02 | no host/effective void |
| EDGE-OP009 | rear_balcony ↔ bedroom_01 | no host/effective void |

All 16 spaces fail S07; reachability fails because no confirmed entrance/root or confirmed edge exists.

## Required evidence per promoted edge

1. Opening ID and exact source segment endpoints in pixel coordinates, with source crop/overlay hash.
2. Real wall atom ID with line overlap/intersection metrics and wall-side normal; midpoint-nearest matching is insufficient.
3. Effective void/jamb endpoints, width, sill/head policy, and classification as door/window/open passage/non-opening.
4. Bounded evidence for `space_a_id` and `space_b_id`; labels/proximity alone do not establish spaces.
5. Traversability: barrier removal in the wall graph plus a path trace between both regions.
6. Entrance/exterior classification where applicable, including a path from exterior to the connected component.
7. Independent Gemini composite review using source image plus labeled crop/overlay, with non-truncated machine-readable output. Gemini remains advisory.
8. Independent verifier report and SHA-256 bindings for source document, wall/opening evidence, crop, Gemini output, and verifier report.

## Required S07 closure

- Confirm an entrance/exterior root (or explicitly source-supported core policy).
- Confirm every required room component's connectivity; no listed space may remain unreachable.
- Record explicit negative decisions for non-door marks, OP011, and demoted duplicate portals.
- Produce a complete graph with no dangling IDs or candidate-only references.
- Recompute reachability independently and remove all S07-blocking unresolved issues.

## Priority queue

- OP002: strongest geometry candidate, but still needs jamb/effective/semantic proof.
- OP001: historical effective void is not enough; proximity is not proof of an exterior opening. Do not infer entrance.
- OP007/OP008: different wall atoms; do not merge from Gemini narrative; prove both sides independently.
- OP003/OP004/OP009: require host-wall, two-sided region, and path evidence.
- OP011: retain unresolved unless a new source crop and independent review close it.

## Gate decision

`S07=FAIL; semantic_promotion=false; build_authorized=false; ready=false.` Next action is evidence acquisition only; this report does not mutate `adjacency_truth`.
