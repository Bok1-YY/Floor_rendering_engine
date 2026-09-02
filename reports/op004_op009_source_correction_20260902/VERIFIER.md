# Independent OP004 / OP009 2D correction-candidate verifier — 2026-09-02

Verdict: **ACCEPT repaired 2D candidates; no source application or build promotion**.

Verified commit: `ec61fb167f0c09a8045c4e2f362a7723b99bef82`.

- Outer candidate: `0043b67b78ffa2db2a7f75e2822d54027ab25d906bfd6060f0af36c2067560c9`.
- Packet file: `a31e82ca1c4e58f1ac132129765509cbf57a4aa2d89f12c235e57041dd6f4244`.
- OP004 packet: `bdc1ed691dbfb799ebe2e6ed70bf3a9eb9fb897fe48f944099d344cb4bdf0b6c`.
- OP009 packet: `a6d4371a7525ec945d6715c7e756fcb44b83af4d1d306e27651464b57d19a052`.

Directed sides are now derived from signed normals rather than sorted space IDs:

- OP004: side A/east/left `bedroom_02` (`+1.68324 m`); side B/west/right `north_toilet` (`-0.684274 m`).
- OP009: side A/north/left `rear_balcony` (`+0.530871 m`); side B/south/right `bedroom_01` (`-2.7179885 m`).

The previous OP009 reversal is rejected even if both inner and outer hashes are recomputed. Direct CLI regeneration reproduces the outer hash. Source is unchanged.

No build disposition, build kind, head/sill/Z, swing, traversability, adjacency, score effect, or application authorization is present. OP009's glazed-interface/access-door conflict and both candidates' final human/source authority remain blocking.

`semantic_promotion=false`  
`adjacency_confirmation=false`  
`score_effect=none`  
`build_authorized=false`
