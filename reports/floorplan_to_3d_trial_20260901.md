# Public method trial: Yytsi/floorplan-to-3d

The repository and model assets were downloaded and inspected in an isolated
trial directory. The shipped demo output contains wall/door/window polygons,
but no metric scale, wall ownership, room IDs, side-space assignments,
adjacency graph, or source provenance. Its viewer uses fixed extrusion heights
and morphological closing can seal real door gaps. The local Python 3.14
environment lacked a usable PyTorch/torchvision inference stack, so no live
inference on sample 1308 was claimed.

Disposition: optional low-trust pixel candidate generator only; do not adopt as
authoritative parser or BIM/CAD-grade Blender/IFC input.

Live adapter trial over demos 4068/1191/3676 succeeded only at quarantine
level (walls/doors/windows normalized; rooms=0). Tampering semantic promotion
or source image SHA was rejected. Embedded demo rasters are not the 1308 source
dimensions, so their outputs cannot be merged without a separate registration
and provenance review.
