# Sanitized real-CAD regression contract

This directory defines the only commit-safe representation of a private real CAD regression case. The original DWG, converted DXF, parse report, space draft, screenshots, original coordinates, filenames, addresses, text, layer names, block names, handles, project IDs, and raw SHA-256 values must never be committed.

## Export

The exporter reads a local project JSON, verifies the project’s original DWG, the external parse-report hash, the ACadSharp DXF against `parse_report.source_sha256`, and the space-draft hash. It emits only translated metre-space geometry and anonymous structural metadata.

```powershell
$env:WHOLE_HOME_CAD_FIXTURE_HMAC_KEY = '<private stable secret of at least 16 bytes>'
.\.venv\Scripts\python.exe tools\whole_home_dataset.py export-cad-regression `
  --project-json data\output_files\_whole_home\projects\<private-project>.json `
  --output data\external_datasets\whole_home_geometry\private_cad_exports\cad_real_001.json `
  --fixture-id cad_real_001
.\.venv\Scripts\python.exe tools\whole_home_dataset.py validate-cad-regression `
  --fixture data\external_datasets\whole_home_geometry\private_cad_exports\cad_real_001.json
```

The secret is never serialized. The public `source_commitments` are HMAC-SHA256 values over the private original-DWG SHA-256 and converted-DXF SHA-256. This lets an authorized maintainer prove which private source produced a fixture without publishing source hashes that could be matched against a leaked CAD file. Use a stable secret from private secret management before committing an independently reviewed fixture; do not type it into source control or shell history.

## `normalized_entities`

- `walls`: anonymous segment geometry, dimensions, coarse source-kind class, and INSERT nesting depth.
- `wall_assemblies`: anonymous footprint candidates and review state.
- `openings`: anonymous wall references and opening dimensions.
- `face_candidates`: anonymous local polygons and eligibility flags.

All coordinates are translated by the minimum selected-geometry X/Z corner, rounded to six decimals, and kept in metres. Original absolute CAD coordinates and all provenance names/handles are discarded. Every entity has a canonical `entity_hash`; the complete package has `fixture_hash`.

## `ground_truth`

An automatic space draft is not ground truth. Export always starts with `status: annotation_required` and explicitly lists four missing tasks:

1. classify every normalized wall as accepted, rejected, or corrected;
2. annotate every real opening independently;
3. assign or exclude every eligible face exactly once;
4. compare the annotation against the private source in an independent dual-view review.

Only after those tasks are complete may the status become `reviewed`. The validator then requires complete wall decisions, complete eligible-face coverage without overlap, all four review flags, no missing tasks, valid entity references, matching entity/package hashes, ASCII-only tokens, locally bounded coordinates, and zero privacy violations.

`contract_example.json` is deliberately synthetic and exists only to lock the format. It must not be represented as a real building or ground truth. Real exports normally remain in the gitignored data directory; an explicitly approved anonymous candidate may be committed with `production_gold_eligible: false` while annotation and final manual privacy review remain pending.

`cad_real_001.json` is the first commit-safe candidate exported from the current private real drawing. It contains 743 anonymous wall segments, 451 anonymous wall assemblies, 27 face candidates, and zero claimed openings. It intentionally preserves all 236 unresolved wall assemblies and remains `annotation_required`; consequently it is a regression input candidate, not production Gold. Its manifest records `manual_privacy_review: pending` and `production_gold_eligible: false` until an independent reviewer completes both geometry annotation and final privacy review.
