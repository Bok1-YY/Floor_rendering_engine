# OP002 registration source-truth review (independent)

## Finding

The registration conflict is not caused by the canonical image's EXIF. The
authoritative canonical PNG is 2245 x 3043, has no EXIF orientation, and its
decoded RGB pixels are byte-for-byte equal to the decoded RGB pixels of the
original JPG. The original JPG carries EXIF orientation `8`; applying that
orientation produces a 3043 x 2245 landscape image whose pixels do **not**
match the canonical PNG. The source pipeline intentionally keeps the raw
portrait pixel frame and ignores the invalid EXIF, so no rotation or axis swap
is justified.

The published affine controls are internally consistent: `(497,2382)` maps to
`(0,0)`, `(1750,2382)` maps to `(9.17,0)`, and `(497,277)` maps to
`(0,15.308)`. Therefore the transform is not the source of the OP002 axis
conflict.

## OP002 reconciliation

The horizontal pixel segment `(965,960)->(1098,960)` is stale/wrong-frame
evidence for OP002. Under the valid transform it maps to approximately
`(3.423,10.339)->(4.397,10.339)`, which is not collinear with the declared
vertical host `ATOM-WB006-02` at `x=4.423994`.

The current metric segment maps back to the canonical pixel frame as:

```text
(4.423994,10.690147) -> (4.423994,9.802938)
approximately (1101,912) -> (1101,1034)
```

This is consistent with the vertical host wall and with the earlier source
arbiter's corrected OP002 segment. This is a candidate correction only; it is
not a semantic promotion and must still be independently re-reviewed before
being accepted.

## Verdict

`exif_not_cause`; `transform_controls_consistent`;
`stale_horizontal_op002_evidence_rejected`; `vertical_pixel_candidate_pending`.

No source document was modified. `semantic_promotion=false`,
`build_authorized=false`, `ready=false`.
