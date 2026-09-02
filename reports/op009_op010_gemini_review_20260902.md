# OP009 / OP010 Gemini composite review attempt — 2026-09-02

Two real single-opening requests were made with the configured Gemini key and explicit `http://127.0.0.1:7897` proxy. Each request used exactly the registered full overlay and crop overlay for one opening. No request combined or cross-contaminated OP009 and OP010.

## OP009

- HTTP: `400`
- API status: `FAILED_PRECONDITION`
- Message: `User location is not supported for the API use.`
- Attempts: `1` (the retry policy does not retry a non-transient HTTP 400)
- Prompt SHA-256: `7bb895fd456a4930e9e969356c11d3622866f14a235af2e379813d93908f840c`
- External result SHA-256: `b03deb1f5481c7afafddf7a9422a4fdb4b3433b78499e6e619779d430a343deb`
- External result: `C:\Users\1_1\Desktop\goal_loop_v2_1308_op009_op010_gemini_20260902\OP009\result.json`

## OP010

- HTTP: `400`
- API status: `FAILED_PRECONDITION`
- Message: `User location is not supported for the API use.`
- Attempts: `1` (the retry policy does not retry a non-transient HTTP 400)
- Prompt SHA-256: `e07d25a5f83a050cdd18b29bb6804adae5daea4978dae12cc761400e6992521d`
- External result SHA-256: `0494c0efcd366361ed96921a43b518f55bd87bdc34ad8f9a88d7cc50163f3b58`
- External result: `C:\Users\1_1\Desktop\goal_loop_v2_1308_op009_op010_gemini_20260902\OP010\result.json`

## Gate decision

Both results are unusable as advisory observations. They contain no parsed visual verdict, do not remove the Gemini-review blocker, and do not affect semantics, adjacency, score, or build authorization. The strict reviewer did not expose the configured API key in output artifacts.

`usable_advisory=false`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`
