# Independent repaired semantic-partition verifier — 2026-09-02

Verdict: **ACCEPT as a bounded research-only semantic partition**.

Verified commit: `fa646c2b89a93a61876a1def03a463f0c3aad9a5`.

- Candidate: `cc2f512bc8db5c6516548e5a747251789290071a4277888d553dfe3c8369b5aa`.
- Cut matrix file: `8feb38e0e1169b9fe0750578836dacd4073c5461014d9af3755f8c0e7317e0fa`.
- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`.
- Direct CLI reproduced the candidate hash; focused suite: `3 passed`.

The four clipped Voronoi cells cover the public multi-anchor face exactly with zero pairwise overlap and all anchors covered. Lobby is correctly preserved as two polygons. Opening eligibility is exactly OP002/006/007/008/010.

At host-half-thickness plus every tested epsilon, stable public-side cells are: OP002 corridor, OP006 corridor, OP007 lobby, OP008 lobby, OP010 kitchen. Empty opposite sides are outside the public partition and have no tie, rather than being missing in-wall samples.

`research_only=true`  
`room_polygon_confirmation=false`  
`pair_confirmation=false`  
`adjacency_confirmation=false`  
`score_effect=none`  
`build_authorized=false`
