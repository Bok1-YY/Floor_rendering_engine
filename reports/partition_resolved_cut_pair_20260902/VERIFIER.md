# Independent partition-resolved cut/pair verifier — 2026-09-02

Verdict: **ACCEPT as bounded pair candidates; no pair/adjacency/cut application**.

- Candidate hash: `5fb410dcee736042652f2a8b290305737b3e4e10178e7263b9ef8e998400dae2`.
- File SHA-256: `041a86ffc8740c38f23ade29b9455d2fdc9af397a6fb68f0e0e1c56b4322cf72`.
- Cut matrix: `8feb38e0e1169b9fe0750578836dacd4073c5461014d9af3755f8c0e7317e0fa`.
- Semantic partition: `ea324b82e533c2346883d440ec0bec71b7e2ea8509853c73b6a3fa15547ce6a3`.

Directed unique candidates are OP002 corridor→bedroom_01, OP006 bedroom_03→corridor, OP007 lobby→wc, and OP010 kitchen→front_balcony. OP008 remains ambiguous with non-public anchors `bath` and `wc` and has no pair.

The implementation now includes exact reconstruction validation and explicit attacks for reversed pairs, forced OP008 selection, rehashed promotion, and direct CLI execution. VLM results are not consumed.

`pair_confirmation=false`  
`adjacency_confirmation=false`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`
