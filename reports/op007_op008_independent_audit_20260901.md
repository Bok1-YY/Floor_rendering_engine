# Independent OP007 / OP008 duplicate audit

Status: evidence-only; no semantic promotion or build authorization.

## Inputs

- `reference-coordinate-authorized-v21.json` (source structure hash `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`)
- `opening-wall-space-evidence-candidate-v1.json`
- `wall-2d-geometry-fact-candidate-v1.json`
- `reports/opening_geometry_audit_20260901.md`

## Deterministic geometry

| opening | source segment (m) | axis | exact host atom | segment distance |
|---|---|---|---|---:|
| OP007 | (1.350251, 4.028804) → (1.350251, 3.272494) | vertical | ATOM-WB019-01 | 0.000000 m |
| OP008 | (1.134358, 4.254242) → (2.085754, 4.254242) | horizontal | ATOM-WB018-01 | 0.000000 m |

The segments have different orientations, different coordinates, and different exact host branches. They cannot be the same geometric opening under the current coordinate contract. OP007 also has a weaker nearby candidate WB021 (0.059205 m segment distance); OP008 has weaker nearby candidates WB017 (0.095140 m) and WB019 (0.217844 m), but these do not replace the exact hosts.

## Independent decision

`OP007` and `OP008` are **distinct geometric opening candidates**, not duplicates. The evidence does **not** establish either opening's type, hinge/swing, jamb, height, effective void, or the two spaces connected. It therefore does not authorize semantic promotion or Blender/IFC construction.

## Required follow-up

Capture separate pixel crops and overlays for each opening, then verify wall-face/jamb ownership and assign both sides to bounded spaces. Keep both records unresolved until those checks agree with an independent Gemini composite review.

`semantic_promotion=false`  
`build_authorized=false`  
`ready=false`
