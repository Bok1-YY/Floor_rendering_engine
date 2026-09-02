# Independent fal/OpenRouter review-bundle verifier — 2026-09-02

Verdict: **ACCEPT as hash-bound advisory evidence; REJECT for promotion**.

Verified commit: `a0bb92ad3b36aa17f4e93908a09207dbba3da122`.

- Bundle candidate: `6193c1f2f25e95ed77416ed4ff18a89492e64d23d0eb72660d0351365d4e4323`.
- Exact, duplicate-free coverage: OP001–OP011.
- Provider/model: fal OpenRouter Vision / `google/gemini-2.5-flash`.
- fal-reported total cost: `$0.0037274`.
- Strict result and bundle suites: `4 passed`.

The verifier independently reopened all external result files, recomputed canonical result hashes, reopened every registered full/crop overlay, revalidated the seven-field JSON schema, and reproduced the bundle hash. Retry results were correctly selected only for OP005 and OP010.

Important mismatches remain blocking rather than promoting: OP001's visual `door` does not create an exterior root; OP005's visual `door` cannot revive rejected history; OP011's visual `door` conflicts with the unresolved source `glazed_interface` classification.

Rehashed promotion, image drift, missing/duplicate coverage, wrong IDs, and malformed output are rejected.

`advisory_only=true`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`

This bundle closes the missing external-review transport gap. It does not close host, effective-void, jamb, bounded-space, cut, adjacency, root, or human-compatible confirmation gates.
