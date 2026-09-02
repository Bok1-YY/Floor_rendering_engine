# Independent fal room-pair trial verifier — 2026-09-02

Verdict: **ACCEPT as a bounded research bundle; REJECT as a pair-confirmation method**.

Verified commit: `df86a29dc0deef30869c418016afab9aa0581365`.

- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`.
- Composite manifest: `0a1403a34354f407032075d5acddbd8a08d5b99ac488235624ccf866c63df239`.
- Bundle candidate: `d5ce610645e5ca81d6d93cb547107b4b45c9a65f5813aabd5cec81f7966718b4`.
- Exact coverage: OP001–OP011.
- Usable: 10; unusable strict output: 1 (OP002).
- Both-top-ranked: 7; partial-top-ranked: 3.
- Total fal-reported cost: `$0.0034065`.
- Focused suite: `2 passed`.

OP001 (`lift_shaft/lobby`), OP003 (`rear_balcony/west_toilet`), and OP007 (`lobby/wc`) demonstrate that neutral-marker VLM selection is not bounded-room proof. The seven both-top-ranked responses are also only consistency with a proximity ranking; they do not prove host ownership, a physical cut, effective void, jambs, traversability, or adjacency.

The bundle correctly rejects rehashed promotion, missing/duplicate coverage, image drift, invalid labels, and malformed JSON. All pair and adjacency confirmations remain false.

Method decision: stop using further VLM pair voting as a confirmation mechanism. Pivot to source-bound cut-impact topology, wall-face ownership, room polygons, effective-void/jamb validation, and traversability checks. fal output remains advisory only.

`pair_confirmation=false`  
`adjacency_confirmation=false`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`
