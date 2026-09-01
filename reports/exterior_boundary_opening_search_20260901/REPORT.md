# Exterior boundary opening search (2026-09-01)

This deterministic, source-only audit tests every active OP opening against the
confirmed `outer_boundary.polygon_m` in the coordinate-authorized 1308 source
document. It uses inclusive segment intersection in metric coordinates; no
semantic labels or source fields are changed.

## Result

No OP opening intersects the confirmed outer-boundary polygon. Consequently no
external entrance/root is promoted by this audit.

Two near-boundary candidates are retained as leads only: OP003 (minimum
endpoint-to-boundary distance 0.014637 m) and OP010 (0.582505 m). Neither has
an exact segment intersection, and proximity alone is not an exterior door
proof.

## Provenance

- Source: `data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json`
- Outer-boundary status: `confirmed`
- Source structure hash: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- Method: exact inclusive segment intersection; endpoint distance is reported
  only as a diagnostic.
- Source mutation: none
- Semantic promotion: false
- Build authorization: false

See `exterior-boundary-opening-search-v1.json` for machine-readable output.
