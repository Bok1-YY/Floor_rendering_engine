# S06 opening-semantic gap inventory

Generated deterministically from the current coordinate-authorized source contract and the opening-wall-space candidate. This report is read-only inventory evidence; it does not mutate source truth or authorize construction.

## Provenance

- Source: `data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json`
- Source SHA-256: `DB4FA7A656B8A0267494D4E299A436503D219BA57F06F168DD5150427F299EB1`
- Candidate: `data/goal_loop_v2/references/1308/opening-wall-space-evidence-candidate-v1.json`
- Candidate SHA-256: `EF35F0742ABCFFDFB891BD7ED4D6006A4E49E5034E2A5457F97B716E67F23B81`
- Candidate hash: `e3bf7bd78dd489746a055b2dcbb2a23e22c164f3164757660590afdbe13a088e`

## Counts

- Source openings: **12** (11 `OPxxx` records plus one demoted portal record)
- Source spaces: **16**
- Wall-link candidates: **36** (3 per opening)
- Openings with exact/zero-distance best wall candidate: **11**
- Openings with non-zero best wall distance: **1** (`OP001`, distance-only association)
- Openings with both side-space IDs populated: **1** (`OP002`)
- Openings with any side-space ID missing: **11**
- Openings with `semantic_promotion=true`: **0**
- Openings with `build_authorized=true`: **0**
- Openings with `effective_void.status=confirmed`: **1** (`OP001`)
- Openings with `effective_void.status=candidate`: **1** (`OP002`)
- Openings with no usable effective void: **10**
- Source opening status: **3 confirmed / 9 candidate**

## Per-opening gaps

| Opening | Source kind/status | Best wall candidate | Side A / Side B | Effective void | S06 blocker |
|---|---|---|---|---|---|
| OP001 | entrance_symbol / confirmed | ATOM-WB016-02 (distance-only) | missing / missing | confirmed (but no space sides) | no boundary intersection; no two-sided space proof; no traversability |
| OP002 | door / candidate | ATOM-WB006-02 (0.000 m) | bedroom_01 / bedroom_corridor | candidate | type/jamb/space/path not independently confirmed |
| OP003 | door / candidate | ATOM-WB010-01 (0.000 m) | missing / missing | none | no type, void, jamb, or side spaces |
| OP004 | door / candidate | ATOM-WB007-01 (0.000 m) | missing / missing | none | no type, void, jamb, or side spaces |
| OP005 | unknown / candidate | ATOM-WB009-01 (0.000 m) | missing / missing | none | unknown semantic and no side spaces |
| OP006 | unknown / candidate | ATOM-WB007-02 (0.000 m) | missing / missing | none | unknown semantic and no side spaces |
| OP007 | door / candidate | ATOM-WB019-01 (0.000 m) | missing / missing | none | no type, void, jamb, or side spaces |
| OP008 | door / candidate | ATOM-WB018-01 (0.000 m) | missing / missing | none | no type, void, jamb, or side spaces |
| OP009 | glazed_access_door / candidate | ATOM-WB005-01 (0.000 m) | missing / missing | none | no two-sided space or balcony access proof |
| OP010 | glazed_access_door / candidate | ATOM-WB003-03 (0.000 m) | missing / missing | none | no two-sided space or balcony access proof |
| OP011 | glazed_interface / confirmed | ATOM-WB022-01 (0.000 m) | missing / missing | none | unresolved semantic; no void/jamb/space proof |
| PORTAL-WB011-WB006-01 | unknown / candidate | ATOM-WB011-02 (0.000 m) | missing / missing | none | demoted duplicate/continuous-wall interpretation; excluded from promotion |

## Required S06 evidence before promotion

Each proposed opening must independently bind: source-registered endpoints, owning wall atom, effective void, jamb/support, opening type, both bounded spaces, and height/sill/head facts. Any door claim additionally requires evidence of a real cut rather than a nearby line or furniture/annotation. Gemini output is advisory and must be complete, parseable, and consistent with geometry; it cannot replace these facts.

## Disposition

```text
S06 = FAIL
semantic_promotion = false
build_authorized = false
ready = false
```

This inventory is intentionally fail-closed. It records deficiencies rather than filling them with assumptions.
