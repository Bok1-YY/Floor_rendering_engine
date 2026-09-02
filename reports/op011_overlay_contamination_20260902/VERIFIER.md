# Independent verifier — OP011 overlay contamination

Disposition: **accept the mechanical contamination finding; reject any semantic promotion**.

The verifier reopened the four strict structured-output results, recomputed their raw-response hashes, checked each result against the expected contaminated or clean image bindings, reran the focused tests, and reproduced the bundle from a distinct temporary working directory.

- Bundle candidate: `25e47b07ec32be573df3a133f130bab1be0d49649f48c3a592410bbf65bc4e36`
- Bundle file SHA-256: `25fe8444db372de153b3fcf74bda859f48fc7c1b80466c6034ca9ad4d7bd82a5`
- Direct CLI: exit `0`
- Focused verifier tests: `2 passed`

Both models changed `sliding_track_visible` from `yes` on the colored overlay inputs to `no` on the byte-exact raw crop. Their clean-image conclusions then disagreed: Gemini reported a visible traversable wall gap, while OpenAI reported no visible wall break and an unknown subtype. Therefore the old sliding-door consensus was contaminated and the clean source remains ambiguous.

Subtype, traversability, pair, adjacency, semantic promotion, score effect, and build authorization remain false or `none`.
