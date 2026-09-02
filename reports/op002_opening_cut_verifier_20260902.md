# Independent OP002 opening-cut candidate verification

Verdict: ACCEPT commit `ac42586` strictly as a fail-closed candidate.

- Candidate hash: `08003ac0004da32a3606c300136c2d35dc01995ee6f1e5f39f4172e90590e98f`.
- Focused tests: 2 passed; worktree remained clean.
- Exact cut retains 13 candidate faces and leaves the two side probes in
  separate faces (`[6]` and `[10]`).
- Endpoint ±4 px probes also retain 13 faces and separate sides.
- Thickness −1 mm yields 14 faces with separate sides.
- Thickness +1 mm yields 11 faces and merges the side probes into one face.
- Promotion, wrong opening ID, and forged-and-rehashed geometry mutations were
  rejected.

The candidate implementation is accepted; cut geometry, traversability,
side-space relation, adjacency, build authorization, and readiness remain
unconfirmed.
