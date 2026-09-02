# fal neutral-label room-pair trial — 2026-09-02

- Bundle candidate: `d5ce610645e5ca81d6d93cb547107b4b45c9a65f5813aabd5cec81f7966718b4`.
- Bundle file: `b744c5062383883bbca66fdaa431a4f0fad9169285eb045d9cb76ec35d64df18`.
- fal-reported cost: `$0.0034065`.
- Usable strict responses: 10/11; OP002 returned fenced Markdown and failed the strict parser.
- Both sides matched the existing top proximity rank: 7.
- Only one side matched the top proximity rank: 3.
- Method disposition: `insufficient_for_pair_confirmation`.

Notable low-value outputs include OP001 `lift_shaft/lobby`, OP003 `rear_balcony/west_toilet`, and OP007 `lobby/wc`. These may be valid label selections from the rendered candidates but are not supported strongly enough by current bounded-room geometry to become adjacency facts. OP002 is unusable because it violated the bare-JSON contract.

This trial demonstrates that neutral candidate markers plus a VLM do not replace room polygons or wall-face ownership. The method is retained as advisory research evidence and is not repeated without a geometry-method change.

`pair_confirmation=false`  
`adjacency_confirmation=false`  
`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`
