# Independent opening-candidate validation (2026-09-01)

## Scope

Read-only validation of `data/goal_loop_v2/references/1308/opening-wall-space-evidence-candidate-v1.json` against the current coordinate-authorized V2.1 source document. The audit checks coverage, provenance, fail-closed flags, OP002 geometry, and exclusion of the superseded horizontal OP002 packet.

## Result

**PASS — candidate is internally valid and does not consume the stale horizontal OP002 packet.** This is only an evidence-candidate validation; it is not semantic authorization or build authorization.

## Evidence

- Candidate validation with `validate_opening_evidence_candidate`: `VALID`, 12 openings.
- Candidate SHA-256: `ef35f0742abcffdfb891bd7ed4d6006a4e49e5034e2a5457f97b716e67f23b81`.
- Candidate logical hash: `e3bf7bd78dd489746a055b2dcbb2a23e22c164f3164757660590afdbe13a088e`.
- Source document file SHA-256: `db4fa7a656b8a0267494d4e299a436503d219ba57f06f168dd5150427f299eb1`.
- Candidate source structure hash equals the source document structure hash.
- Opening and space coverage both match the source document (12 openings, 16 spaces).
- Candidate flags remain `pending_independent_review`, `build_authorized=false`, `ready=false`; each opening has `semantic_promotion=false` and `space_relation_status=candidate_only`.
- All wall links point to existing wall atoms and use `geometric_wall_candidate`; no semantic claim is encoded.

## OP002 check

The candidate's OP002 metric segment is vertical and exactly matches the governing source contract:

```text
(4.423994413407822, 10.690147268408552)
→ (4.423994413407822, 9.802937767220904)
```

Its nearest wall link is `ATOM-WB006-02` with `segment_distance_m=0.0` and `midpoint_distance_m=0.0`.

The superseded horizontal packet remains outside the candidate's evidence bindings:

- stale packet: `reports/op002_door_evidence_20260901/op002-evidence.json`
- stale packet SHA-256: `b22095ed2be8f7762b00374dbaa90ac1d2fb082acec863e70b640d13ee2f93cb`
- corrected vertical packet: `reports/op002_vertical_evidence_20260901/op002-vertical-evidence.json`
- corrected vertical packet SHA-256: `5e90c21081596d24cf18ee99bbd6837f671623593cee3da0650a6f6a56473b6e`

The candidate does not bind either OP002-specific packet, so this audit confirms that the stale packet is not consumed by the generic candidate. The corrected packet is separately available for a later adjudication package.

## Limitations / gate outcome

This validation does **not** prove door type, jamb, opening height, two-sided space ownership, traversability, adjacency, or entrance/root semantics. It does not authorize source mutation, semantic promotion, Blender, or IFC.

```text
opening_candidate_valid = true
stale_horizontal_op002_consumed = false
semantic_promotion = false
build_authorized = false
ready = false
```
