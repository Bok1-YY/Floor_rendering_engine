# Independent OP002 topology-tolerance policy verification

Verdict: ACCEPT commit `9d5ebf9` strictly as a source-neutral numerical
policy candidate.

- Tests: 2 passed.
- Candidate hash: `e876c3a3f6b79395e4db9d344da690f49bb220379567b0779eb2552484435a16`.
- Selected clearance: `1e-6 m`; validated range: `[3e-7, 5e-5] m`.
- Stable topology: 11 faces, 10 single-anchor, one multi-anchor, zero unlabeled;
  OP002 side probes share one face.
- Opening-cut candidate hash matches
  `08003ac0004da32a3606c300136c2d35dc01995ee6f1e5f39f4172e90590e98f`.
- Forged selected clearance and forged stable topology were rejected.

The policy has no source-dimension, score, semantic, adjacency, or build
effect.
