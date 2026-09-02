# Independent OP007 / OP008 adjudication verifier — 2026-09-02

Verdict: **ACCEPT as a bounded, fail-closed candidate only**.

The verifier inspected commit `c2ed9cb15cb99064b00329fea02030f7c0a8f23e`, then independently re-verified the artifact-binding hardening in commit `9b536749f02cb5029f6cd65b226b4019de67fed2`. This verdict does not confirm opening semantics, a physical wall cut, adjacency, score promotion, or build readiness.

## Bound inputs

- Source structure hash: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Evidence file SHA-256: `a1cc8910433eb48fa93412dcddd5cfe1bd0182ab8c9ec7c1a09db727c72fd4d4`
- Evidence canonical SHA-256: `20be3eedb92a3af67c5eb7bd6ea83dae78036287d86bb2ddb3a94379d34c9762`
- Opening-side candidate hash: `9009000bdfdc8f3eae112aaa258af3e55c3ca3dab19c03e31bce9b633788ea23`
- Target-aware wall candidate hash: `fd4e8b2b787715800fadcbd3bf4867b27a990baf8ae3d3c8a348834eb3dc9aed`
- Final packet hash: `873c2d87e27c39509588a01f7b9e85c0a7dd05a3441f816bf8d175d2af3b73e`

## Independent findings

- OP007 and OP008 are distinct candidates: different IDs, different exact host atoms, and orthogonal registered pixel directions (dot product `0.0`).
- OP007 binds to `ATOM-WB019-01`; its provisional pair is `wc / kitchen`; its minimum geometric end support is `0.06545 m`.
- OP008 binds to `ATOM-WB018-01`; its provisional pair is `bath / kitchen`; its minimum geometric end support is `0.035139857 m`.
- Both end supports are below the candidate policy threshold of `0.12 m`, so both records contain `JAMB_INSUFFICIENT_AT_ENDPOINT`.
- Both records retain all source, void, jamb, side-space, Gemini, and human-review blockers. They remain `unresolved_candidate`.
- A forged/rehashed host merge is rejected as evidence drift. A promotion attempt is rejected before reconstruction.
- Direct script invocation from outside the repository succeeds.

## Overlay byte-chain verification

The follow-up implementation reopens all four portable evidence-directory files and validates actual bytes against the manifest:

- OP007 crop: `94441` bytes, `0257d7b0b05b819b41f24c0cd29ab32ad6013597d914e3293b8dc1c8fcdaeed7`
- OP007 full: `1619433` bytes, `67e9611725d0f997ca8778907e92821cb8339fb2eeda25e441c0d0bd88589418`
- OP008 crop: `108851` bytes, `4fa45b205e1cdd270936caa4df193dca27803a321a6635d52a28dc70f25c78d5`
- OP008 full: `1619744` bytes, `0faef05ef08db5d6636651b49cf7bfdd72ea00c320aa1e487710d6d220726106`

The tamper test changes the portable OP008 crop bytes and packet construction fails closed with `artifact hash drift`.

## Executed checks

- Follow-up targeted suite: `4 passed`.
- Broader repository suite at the immediately preceding implementation point: `535 passed, 1 skipped`.
- `git diff --check`: clean before each implementation commit.

## Gate decision

`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`

The source score remains `65/100`. A later Gemini review may be advisory evidence, but cannot replace missing source-confirmed host/void/jamb/side-space facts or human-compatible review.
