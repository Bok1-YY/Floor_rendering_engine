# Whole-home progressive geometry benchmark catalog

The committed catalog is intentionally small. Raw IFC, CAD, images, annotations, and derived truth remain under the repository's already-ignored `data/` tree.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe tools\whole_home_dataset.py audit
.\.venv\Scripts\python.exe tools\whole_home_dataset.py list --levels L1
.\.venv\Scripts\python.exe tools\whole_home_dataset.py download --levels L1
.\.venv\Scripts\python.exe tools\whole_home_dataset.py verify-checksums --levels L1 --require-installed
.\.venv\Scripts\python.exe tools\whole_home_dataset.py prepare --levels L1
.\.venv\Scripts\python.exe tools\whole_home_dataset.py inspect --levels L1
.\.venv\Scripts\python.exe tools\whole_home_geometry_gold.py all
```

`download` only follows pinned HTTPS URLs on the code-level official allowlist. It uses `.part` files, bounded retries, exact byte-size checks, and SHA-256 before atomic replacement. Agreement-required, non-commercial, unknown-data-license, and link-only sources are reported as skipped; the command never submits a form, accepts terms, uses a token, or looks for an unofficial mirror.

Difficulty is calculated from `difficulty_rules.json`. The first real L1 candidate is FZK House: its pinned IFC contains 7 `IfcSpace` entities, 13 walls, 5 doors, 11 windows, and a stair, so it is not a four-wall toy fixture. Higher levels add multi-storey topology, dense CAD expression, raster degradation, non-orthogonal geometry, and larger projects.

`prepare` creates a checksum-bound IFC entity inventory. `whole_home_geometry_gold.py all` uses the dev-pinned IfcOpenShell 0.8.5 extractor to derive, from the exact same FZK storey, a double-line DXF, ordinary dimensioned PNG, independent IFC triangle GeometryManifest, OBJ gray model, gray preview, and two gold reports. The derived files remain under the ignored `data/` tree and every artifact is SHA-256 bound in `case_manifest.json`.

The CAD gold path runs the real production DXF parser before comparing its model against the independent IFC footprints, spaces and opening relations. The raster gold path runs the real reversible registration, two independent scale anchors, weak wall-ink measurement, room mask comparison and door/window observation. A second derivation must reproduce identical artifact hashes; writer timestamps are canonicalized and covered by a regression test.
