# Goal-Loop v2 checkpoints

Create one short JSON checkpoint only when a stage passes, the user pauses, or
an external blocker prevents progress. A checkpoint records evidence paths,
scores, budget and one executable next action. It is not a signing, trust or
release-approval mechanism; `CURRENT.json` remains the resume pointer.
