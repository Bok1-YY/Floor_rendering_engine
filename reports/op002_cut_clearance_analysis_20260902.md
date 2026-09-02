# OP002 cut-clearance topology analysis

The apparent `+1 mm` cut sensitivity is a boundary-contact precision effect,
not a millimetre-scale threshold. Binary search over the wall-normal clearance
found two transitions:

- `0.243026 µm`: the unlabeled pocket merges into the corridor/living main face
  (13→12 retained faces).
- `0.278408 µm`: the `bedroom_01` face merges into that main face (12→11
  retained faces), and the OP002 side probes resolve to the same free-space
  component.

From `0.3 µm` through `50 µm`, all tested endpoint perturbations (`−4 px`,
exact, `+4 px`) remained at 11 retained faces, 10 single-anchor faces, one
multi-anchor face, and zero unlabeled faces. A `1 µm` numerical clearance is
therefore a stable candidate for topology computation, but it is still an
inferred numerical policy—not source-confirmed cut geometry.

The merged multi-anchor component contains `bedroom_01`, `bedroom_corridor`,
`bath`, `kitchen`, `living_hall`, and `lobby`. This is consistent with opening
OP002 connecting `bedroom_01` to the already open main component, while also
absorbing the pre-existing unlabeled pocket. It does not prove the semantic
space pair or authorize adjacency/build.

Independent read-only reproduction accepted the report. It measured the two
boundaries at `0.2430263013573909 µm` and `0.27840782218860394 µm`, and
confirmed identical topology/anchor groups at `0.3 µm`, `1 µm`, and `50 µm`
for exact and ±4 px endpoint perturbations.
