# Canonical boundary-label audit — sample 1308

Status: read-only audit; no source-contract or semantic promotion.

## Inputs

- Canonical image: `data/goal_loop_v2/references/1308/canonical-raw-portrait.png`
- Image SHA-256: `b85ff7446e2d1a123e3a41dc541ea9d6e081f3978d3460d6de8ef9906119bc07`
- Governing document: `reference-coordinate-authorized-v21.json`
- Document SHA-256: `db4fa7a656b8a0267494d4e299a436503d219ba57f06f168dd5150427f299eb1`
- Canonical image size: `2245 × 3043 px`

## Visual observations

The supplied plan visibly contains the following labels and symbols:

| Label/symbol | Approximate location in canonical pixels | What it denotes in the drawing | Exterior-root support |
|---|---:|---|---|
| `GATE` | bottom centre, approximately `(1120, 2920)` | plot/site gate on the outer plot boundary, adjacent to the `20' APPROACH ROAD` annotation | No: it is not a building opening and no segment connects it to an interior space |
| `20' APPROACH ROAD` | below the plot, approximately `(1120, 3000)` | external site/road context | No: context only; not an entrance geometry |
| `ENTRY` | inside the stair/entry zone, approximately `(920, 1650)` | internal room/zone label with a downward arrow | No: it is inside the confirmed footprint and is not itself a boundary intersection |
| `DN` / `UP` | within the stair zone | stair direction annotations | No: circulation annotation, not an opening |
| plot boundary lines | outer rectangle and bottom site edge | site/plot boundary around the apartment footprint | No by itself: boundary is not an opening or root connection |

The `GATE` label is especially important: it refers to the site/plot gate at the bottom of the drawing, not to a door in the apartment wall. The apartment footprint sits above it; the drawing does not provide a door cut, jamb, or traversable segment from the gate to `LOBBY`, `ENTRY`, or another bounded interior space.

## Contract comparison

The authorized document's confirmed outer polygon is:

```text
(0.000, 15.308) → (9.170, 15.308) → (9.170, 0.000)
→ (7.520370, 0.000) → (7.520370, -2.070435)
→ (2.425926, -2.070435) → (2.425926, 0.000) → (0.000, 0.000)
```

This polygon includes the lower balcony projection. The site `GATE` is outside the apartment wall/space topology and has no corresponding active opening ID. The document's active opening contract has no opening with a source segment that is both a confirmed building-boundary intersection and a two-sided interior/exterior connection.

Relevant current opening states:

- `OP001`: labelled `entrance_symbol`, but its current host candidate is an internal wall and it has no confirmed side spaces; it cannot be promoted from the label alone.
- `OP002`: internal door candidate (`bedroom_01` ↔ `bedroom_corridor`); not exterior.
- `OP009`/`OP010`: glazed-access candidates at balcony edges; not site entry evidence.
- `OP011`: unresolved glazed interface.
- No active opening is source-confirmed as `exterior → bounded interior`.

## Decision

```text
site_gate_detected = true
building_exterior_opening_detected = false
source_supported_exterior_root = false
entrance_opening_id = null
semantic_promotion = false
build_authorized = false
ready = false
```

The labels support a site-context fact (`GATE` and approach road exist in the image), but do not support an exterior root for the apartment. Treating `GATE` as an apartment entrance would invent missing geometry and would violate the current source contract.

## Required evidence to change the decision

One of the following is required before S07 can be promoted:

1. A source drawing showing the building entrance/door and its connection to a bounded interior space; or
2. a human annotation on the canonical image identifying the actual building-side entrance point and inside/outside sides, followed by registration, wall ownership, jamb/void, and path verification; or
3. an explicitly approved research-only off-frame policy (which remains ineligible for BIM/IFC authorization).

This report makes no mutation to `reference-coordinate-authorized-v21.json`.
