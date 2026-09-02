# OP002 cut/closure on the target-aware wall baseline

The independently accepted target-aware wall candidate starts with 14 retained
faces: 12 single-anchor, one multi-anchor, one unlabeled. `bath` is isolated;
the multi-anchor face is `bedroom_corridor`, `kitchen`, `living_hall`, `lobby`.

Applying the OP002 physical cut with the accepted `1 µm` numerical clearance
yields 12 retained faces: 11 single-anchor, one multi-anchor, zero unlabeled.
`bedroom_01` joins the public multi-anchor component; `bath` remains isolated.

Adding the room-topology-only OP002 closure yields 13 retained faces: 12
single-anchor, one multi-anchor, zero unlabeled. `bedroom_01` is restored as a
single-anchor face, while the remaining multi-anchor face is exactly
`bedroom_corridor`, `kitchen`, `living_hall`, `lobby`. `bath` remains a separate
face throughout.

This removes the bath contamination present in the earlier junction-wall
baseline and is the intended input for a new target-aware OP002 cut/closure
candidate. No source, semantic, adjacency, score, or build state is confirmed.
