# Independent OP005 / OP006 adjudication verifier — 2026-09-02

Verdict: **ACCEPT only as a bounded, fail-closed candidate**.

Verified commit: `73c0cb12c3a158ba03b57eec7b48a981f0f73d32`.

## Recomputed bindings

- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Evidence file: `fe271884087318eed168d5fac718806f720197ea83108285465b0acdafcb38b9`
- Opening-side candidate: `9009000bdfdc8f3eae112aaa258af3e55c3ca3dab19c03e31bce9b633788ea23`
- Target-aware walls: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`
- Candidate packet: `590433f7f23d2e71ed45326531a61c9aae89748560302ed0cbf4707ad21c200e`

## OP005

OP005 remains an `unknown`, hostless candidate. `host_candidate=null` and `host_support_candidate=null`. No rejected legacy host, effective void, or door payload is resurrected. Its north-side proximity ranking remains close and ambiguous.

## OP006

OP006 retains a source-observation `door` label but remains excluded from construction. Its candidate host is `ATOM-WB007-02`. Recomputed host parameters are `[0.02690102, 0.291774685]`; geometric supports are `0.094538 m` and `2.488909491 m`. The minimum is below the `0.12 m` policy threshold, so `GEOMETRIC_JAMB_INSUFFICIENT` remains active.

## Attacks and tests

- Rejected fabrication of an OP005 host.
- Rejected unconfirmed OP006 room-pair selection.
- Rejected build/semantic promotion.
- Reopened and verified all overlay bytes; tampering fails closed.
- Verified direct invocation outside the repository.
- Targeted evidence and packet suites: `6 passed`.

`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`

This verdict does not authorize any wall cut, adjacency, source-score increase, Blender object, or IFC relationship.
