# Bonsai/Blender IFC backend trial

An isolated trial downloaded Blender 4.2.8 and a Bonsai add-on archive. A
minimal IFC4 file was generated with IfcOpenShell and read back successfully;
it contains an IfcProject/site/building/storey and one IfcWall, with metric
units. This validates the backend path only. It does not validate floorplan
interpretation, wall ownership, opening semantics, adjacency, or source
provenance, so Bonsai remains downstream-only and cannot bypass S06/S07/S08.
