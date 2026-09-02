# OP002 physical-cut plus room-topology closure analysis

Physical wall state uses the independently accepted OP002 cut with `1 µm`
numerical clearance. It has 11 retained free-space faces; both side probes are
in the same face. Adding a room-topology-only barrier along the registered
OP002 segment, buffered by a `1 µm` half-width, yields:

- 12 retained faces;
- 11 single-anchor faces;
- one multi-anchor face;
- zero unlabeled faces;
- side probes in different faces;
- `bedroom_01` restored as a single-anchor face;
- the remaining multi-anchor face contains `bath`, `bedroom_corridor`,
  `kitchen`, `living_hall`, and `lobby`.

Closure half-widths from `1 nm` through `50 µm` produce the same topology at
the exact registered endpoints. Extending the segment by `1 µm`, `1 mm`, or
29.3 mm also preserves it. Shortening by `1 mm` or 29.3 mm leaves a gap and
fails to partition the room face; shortening by only `1 µm` remains closed due
to the square-cap half-width.

Verdict: the cut/closure separation is geometrically coherent at the exact
registered OP002 endpoints, but is not robust to materially shortened endpoint
geometry. It remains a candidate and does not confirm the opening, room pair,
adjacency, score, or build authorization.
