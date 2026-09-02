# OP004 compact Gemini review attempt

A single `gemini-3.6-flash` POST was sent through the configured
`http://127.0.0.1:7897` proxy using the registered OP004 full/crop overlays.
The service returned HTTP 400 `FAILED_PRECONDITION`: user location not
supported. No model result or token usage was produced; no retry was made.

Image SHA-256 values:

- full overlay: `46be459f80d66f563577fc0a917659d95c46c479c568c605f19ca2e5f35a8e53`
- crop overlay: `f4898f970091c7fa6e804ce3056fa5af33067f60369614dedfffd64d9907c46d`

OP004 remains candidate-only; no semantic, room-pair, traversability, score,
or build state changed.
