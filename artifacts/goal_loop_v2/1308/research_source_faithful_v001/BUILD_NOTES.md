# 1308 source wall layer v001

This is an isolated research gray-model branch. It reproduces the 35 validated v21 wall atoms using identity source-XY to Blender-XY coordinates, 0.12 m source wall thicknesses, and the reversible 2.8 m research wall-height assumption.

No wall is cut for OP001–OP012 in this first Blender layer. The unit-connectivity source ambiguity remains active: `bedroom_02`, `north_toilet`, and `dry_balcony` are not represented as confirmed reachable spaces. The scene must be labeled research-only and not for construction.

Required outputs before this layer can be accepted as a research artifact:

- versioned `.blend` source;
- top orthographic render;
- northeast axonometric render;
- northwest axonometric render;
- exact wall-object/endpoint/thickness/height validation JSON;
- independent visual and structural review.

This branch does not change the source score, source document, opening semantics, adjacency graph, or formal Blender/IFC build authorization.

## Rebuild from an empty Blender

Run from the repository root:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --factory-startup --background `
  --python tools\goal_loop_v2\blender_research_wall_layer.py -- `
  --source data\goal_loop_v2\references\1308\reference-coordinate-authorized-v21.json `
  --out artifacts\goal_loop_v2\1308\research_source_faithful_v001
```

The builder verifies the source file/structure hashes before mutation, hides rather than deletes factory-startup objects, removes only objects tagged with the same research branch on idempotent reruns, and regenerates the checkpoint, final Blend, GLB, three renders, structural validation, and artifact manifest.
