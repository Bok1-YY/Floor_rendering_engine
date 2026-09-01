# Deterministic room-face polygonization research — 1308

## Scope and authority boundary

This is a read-only research prototype over the current authorized 2D wall
geometry sidecar. It does not mutate the source contract, score, semantic
status, adjacency, Blender state, or IFC build authorization.

Frozen input:

- source: `reference-coordinate-authorized-v21.json`
- file SHA-256: `db4fa7a656b8a0267494d4e299a436503d219ba57f06f168dd5150427f299eb1`
- structure hash: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- 21 wall branches / 35 wall atoms / 49 junctions / 16 space anchors
- confirmed outer-boundary polygon
- Shapely `2.1.2`; NetworkX `3.6.1`; Python `3.14.5`

## Route A — polygonize wall centerlines

The raw centerline graph cannot produce room faces.

At exact precision the noded graph has:

- 55 vertices
- 41 edges
- 14 connected components
- cycle rank `E - V + C = 0`
- 42 degree-one vertices
- 0 polygons, 41 dangles, 0 cuts, 0 invalid rings

After a 1 µm precision grid it has 49 vertices, 35 edges, 14 components,
cycle rank 0, and 0 polygons / 35 dangles. Every tested precision grid from
1 µm through 100 mm still yields zero polygons. This is a forest, not a closed
planar subdivision. Face-abutment wall endpoints intentionally stop at wall
faces rather than meeting other centerlines, so centerline polygonization is
not a valid room-boundary algorithm for this contract.

Coarser grids at 150 mm and above can occasionally create one polygon, but the
result is non-monotonic and fabricated by snapping. It is rejected.

## Route B — confirmed outer boundary minus buffered wall solids

This route yields candidate free-space components, but not confirmed rooms.

### Nominal thickness, flat caps

- 13 candidate faces
- 12 faces contain one or more space anchors
- 1 empty sliver (`~0.000958 m²`) at the north-east outer-wall corner
- one `~60.711 m²` component contains five anchors:
  `bedroom_corridor`, `lobby`, `kitchen`, `living_hall`, and `bath`

Flat caps follow the literal atom segment extents. Exact face contacts do not
always seal the free-space complement; `bath` leaks into the main component.

### Nominal thickness, square caps

- 14 candidate faces
- 13 faces contain one or more space anchors
- 1 empty `~1.475542 m²` pocket between WB010/WB011/WB012/WB013/WB006
- one `~58.138 m²` component contains four anchors:
  `bedroom_corridor`, `lobby`, `kitchen`, and `living_hall`
- `bath` becomes a separate `~2.539696 m²` face

Square caps close face-abutment joints by extending every atom by half its
thickness. That produces a stable candidate topology across the tested
thickness range, but the extension is inferred geometry and is not authorized
by the current 2D fact.

## Sensitivity evidence

With flat caps, candidate face count changes sharply under thickness scaling:

- 0.75×: 8 faces
- 0.90×: 8 faces
- 0.95×: 10 faces
- 0.99×: 13 faces
- 1.00×: 13 faces
- 1.01× through 1.25×: 15 faces

With square caps, the count remains 14 from 0.75× through 1.25×, but this
stability comes from unapproved endpoint extension. The two cap policies
therefore encode different topology, not just different rendering.

## Failure modes

1. **No centerline cycles.** The 35-atom graph has cycle rank zero, so a DCEL
   over current centerlines has no bounded faces to enumerate.
2. **Face-abutment semantics.** Atom endpoints meet wall faces, not adjacent
   centerlines. Generic polygonize treats the graph as disconnected dangles.
3. **Cap-policy dependence.** Flat versus square caps changes bath separation
   and total face count.
4. **Thickness sensitivity.** A 1% change around nominal thickness changes the
   flat-cap topology from 13 to 15 faces.
5. **Semantic zones are not topological rooms.** Four space anchors share the
   same large open component even under the most stable square-cap variant.
6. **Unlabelled geometric pocket.** The square-cap result contains a sizeable
   face with no space anchor; it cannot be named or discarded automatically.
7. **Openings and virtual partitions.** Cutting doors connects spaces, while
   leaving wall atoms continuous can create narrow pockets. Room boundaries
   require an explicit virtual-closure policy at openings.
8. **No room-boundary provenance.** Space anchor points do not certify the
   walls that bound a space and cannot promote candidate faces.

## Candidate algorithm for the next layer

The deterministic next step should not be raw polygonize. It should build
source-auditable wall solids and half-edge topology:

1. validate every atom, thickness, endpoint, and junction policy;
2. resolve each endpoint according to its explicit face-abutment/junction
   policy, without global snapping;
3. build wall-solid polygons with a per-junction provenance record;
4. subtract their union from the confirmed outer boundary;
5. add explicit, provenance-bound virtual closures for door/opening locations;
6. enumerate bounded free-space components;
7. test space anchors with point-in-polygon;
8. fail closed on zero-anchor, multi-anchor, cap-sensitive, or topology-drift
   faces;
9. require independent image/Gemini/human verification before any room polygon
   is promoted.

## Disposition

Current deterministic room-face status: `candidate_only / unresolved`.

- centerline/DCEL face count: **0**
- wall-solid complement candidates: **13 flat-cap / 14 square-cap**
- confirmed room polygons: **0**
- score effect: **none**
- semantic promotion: **false**
- adjacency/traversability: **false**
- Blender/IFC authorization: **false**

Reproduce with:

```powershell
python reports/room_polygonization_research_20260901/prototype_room_faces.py > $env:TEMP/room-face-research.json
```
