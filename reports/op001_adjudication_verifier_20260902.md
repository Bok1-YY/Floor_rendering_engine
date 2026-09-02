# Independent OP001 adjudication verifier — 2026-09-02

Verdict: **ACCEPT only as a bounded fail-closed candidate; REJECT for exterior-root, score, or build promotion**.

Verified commit: `64484447494567fd4d263ec1cdf01b6fd4374e74`.

## Bindings

- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Evidence file: `a0c3e4b445eef8ebd4502f4cfde468caf7b4ad9301cb2e4f9a2173deb8760c6f`
- Evidence canonical JSON: `cc58d38246a6aea64f5837617f643ef6ab78edb150c6d6794945b9984ccd1f18`
- Opening-side candidate: `9009000bdfdc8f3eae112aaa258af3e55c3ca3dab19c03e31bce9b633788ea23`
- Target-aware walls: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`
- OP001 packet: `6a5b20d7cbb58ef8fead76984e32b10a54fb61e7ff7d36ada59352e7bf4a0c31`

## Independent result

The source contains an `entrance_symbol`, a source host/effective-void/jamb claim, and `build_kind=entrance`; however, active status is candidate and `traversable=false`. Independent evidence binds the host only by distance (`0.011283 m` segment distance), explicitly sets `closed_wall_break_proven=false`, and proves only a visible dashed swing/jamb observation.

Both opening endpoints lie inside the confirmed footprint. The segment intersects no confirmed outer-boundary edge, has no outside-side region, and cannot establish an exterior entrance root:

```text
intersects_outer_boundary = false
endpoints_inside_confirmed_footprint = [true, true]
outside_side_region_confirmed = false
exterior_entrance_root_confirmed = false
```

The provisional north-side ranking starts with `common_core_circulation`; the south-side ranking starts with `lobby`. No space pair is selected or confirmed.

Overlay bytes were independently reopened: crop `7946d93e0981eb481510eab9c1784184d393959ed2e111984aef207141bf6702`; full `cd50ea82cd06c2d731a7e5b74ff0a46033262035999cd0486a6cbfd02b946dc3`.

Forged room pair, cut, exterior root, and build promotion are rejected; overlay tampering fails closed; direct script invocation works. Targeted packet suite: `4 passed`.

`entrance_confirmation=false`  
`exterior_root_confirmation=false`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`

An entrance symbol must not be used as the S07 exterior root.
