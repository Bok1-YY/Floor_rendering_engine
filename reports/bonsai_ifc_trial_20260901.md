# Bonsai/Blender IFC backend trial

An isolated trial downloaded Blender 4.2.8 and Bonsai 0.8.5-post1. Extracting
the bundled wheels into an isolated `site-packages` directory resolved the
IfcOpenShell and `shapely.lib` import failures. Blender then enabled the Bonsai
add-on through `addon_utils.enable`; BIM operators were registered and
`bonsai.last_error` was empty. A minimal IFC4 file containing one IfcWall was
written from Blender's Python runtime and read back successfully.

This validates the backend/plugin loading path only. It does not validate
floorplan interpretation, wall ownership, opening semantics, adjacency, or
source provenance, so Bonsai remains downstream-only and cannot bypass
S06/S07/S08.

A second isolated smoke test created and independently reopened an IFC4 model
with one project/site/building/storey, wall, space, opening, and door. Read-back
confirmed four aggregate relations, one spatial containment relation, one
wall-opening void relation, one opening-door fill relation, unique GlobalIds,
and geometry reconstruction for all four products. Both fresh Blender
processes exited 0. Artifact SHA-256:
`0F49E2EC469248BD335E4B60DF67B27963A0829E6E61125B0ED9878E508429BF`.
This used IfcOpenShell API inside the verified Blender/Bonsai dependency
environment; it did not exercise Bonsai UI operators.
