# Independent verifier — reproducible Layer 1

Disposition: **accept as a reproducible source-wall research artifact; reject functional/construction BIM claims**.

The verifier ran `tests/test_blender_research_wall_layer.py`, including a Blender 5.2 factory-startup build and a second factory-startup GLB import. It also cold-opened the canonical Blend, recomputed every artifact-manifest file hash and size, and checked the generator's scene-cleanup scope.

Canonical manifest facts:

- source structure: `700bb25a37a6b944bb792c1837ee2c47fcfa0437e315cbcc333fb880057299c1`
- source file: `db4fa7a656b8a0267494d4e299a436503d219ba57f06f168dd5150427f299eb1`
- Blender: `5.2.0 LTS`
- wall objects: `35`
- source Blend topology: `280 vertices / 210 faces`
- opening cuts: `0`
- missing/extra/duplicate wall IDs: `0 / 0 / 0`
- geometry errors: `[]`
- focused builder tests: `5 passed`

The canonical GLB imported as 35 `GEO-WALL-*` meshes plus one metadata empty, with no camera, light, action, or animation. glTF triangulation produced 24 vertices / 12 triangles per wall, and research/source metadata extras survived the roundtrip.

Current canonical artifact hashes are authoritative in `artifact_manifest.json`; the verifier independently recomputed and matched each entry. The generator removes only objects in the named research collection carrying the same branch ID, and merely hides the factory-startup Cube/Camera/Light. It does not delete unrelated scene content.

`research_only=true`, `not_for_construction=true`, `formal_build_authorized=false`, and `score_effect=none` remain mandatory. No opening, room adjacency, or IFC semantic is established by this milestone.
