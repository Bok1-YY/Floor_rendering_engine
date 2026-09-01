# OP002 registration reconciliation (independent)

Advisory-only reconciliation; no source promotion or Blender/IFC authorization.

## Inputs and recalculation

Canonical source SHA: `b85ff7446e2d1a123e3a41dc541ea9d6e081f3978d3460d6de8ef9906119bc07`.
The published affine transform is x=`0.007318435754189944*px-3.637262569832402`,
y=`-0.007272209026128266*py+17.32240190023753`.

OP002 pixel evidence `(965,960) -> (1098,960)` maps to approximately
`(3.423,10.339) -> (4.397,10.339)`, remaining horizontal and approximately
0.974 m long. It cannot map to the stored metric OP002 segment
`(4.423994,10.690147) -> (4.423994,9.802938)`, which is vertical.

The claimed host `ATOM-WB006-02` metric centerline is
`(4.423994,12.690005) -> (4.423994,5.688690)`. Inverse transformation gives
approximately pixel `(1101,637) -> (1101,1600)`, a vertical line. Therefore
the pixel OP002 segment at y=960 crosses/approaches this line; it is not
collinear with it. `exact_collinear_overlap` is unsupported by the published
transform.

## Verdict

The metric OP002 payload is stale or was derived under another registration
frame, or the claimed host/evidence frame is stale. This is not rounding or
endpoint ordering. Keep OP002 unresolved. Do not modify the authorized source,
promote adjacency, or build geometry. A fresh single-frame registration with
explicit intersection/collinearity measurements is required.

`registration_conflict_confirmed`; `semantic_promotion=false`;
`build_authorized=false`; `ready=false`.
