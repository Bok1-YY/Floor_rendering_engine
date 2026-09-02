# Independent OP009 / OP010 adjudication verifier — 2026-09-02

Verdict: **ACCEPT only as a bounded, fail-closed geometry candidate; REJECT for promotion or build**.

Verified commit: `b93192519d42701ae8dbe753f74259233a061cbe`.

## Bindings

- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Evidence file: `a757def188963df0096ffa9684d93b5a74ff0803d11d2bf45a423d4319b26e66`
- Evidence canonical JSON: `2391b9d4d58392a835dc004b974f2ed6e9fa5e071ee50f3bbd955bf6cf632c27`
- Opening-side candidate: `9009000bdfdc8f3eae112aaa258af3e55c3ca3dab19c03e31bce9b633788ea23`
- Target-aware walls: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`
- Adjudication candidate: `c4ccbf3fe02bc77f7a0be0a7639cb4221a4626ec8d9d08c00c74a5ad2392aa79`

## OP009

- Registered to `ATOM-WB005-01` with `0 px` endpoint error.
- Host parameters: `[0.333889783, 0.669449144]`.
- Geometric end supports: `1.463687 m` and `1.44905 m`.
- Left side has one unconfirmed candidate, `rear_balcony`.
- Right side ranks `north_toilet`, `bedroom_01`, and `bedroom_corridor`; the leading result is close-ranked and ambiguous.
- No space pair is selected.

## OP010

- Registered to `ATOM-WB003-03` with `0 px` endpoint error.
- Host parameters: `[0.56208866, 0.740236927]`.
- Geometric end supports: `4.687458 m` and `2.166257 m`.
- Left side ranks `kitchen`, `living_hall`, and `lobby`; this remains proximity evidence only.
- Right side has one unconfirmed candidate, `front_balcony`.
- No space pair is selected, and no exterior-boundary intersection is confirmed.

## Distinct policy and tamper checks

The openings share a horizontal orientation but have different IDs, source segments, wall atoms, wall thicknesses, and independent policy keys. `shared_cut_or_adjacency_policy=false`.

All four overlay hashes were independently reopened and verified. Forged room-pair selection, build promotion, evidence drift, and overlay-byte tampering are rejected. Direct script invocation from outside the repository succeeds. Targeted result: `4 passed`.

## Gate decision

Missing source host payloads, effective voids, source jambs, confirmed two-sided spaces, physical wall breaks, adjacency, non-assumed Z, usable Gemini reviews, and human-compatible review remain hard blockers.

`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`

The source score remains `65/100`.
