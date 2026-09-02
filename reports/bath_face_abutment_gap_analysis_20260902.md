# Bath face-abutment gap analysis

A single-endpoint scan over all 40 `face_abutment_candidate` records found one
and only one topology-changing endpoint: `ATOM-WB018-01:end` at
`NODE-WB018-END`. Extending that endpoint closes the bath boundary and changes
the per-junction wall-solid topology from 13 to 14 retained faces, increasing
single-anchor faces from 11 to 12. The other 39 endpoint probes are topology
no-ops.

The intended receiving wall is `ATOM-WB017-02`. The endpoint-to-receiving-wall
face gap is `0.14318435731 µm`; binary search finds the topology transition at
`0.13520000208 µm`. Using the measured face gap, or any tested extension from
`0.2 µm` through `10 µm`, restores the bath as a single-anchor face while
preserving the other anchor groups.

This is an inferred face-abutment target/extension candidate, not confirmed
source geometry. It shows that the missing bath face is caused by sub-micrometre
rounding at one wall-face contact, not by a missing wall or door.
