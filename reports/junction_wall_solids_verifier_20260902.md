# Independent junction wall-solid candidate verification

Verdict: ACCEPT commit `2468b5b` strictly as a fail-closed candidate.

- Candidate hash: `736d1c02909966bab747a24d77b9157135d539d9858cd0a76d3c891bc9d7ff79`
- Coverage: 35 atom solids, 49 junction solids, 70 endpoint records.
- Exact topology: 14 raw faces, 13 retained faces, one discarded
  `0.000957955 m²` sliver, 11 single-anchor faces, one multi-anchor face, one
  unlabeled face; all 16 anchors remain in one face each.
- `-1 mm` face-abutment perturbation preserves the exact counts.
- `+1 mm` perturbation changes 14 raw/13 retained to 15 raw/14 retained and
  adds one single-anchor face; topology confirmation therefore remains false.
- Forged-and-rehashed geometry and provenance mutations were rejected.
- Focused tests: 5 passed.

All confirmation, promotion, build authorization, and readiness flags remain
false. No opening cuts are included.
