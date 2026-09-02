# Independent OP011 adjudication verifier — 2026-09-02

Verdict: **ACCEPT only as a bounded fail-closed coordinate candidate**.

Verified commit: `1028cfb9958f0d7bcb6ed66f70a2e128f16d9aa3`.

- Source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Evidence JSON: `746a1a3a71d3095aaf3eea010ab5dc8b27683752abf35e5fe3573bb0ae40e637`
- Opening-side candidate: `9009000bdfdc8f3eae112aaa258af3e55c3ca3dab19c03e31bce9b633788ea23`
- Target-aware walls: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`
- OP011 packet: `704bfcc5a70052010f995aa5de9adb7ac81e9d1c96b57665a7c5fedc2401064e`

The source-confirmed `glazed_interface` label is retained only as a coordinate observation. Active status and decision remain unresolved. Host, effective void, jambs, semantic subtype, selected room pair, cut, adjacency, and traversability all remain absent or false. The left-side proximity ranking is close/ambiguous and is not promoted.

Overlay bytes were reopened and matched the manifest: crop `166aecf6b188fa35d7c222df2350d53375125fc7092cfa24e8eafc3d09a9a4dc`; full `ac1216ee6a0f3c5234456b41b06a45bf6ca12086cd89e7202ba633f6b1cd92c2`. Tampering fails closed; direct invocation outside the repository succeeds. Focused evidence/packet suites: `5 passed`.

The packet does not perform semantic image classification; this is intentional because the Gemini attempt failed and coordinate evidence cannot prove a door/window/cut subtype.

`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`
