# FastLoop research-model kernel

`run_research_model(bundle, output_root, blender_executable=None, ifc_python=None)`
is the product-facing entry point. It validates a source-bound
`research-structure-bundle-v1`, creates one non-overwriting version directory,
runs Blender 5.x with argument arrays and `shell=False`, cold-opens the `.blend`,
cold-imports the `.glb`, writes/reopens IFC4, and returns artifact hashes.

The exact top-level contract is:

```text
schema, source, project, source_hash, structure_hash,
outer_boundary_m, spaces,
wall_branch_graph={version,walls},
opening_contract={version,junction_clearance_m,openings},
adjacency_truth={version,edges,confirmed},
assumptions={scale_m_per_unit,floor_slab_thickness_m,research_only},
unresolved_issues
```

Use `compute_structure_hash()` after assembling every other field. Geometry is
metres/Z-up. All active records must be confirmed. Openings are cut by a
deterministic wall-axis/height occupancy grid; the builder never applies a
whole-home Boolean.

Statuses are deliberately limited to `mechanical_verified`,
`blocked_dependency_missing`, and `failed_product`. An unavailable
IfcOpenShell process leaves verified Blender artifacts intact and returns the
blocked status rather than relabeling them as a product failure.
