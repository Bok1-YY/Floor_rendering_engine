# Independent opening side-space candidate audit

Space anchors can rank low-trust candidates on opposite opening half-planes,
but cannot prove contact without room polygons or wall left/right ownership.
Line-of-sight wall crossings and a 0.12 m jamb threshold were applied.

- OP002: `bedroom_corridor` is a strong single-side candidate; west side is unresolved.
- OP003: `{bedroom_01, west_toilet}` candidate pair; one jamb remainder is 0.0 m.
- OP004: `{north_toilet, bedroom_02}` is the least ambiguous geometric pair.
- OP007: `{wc, kitchen}` candidate pair; minimum jamb remainder is 0.065 m.
- OP008: `{bath, kitchen}` candidate pair; minimum jamb remainder is 0.035 m.

All remain candidate-only. Final confirmation requires room polygons, confirmed
wall-side ownership, or local half-edge/DCEL topology plus independent review.
