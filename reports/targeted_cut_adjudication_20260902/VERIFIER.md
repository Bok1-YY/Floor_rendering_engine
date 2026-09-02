# Independent repaired targeted-cut verifier — 2026-09-02

Verdict: **ACCEPT the repaired evidence candidate; no cut/pair/adjacency promotion**.

Verified commit: `195254a557561e032d26299d3c3a63dd42f0ca4b`.

- Packet hash: `e8c906b611ca190fd9df93716ff047362abff1a1dafa43e660eae735a9e6440b`.
- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`.
- Target-aware walls: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`.
- Cut-impact matrix: `ffe9dc923fd998ab28e984d75951b913f21af8e15f0c45cb9d4a42de6ef8f613`.

Each F-A/F-B pair is now a distinct single-anchor pre-cut polygon with different coordinates and hashes:

- OP003: `bedroom_01/west_toilet`; minimum same-wall jamb `0 m` — insufficient.
- OP004: `bedroom_02/north_toilet`; minimum jamb `0.879937 m` — sufficient.
- OP009: `bedroom_01/rear_balcony`; minimum jamb `1.44905 m` — sufficient.

Registration error is `0 px`; all three remain stable across configured clearance and endpoint perturbations. Full/crop images contain the registered void, host centerline, both wall faces, and both neutral pre-cut face outlines.

The original commit `4be49a4` was rejected because F-A/F-B duplicated one polygon. The repaired generator and test explicitly require different space IDs, polygon hashes, and polygon coordinates.

`cut_confirmation=false`  
`pair_confirmation=false`  
`adjacency_confirmation=false`  
`semantic_promotion=false`  
`build_authorized=false`

Only OP004 and OP009 proceed to targeted visual geometry review. OP003 remains blocked on its zero same-wall jamb unless independent end-condition evidence proves a valid return-wall jamb.
