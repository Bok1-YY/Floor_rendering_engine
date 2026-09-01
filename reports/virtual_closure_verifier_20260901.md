# Independent virtual-closure audit

Baseline square-cap wall solids produce 14 candidate faces. OP001/002/003/004/
006/007/008/009/010/011 and the demoted portal are already fully covered by
the inferred wall solids, so adding explicit closure buffers changes no face.
OP005 is only 6.49% covered and splits 14→15 faces, but it creates an unlabeled
pocket without changing source-anchor groups; OP005 is already rejected
evidence and must remain excluded.

Verdict: authoritative virtual-closure allowlist is empty at the current stage.
Build per-junction wall solids first; apply closures only after an opening cut,
host, terminals, side spaces, registration, and independent review are all
authorized. OP005 and the demoted portal remain permanently denied; OP011
remains unresolved.
