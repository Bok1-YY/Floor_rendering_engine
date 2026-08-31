# Goal-Loop v2

Goal-Loop v2 turns the two complex floor-plan fixtures (`1308` and `121m2`)
into independently verified Blender/GLB/research-IFC structural models. The
loop never repairs final meshes directly: every correction changes the
source-bound structure contract, changes its hash, and rebuilds from scratch.

## Resume

From the repository root, with any Python 3.10+ interpreter:

```powershell
python tools/goal_loop_v2/status.py --json
python tools/goal_loop_v2/resume.py --dry-run
```

`docs/goal_loop_v2/CURRENT.json` is the only cross-session execution pointer.
Run only its `next_action`; do not infer progress from chat history. A real
resume without `--dry-run` changes `paused` to `running` atomically but does not
start Blender or call a paid API.

## Non-negotiable gates

- Source JPEG bytes, decoded raw-pixel hash, EXIF value, canonical visible
  orientation and normalized PNG hash are all retained.
- Floor-plan normalization does not silently rotate stale EXIF metadata.
- Human opening anchors bind one-to-one to existing openings with matching
  types and project/source provenance.
- Unsplit T/X wall intersections, collinear overlaps, near-miss gaps, and
  opening geometry outside deterministic tolerances fail before Blender.
- Automatic repair allows only the operations in the goal contract, writes
  loop state only, and stops after two attempts.
- Daily development and push do not run Nuitka packaging.

Runtime evidence belongs under ignored `data/goal_loop_v2/`; tracked files
contain only contracts, state pointers, tests and checkpoint summaries.
