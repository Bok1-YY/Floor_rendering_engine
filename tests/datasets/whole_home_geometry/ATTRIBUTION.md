# Whole-home geometry dataset attribution

This directory contains metadata, hashes, selection rules, and small project-authored expectations only. Original datasets are downloaded to the gitignored `data/external_datasets/whole_home_geometry` directory and retain their upstream licenses.

## Automatically downloadable public candidates

- **IFC-Bench V2**, curated by Sylvain Hellin and contributors. Dataset metadata is CC BY 4.0; each IFC model retains the license stored beside that project. The selected FZK House, City House Munich, and Duplex architectural models are CC BY 4.0. The Fantasy Residential Building 1 model is MIT. Original author credits must be taken from the pinned upstream model card and license before any derivative fixture is redistributed: <https://huggingface.co/datasets/sylvainHellin/ifc-bench>.
- **FZK House / KIT IFC examples**: retain the KIT/source attribution named by the IFC-Bench model card.
- **Duplex Apartment / buildingSMART community sample files**: retain the buildingSMART project attribution and CC BY 4.0 notice.
- **Fantasy Residential Building 1**: retain the TUM BIM Fundamentals SS2025 student authorship and MIT notice.

## Restricted or link-only candidates

- **MLStructFP**: the repository code is MIT, but this catalog does not infer a license for the separately downloaded drawing archive. It remains link-only until its supplied data terms are reviewed: <https://github.com/MLSTRUCT/MLStructFP>.
- **CubiCasa5K**, Kalervo et al.: CC BY-NC 4.0, local non-commercial evaluation only: <https://github.com/CubiCasa/CubiCasa5k>.
- **FloorPlanCAD**, Fan et al.: CC BY-NC 4.0, local non-commercial recognition evaluation only: <https://floorplancad.github.io/>.
- **Structured3D**, Zheng et al.: separate Terms of Use agreement required; no automatic download or redistribution: <https://github.com/bertjiazheng/Structured3D>.
- **Zillow Indoor Dataset (ZInD)**, Cruz et al.: registration and Zillow research/non-commercial terms required; no automatic download or redistribution: <https://github.com/zillow/zind>.

Do not remove upstream license or model-card files from any locally prepared case. A derivative DXF, PNG, truth JSON, GeometryManifest, OBJ, or gray-model preview must carry the source SHA-256, preparation tool version, parameters, and this attribution chain. The executable L1 bundle records those fields in the ignored `case_manifest.json`; it must be regenerated from the pinned IFC rather than copied into product runtime data.
