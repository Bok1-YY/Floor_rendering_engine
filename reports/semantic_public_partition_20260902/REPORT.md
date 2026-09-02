# Repaired constrained semantic public partition — 2026-09-02

- Candidate hash: `cc2f512bc8db5c6516548e5a747251789290071a4277888d553dfe3c8369b5aa`.
- File SHA-256: `ea324b82e533c2346883d440ec0bec71b7e2ea8509853c73b6a3fa15547ce6a3`.
- Maximum pairwise overlap: `0 m²`.
- Union symmetric difference from the public multi-anchor face: `0 m²`.
- All four source anchors are covered.
- Lobby is preserved as a two-component clipped Voronoi cell rather than serializing empty.

Stable public-side candidates at host-half-thickness plus 1 mm, 1 cm, 5 cm and 10 cm:

- OP002 → `bedroom_corridor`
- OP006 → `bedroom_corridor`
- OP007 → `lobby`
- OP008 → `lobby`
- OP010 → `kitchen`

OP003, OP004 and OP009 are excluded because their own cut impacts do not merge the public multi-anchor group. The non-public side of each retained opening intentionally has no public-cell hit.

These are constrained semantic Voronoi candidates inside an already connected free-space face. They do not create wall geometry or confirm room polygons, opening pairs, adjacency, score, or build readiness.

`research_only=true`  
`room_polygon_confirmation=false`  
`pair_confirmation=false`  
`adjacency_confirmation=false`  
`score_effect=none`  
`build_authorized=false`
