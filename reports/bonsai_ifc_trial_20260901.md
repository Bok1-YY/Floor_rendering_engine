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
