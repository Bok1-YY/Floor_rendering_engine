# -*- coding: utf-8 -*-
"""Whole-home v2 API: metric shell, deterministic 3D captures, generation, QA."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import hmac
import math
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from . import server_state as state
from .api import call_image_generate
from .api import call_gpt_image_edit
from .api import call_fal_flux_canny_edit
from .config import GEMINI_MODEL_MAP, MAIN_OUTPUT_DIR, load_config, logger
from .floorplan_engine import call_gemini_json, load_analysis
from .records import (
    load_records_file,
    record_file_lock,
    save_api_result_jpg,
    save_api_result_png,
    save_records_file,
)
from .server_helpers import require_ref_image_path, require_upload_image_path, to_url
from .server_schemas import (
    WholeHomeAutoCameraRequest,
    WholeHomeCadReparseRequest,
    WholeHomeCadOpeningAnnotationsRequest,
    WholeHomeCadSpaceDraftRequest,
    WholeHomeCadAiAssistRequest,
    WholeHomeCadSemanticReconstructRequest,
    WholeHomeCadWallAssemblyConfirmRequest,
    WholeHomeCameraCandidatesRequest,
    WholeHomeCaptureRequest,
    WholeHomeContinueRequest,
    WholeHomeDevelopmentAutopilotRunRequest,
    WholeHomeDevelopmentReconcileRequest,
    WholeHomeAgentTaskClaimRequest,
    WholeHomeAgentTaskCompleteRequest,
    WholeHomeAgentTaskHeartbeatRequest,
    WholeHomeAgentWorkflowCreateRequest,
    WholeHomeAgentWorkflowTransitionRequest,
    WholeHomeExternalReviewRequest,
    WholeHomeModelSaveRequest,
    WholeHomeGeometryAcceptanceRequest,
    WholeHomeConstructionProfileRequest,
    WholeHomeSceneRecipeCommitRequest,
    WholeHomeSceneRecipePreviewRequest,
    WholeHomeSceneRecipeReviewRequest,
    WholeHomeGenerationDraftRequest,
    WholeHomeHistoryForkRequest,
    WholeHomeManualRunCommitRequest,
    WholeHomeManualRunPreviewRequest,
    WholeHomePanoCaptureRequest,
    WholeHomePanoEditRequest,
    WholeHomePanoGateRequest,
    WholeHomePanoHotspotRequest,
    WholeHomePanoMaterializeRequest,
    WholeHomePanoPaidPreviewRequest,
    WholeHomePanoReviewRequest,
    WholeHomeProjectRequest,
    WholeHomeQaRetryRequest,
    WholeHomeReferenceCaptureBatchRequest,
    WholeHomeRasterRegistrationPrepareRequest,
    WholeHomeResultReviewRequest,
    WholeHomeReviewCompleteRequest,
    WholeHomeRunRequest,
    WholeHomeSemanticLayoutRequest,
    WholeHomeSourceRegistrationRequest,
    WholeHomeTrainingConsentRequest,
    WholeHomeVariantBatchCommitRequest,
    WholeHomeVariantBatchPreviewRequest,
    WholeHomeVerifyRequest,
)
from .usage_stats import record_usage
from .whole_home_engine import (
    analyze_whole_home,
    analyze_semantic_layout,
    build_room_generation_contract,
    cas_update_project,
    build_generation_prompt,
    evaluate_final_local_gate,
    evaluate_structure_local_gate,
    evaluate_whole_home_phase,
    generate_semantic_camera_candidates,
    infer_camera_room_id,
    legacy_model_from_analysis,
    list_projects,
    list_runs,
    load_reference_camera_proposal,
    load_project,
    load_run,
    model_hash,
    prefer_historical_geometry,
    new_id,
    normalize_model,
    pano_manifest_hash,
    pano_file_sha256,
    project_view,
    rank_auto_camera_plan,
    runtime_project_copy,
    run_has_viewable_artifact,
    run_view,
    save_capture_data,
    save_camera_plan_overlay,
    save_pano_data,
    save_pano_image_file,
    save_project,
    save_reference_camera_proposal,
    save_review_manifest,
    save_run,
    state_hash,
    validate_model,
    validate_semantic_layout,
)
from .whole_home_learning import (
    WholeHomeLearningError,
    build_learning_export,
    complete_run_review,
    ensure_run_recipes,
    generation_spec_hash,
    get_run_review_state,
    learning_summary,
    list_learning_runs,
    review_result,
    set_training_consent,
    workflow_covered_room_ids,
)
from .whole_home_cad import (
    CadError,
    cad_plan_to_model,
    cad_hybrid_model_from_ai,
    cad_facts_hash,
    cad_runtime_status,
    cad_report_summary,
    ingest_cad,
    load_cad_draft_model,
    load_cad_report,
    persist_cad_report,
    public_reference_contract,
    refresh_hybrid_reference_anchor_report,
    reference_contract_for_url,
    reference_slot_for_room,
    resolve_reference_assets,
    require_managed_cad_path,
    save_cad_draft_model,
    validate_cad_model,
    validate_cad_semantic_overlay,
)
from .whole_home_geometry import (
    GeometryContractError,
    canonical_hash,
    production_readiness,
    validate_source_registration,
)
from .whole_home_geometry_acceptance import (
    RASTER_REVIEW_METRICS,
    build_cad_source_registration,
    build_project_geometry_acceptance,
)
from .whole_home_geometry_kernel import model_facts_hash as geometry_model_facts_hash
from .whole_home_professional import (
    ProfessionalContractError,
    build_floorplan_graph,
    build_marketing_proposal,
    confirm_construction_profile,
    default_construction_profile,
    generate_scene_recipe,
    professional_capabilities,
    review_scene_recipe,
    canonical_hash as professional_canonical_hash,
)
from .whole_home_raster_registration import (
    RasterRegistrationError,
    build_structure_evidence,
    lock_raster_scale,
    prepare_raster_source,
    wall_ink_support,
)
from .whole_home_wall_assembly import (
    WallAssemblyError,
    bind_manual_opening_annotations,
    confirm_ambiguous_assembly,
)
from .whole_home_cad_space import (
    CadSpaceError,
    apply_space_draft,
    model_summary as cad_space_model_summary,
    physical_facts_hash,
    semantic_overlay_hash,
)
from .whole_home_cad_reparse import (
    CadReparseOperationError,
    create_operation as create_cad_reparse_operation,
    get_operation as get_cad_reparse_operation,
    latest_operation_summary as latest_cad_reparse_operation_summary,
    public_operation as public_cad_reparse_operation,
    update_operation as update_cad_reparse_operation,
)
from .whole_home_cad import CAD_ROOT
from .whole_home_reference_camera import (
    evaluate_subject_id_pixels,
    find_reference_candidate,
    generate_reference_camera_candidates,
    pano_hotspot_origin_clear,
    reference_model_facts_hash,
    split_reference_contract,
)
from .whole_home_software_renderer import (
    SEMANTIC_COLORS,
    image_data_url,
    render_reference_candidate,
)
from .whole_home_autopilot import (
    BUDGET_ACCOUNTING_SCOPE,
    DevelopmentAutopilotError,
    bind_development_run,
    cancel_development_session,
    claim_development_run,
    finish_logical_call,
    get_development_session,
    mark_development_preflight_failed,
    mark_development_run_terminal,
    mark_logical_call_dispatched,
    prepare_development_batch,
    reconcile_development_session,
    reserve_logical_call,
)
from .whole_home_agent_workflow import (
    authorize_review_lease,
    claim_task as claim_agent_task,
    complete_task as complete_agent_task,
    create_workflow as create_agent_workflow,
    get_workflow as get_agent_workflow,
    heartbeat_task as heartbeat_agent_task,
    transition_workflow as transition_agent_workflow,
)
from .whole_home_external_review import (
    get_external_reviews,
    record_external_review,
    resolve_review_artifact,
)
from .whole_home_manual import (
    MANUAL_IMAGE_CALL_CAP,
    MANUAL_POLICY,
    MANUAL_QA_CALL_CAP,
    capabilities as manual_capabilities,
    claim_manual_run_commit,
    create_manual_run_preview,
    finish_manual_run_commit,
    get_manual_preview_project_id,
    manual_paid_enabled,
    manual_safe_enabled,
)
from .whole_home_history import (
    TERMINAL_BATCH_STATUSES,
    WholeHomeHistoryError,
    build_history as build_whole_home_history,
    canonical_hash as history_canonical_hash,
    claim_variant_batch,
    create_variant_preview,
    ensure_replay_snapshot,
    list_variant_batches,
    load_variant_batch,
    prepare_branch_project,
    public_variant_batch,
    replay_capability,
    request_variant_cancel,
    save_variant_batch,
    snapshot_project,
    transient_replay_snapshot,
)
from .whole_home_pano_edit import (
    build_erp_edit_prompt,
    build_flux_canny_erp_prompt,
    build_seam_repair_mask,
    build_seam_repair_prompt,
    build_structure_holdout_mask,
    circular_shift_erp,
    finalize_flux_canny_output,
    prepare_flux_canny_inputs,
)
from .whole_home_pano_gate import certify_geometry_locked_gate, gate_pano_erp
from .whole_home_pano_paid import (
    claim_pano_paid_stage,
    create_pano_paid_preview,
    persistable_pano_paid_preview,
    public_pano_paid_preview,
    restore_pano_paid_preview,
)
from .whole_home_pano_material import (
    MATERIAL_ENGINE_VERSION,
    materialize_geometry_locked_erp,
    verify_geometry_locked_replay,
)
from .whole_home_pano_render import (
    CUBE_FACE_ORDER as PANO_CUBE_FACE_ORDER,
    atlas_to_cube_faces,
    cube_to_erp,
)
from .config import (
    FAL_FLUX_CANNY_ERP_ENDPOINT,
    FAL_GPT_IMAGE_2_ENDPOINT,
    FLUX_CANNY_ERP_CORE_HEIGHT,
    FLUX_CANNY_ERP_CORE_WIDTH,
    FLUX_CANNY_ERP_GUTTER_PX,
    FLUX_CANNY_ERP_MODEL,
    FLUX_CANNY_ERP_PROVIDER_HEIGHT,
    FLUX_CANNY_ERP_PROVIDER_WIDTH,
    GPT_IMAGE_2_ERP_HEIGHT,
    GPT_IMAGE_2_ERP_WIDTH,
    OPENAI_IMAGE_EDITS_URL,
)


router = APIRouter()
_ACTIVE_PROJECTS: dict[str, dict] = {}
_ACTIVE_RUNS: dict[str, dict] = {}
_RUN_KEYS: dict[str, str] = {}
_DEVELOPMENT_CLAIM_PROOFS: dict[str, dict] = {}
_CANCELLED: set[str] = set()
_ACTIVE_VARIANT_BATCHES: set[str] = set()
_QA_SEMAPHORE = asyncio.Semaphore(1)
_DEVELOPMENT_POLICY = 'development_autopilot_v1'


def _raise_history_error(ex: WholeHomeHistoryError) -> None:
    raise HTTPException(ex.status_code, ex.to_dict()) from ex


def _project_entry(project_id: str) -> Optional[dict]:
    active = _ACTIVE_PROJECTS.get(project_id)
    # CAD mutations complete through cross-process CAS.  Never let an old
    # in-process object shadow the authoritative project JSON afterwards.
    project = (active if active and active.get('source_type') != 'cad'
               else load_project(project_id))
    runtime = runtime_project_copy(project) if project else None
    if (runtime and runtime.get('source_type') == 'cad'
            and str(((runtime.get('model') or {}).get('cad_semantic_derivation') or {}).get('method') or '')
            == 'gemini_room_polygon_on_audited_cad_raster_v1'):
        # The raw CAD report predates semantic room containment.  Recompute on
        # the runtime copy so read-only loads never persist a migration, while
        # camera/save operations naturally retain the audited refreshed report.
        refresh_hybrid_reference_anchor_report(runtime.get('model') or {})
    return runtime


def _run_entry(run_id: str) -> Optional[dict]:
    return _ACTIVE_RUNS.get(run_id) or load_run(run_id)


def _persist_project(project: dict) -> None:
    save_project(project)
    project_id = str(project.get('project_id') or '')
    # floorplan 分析期间会保留一个内存条目；所有同步路由都在 runtime copy 上修改。
    # 写盘后必须同步该条目，否则 capture→paid-preview 这种紧邻请求会读到旧副本。
    if project_id and project_id in _ACTIVE_PROJECTS:
        _ACTIVE_PROJECTS[project_id] = copy.deepcopy(project)


def _cad_report(project: dict) -> dict:
    try:
        return load_cad_report(project.get('parse_report') or {})
    except CadError:
        raise


def _cad_space_raw_faces(project: dict, report: dict, model: dict) -> list[dict]:
    """Return authoritative CAD faces in the project's public model basis.

    Historical parse reports intentionally retain the pre-v2 translated face
    polygons as audit evidence.  Production v2 models use CAD +Y -> model -Z,
    however, so passing those historical polygons straight into the space-draft
    validator makes a lossless GET -> PUT round trip fail.  Rebuild public model
    polygons from immutable CAD coordinates and the persisted affine transform;
    only fall back to a depth mirror for reports that predate that transform.
    """
    rows = copy.deepcopy(report.get('raw_faces') or [])
    if int(model.get('coordinate_contract_version') or 0) < 2:
        return rows

    forward = report.get('cad_to_model') if isinstance(report.get('cad_to_model'), dict) else {}
    depth_m = float(model.get('depth_m') or 0.0)

    def transform_ring(cad_ring, model_ring):
        if (isinstance(cad_ring, list) and len(cad_ring) >= 3
                and all(isinstance(point, (list, tuple)) and len(point) >= 2
                        for point in cad_ring)):
            return [
                {'x': round(cad_plan_to_model(point, forward)[0], 5),
                 'z': round(cad_plan_to_model(point, forward)[1], 5)}
                for point in cad_ring
            ]
        result = []
        for point in model_ring or []:
            if not isinstance(point, dict) or 'x' not in point or 'z' not in point:
                result.append(copy.deepcopy(point))
                continue
            result.append({
                **copy.deepcopy(point),
                'x': round(float(point['x']), 5),
                'z': round(depth_m - float(point['z']), 5),
            })
        return result

    for row in rows:
        row['polygon'] = transform_ring(row.get('cad_polygon_m'), row.get('polygon'))
        cad_rings = row.get('cad_interior_rings_m') or []
        model_rings = row.get('interior_rings') or []
        ring_count = max(len(cad_rings), len(model_rings))
        row['interior_rings'] = [
            transform_ring(
                cad_rings[index] if index < len(cad_rings) else None,
                model_rings[index] if index < len(model_rings) else None,
            )
            for index in range(ring_count)
        ]
        for anchor in row.get('anchors') or []:
            point_m = anchor.get('point_m')
            if (isinstance(point_m, (list, tuple)) and len(point_m) >= 2
                    and forward):
                x_value, z_value = cad_plan_to_model(point_m, forward)
                anchor['point'] = {'x': round(x_value, 5), 'z': round(z_value, 5)}
            elif isinstance(anchor.get('point'), dict) and 'z' in anchor['point']:
                anchor['point']['z'] = round(depth_m - float(anchor['point']['z']), 5)
    return rows


def _cad_space_editor_model(model: dict) -> tuple[list[dict], list[dict]]:
    """Project production spaces into the strict, client-editable DTO contract."""
    physical_spaces = []
    for row in model.get('physical_spaces') or []:
        physical_spaces.append({
            'id': str(row.get('id') or ''),
            'label': str(row.get('label') or row.get('id') or '未命名空间'),
            'space_type': str(row.get('space_type') or 'other'),
            'face_ids': [str(value) for value in row.get('face_ids') or []],
            'polygon': [
                {'x': float(point.get('x') or 0.0), 'z': float(point.get('z') or 0.0)}
                for point in row.get('polygon') or [] if isinstance(point, dict)
            ],
            'selected': bool(row.get('selected', True)),
        })

    semantic_zones = []
    geometry_keys = ('kind', 'points', 'min_x', 'min_z', 'max_x', 'max_z',
                     'start', 'end', 'side')
    for row in model.get('semantic_zones') or []:
        source_geometry = row.get('geometry') if isinstance(row.get('geometry'), dict) else {}
        geometry = {
            key: copy.deepcopy(source_geometry[key])
            for key in geometry_keys if key in source_geometry and source_geometry[key] is not None
        }
        semantic_zones.append({
            'id': str(row.get('id') or ''),
            'physical_space_id': str(row.get('physical_space_id') or ''),
            'label': str(row.get('label') or row.get('id') or '未命名语义区'),
            'zone_type': str(row.get('zone_type') or 'other'),
            'geometry': geometry,
        })
    return physical_spaces, semantic_zones


def _cad_space_confirmation_view(project: dict) -> dict:
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    confirmation = copy.deepcopy(model.get('space_confirmation') or {})
    return {
        'status': str(confirmation.get('status') or 'needs_review'),
        'physical_space_count': int(
            confirmation.get('physical_space_count') or len(model.get('physical_spaces') or [])),
        'semantic_zone_count': int(
            confirmation.get('semantic_zone_count') or len(model.get('semantic_zones') or [])),
        'reason_codes': [str(value)[:120] for value in confirmation.get('reason_codes') or []][:50],
        'physical_facts_hash': str(model.get('physical_facts_hash') or ''),
        'semantic_overlay_hash': str(model.get('semantic_overlay_hash') or ''),
        'space_model_schema_version': int(model.get('space_model_schema_version') or 0),
    }


def _geometry_current_facts(project: dict, model: dict, registration: dict) -> dict:
    manifest = model.get('geometry_manifest') if isinstance(model.get('geometry_manifest'), dict) else {}
    try:
        current_model_facts_hash = geometry_model_facts_hash(model)
    except (GeometryContractError, ValueError):
        # The readiness layer will emit the precise geometry-contract issue;
        # this sentinel keeps project views and production gates fail-closed
        # instead of turning malformed persisted geometry into an HTTP 500.
        current_model_facts_hash = 'invalid_geometry_facts'
    return {
        'source_hash': str(registration.get('source_hash') or ''),
        'model_revision': int(project.get('revision') or 0),
        'model_facts_hash': current_model_facts_hash,
        'registration_hash': str(registration.get('registration_hash') or ''),
        'cad_facts_hash': (cad_facts_hash(model) if project.get('source_type') == 'cad' else ''),
        'geometry_kernel_version': str(manifest.get('geometry_kernel_version') or ''),
        'manifest_hash': str(manifest.get('manifest_hash') or ''),
    }


def _geometry_contract_view(project: dict) -> dict:
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    manifest = model.get('geometry_manifest') if isinstance(model.get('geometry_manifest'), dict) else {}
    report = project.get('geometry_acceptance') if isinstance(project.get('geometry_acceptance'), dict) else {}
    registration = project.get('source_registration')
    if not isinstance(registration, dict):
        registration = model.get('source_registration') if isinstance(model.get('source_registration'), dict) else {}
    current_facts = _geometry_current_facts(project, model, registration)
    readiness = production_readiness(
        project, report=report or None, manifest=manifest or None,
        current_facts=current_facts,
    )
    return {
        'required': bool(project.get('geometry_acceptance_required')),
        'input_grade': str(project.get('input_grade') or model.get('input_grade') or 'legacy_unproven'),
        'geometry_facts_hash': current_facts['model_facts_hash'],
        'cad_geometry_fingerprint': (
            current_facts['model_facts_hash'] if project.get('source_type') == 'cad' else ''),
        'registration': {
            key: copy.deepcopy(registration.get(key)) for key in (
                'version', 'source_type', 'input_grade', 'source_hash', 'cad_units',
                'registration_hash', 'roundtrip_error', 'roundtrip_threshold',
                'scale_anchor_count', 'scale_disagreement') if key in registration
        },
        'raster_alignment_metrics': copy.deepcopy(
            project.get('raster_alignment_metrics') or {}),
        'manifest': {
            'version': manifest.get('version'), 'manifest_hash': manifest.get('manifest_hash') or '',
            'model_facts_hash': manifest.get('model_facts_hash') or '',
            'geometry_kernel_version': manifest.get('geometry_kernel_version') or '',
            'vertex_count': len(manifest.get('vertices') or []),
            'wall_part_count': len(manifest.get('wall_parts') or []),
            'floor_part_count': len(manifest.get('floor_parts') or []),
            'opening_void_count': len(manifest.get('opening_voids') or []),
        },
        'acceptance': {
            key: copy.deepcopy(report.get(key)) for key in (
                'report_id', 'report_hash', 'status', 'model_revision', 'model_facts_hash',
                'manifest_hash', 'issues', 'human_review', 'metrics') if key in report
        },
        'production_readiness': readiness,
    }


def _assert_geometry_production_gate(project: dict) -> None:
    """Apply Correspondence Lock v1 only to projects enrolled in the new contract."""
    if not project.get('geometry_acceptance_required'):
        return
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    registration = project.get('source_registration')
    if not isinstance(registration, dict):
        registration = model.get('source_registration') if isinstance(model.get('source_registration'), dict) else {}
    readiness = production_readiness(
        project,
        report=project.get('geometry_acceptance') if isinstance(project.get('geometry_acceptance'), dict) else None,
        manifest=model.get('geometry_manifest') if isinstance(model.get('geometry_manifest'), dict) else None,
        current_facts=_geometry_current_facts(project, model, registration),
    )
    if not readiness['ready']:
        raise HTTPException(409, {
            'message': '图纸与 3D 模型尚未通过对应锁验收；禁止进入机位、全景或付费生成',
            **readiness,
            'gate_code': readiness.get('code') or 'not_ready',
            'code': 'geometry_correspondence_not_ready',
        })


def _whole_home_project_view(project: dict, *, list_mode: bool = False) -> dict:
    """Keep hot project responses small; full CAD evidence has a dedicated GET."""
    if project.get('source_type') == 'cad':
        if list_mode:
            floorplan_url = to_url(project.get('floorplan_path'))
            summary = cad_space_model_summary(project.get('model') or {})
            summary['capture_count'] = len(project.get('captures') or [])
            summary['reference_contract_id'] = str(
                (project.get('reference_contract') or {}).get('contract_id') or '')
            return {
                key: copy.deepcopy(project.get(key)) for key in (
                    'project_id', 'source_type', 'status', 'stage', 'error', 'summary',
                    'created_at', 'updated_at', 'revision', 'verified', 'verified_revision',
                    'lineage', 'generation_draft')
                if key in project
            } | {
                'floorplan_url': floorplan_url, 'cad_geometry_read_only': True,
                'cad_space_draft': _cad_space_confirmation_view(project),
                'cad_reparse_summary': latest_cad_reparse_operation_summary(
                    CAD_ROOT, str(project.get('project_id') or '')),
                'model_summary': summary,
                'professional': _professional_summary(project),
            }
        base_keys = (
            'project_id', 'source_type', 'status', 'stage', 'error', 'summary',
            'created_at', 'updated_at', 'revision', 'verified', 'verified_revision',
            'floorplan_path', 'cad_import', 'model', 'captures', 'pano_captures', 'operations',
            'cad_ai_advisories',
            'reference_contract', 'lineage', 'generation_draft', 'history_read_only',
            'history_snapshot_id',
            'construction_profile', 'scene_recipes', 'active_scene_recipe_id',
            'professional_revision',
        )
        value = {}
        for key in base_keys:
            if key not in project:
                continue
            if key == 'model':
                raw_model = project.get('model') or {}
                value['model'] = {
                    field: copy.deepcopy(raw_model.get(field)) for field in (
                        'schema_version', 'space_model_schema_version', 'model_id',
                        'coordinate_system', 'coordinate_contract_version',
                        'plan_axis_convention', 'width_m', 'depth_m', 'wall_height_m',
                        'wall_thickness_m', 'scale', 'walls', 'wall_assemblies', 'openings', 'rooms',
                        'global_wall_footprints', 'global_wall_topology',
                        'physical_spaces', 'semantic_zones', 'excluded_face_ids',
                        'fixed_objects', 'cameras', 'uncertainties', 'room_contracts',
                        'semantic_report', 'geometry_report', 'reference_anchor_report',
                        'space_confirmation', 'cad_facts_hash', 'physical_facts_hash',
                        'semantic_overlay_hash', 'cad_to_model', 'model_to_cad',
                        'geometry_schema_version', 'input_grade', 'model_facts_hash')
                    if field in raw_model
                }
                # Old CAD revisions predate editable camera/uncertainty arrays,
                # while WholeHomeStudio deliberately treats them as ordinary
                # empty collections.  Hydrate the public view instead of
                # mutating the immutable stored revision.
                for field in ('walls', 'openings', 'rooms', 'fixed_objects',
                              'cameras', 'uncertainties', 'room_contracts'):
                    value['model'].setdefault(field, [])
                value['model'].setdefault(
                    'semantic_report', {'status': 'complete', 'hard_errors': [], 'warnings': []})
                value['model'].setdefault(
                    'geometry_report', {'hard_errors': [], 'warnings': []})
            elif key == 'captures':
                value['captures'] = copy.deepcopy((project.get('captures') or [])[-100:])
            elif key == 'pano_captures':
                # This is the reload contract for the browser sphere viewer.
                # Keeping pano IDs only inside operations/history made a newly
                # generated panorama disappear after a page refresh.
                rows = copy.deepcopy((project.get('pano_captures') or [])[-100:])
                for pano in rows:
                    channels = (pano.get('manifest') or {}).get('channels') or {}
                    pano['channel_urls'] = {
                        name: to_url(path) for name, path in channels.items() if path
                    }
                    for image_key in ('edited_rgb', 'repaired_rgb'):
                        pano[f'{image_key}_url'] = to_url(
                            pano.get(f'{image_key}_path'))
                value['pano_captures'] = rows
            elif key == 'operations':
                value['operations'] = copy.deepcopy((project.get('operations') or [])[-100:])
            elif key == 'cad_ai_advisories':
                value['cad_ai_advisories'] = copy.deepcopy(
                    (project.get('cad_ai_advisories') or [])[-10:])
            elif key == 'reference_contract':
                value['reference_contract'] = public_reference_contract(
                    project.get('reference_contract') or {})
            else:
                value[key] = copy.deepcopy(project.get(key))
        value['floorplan_url'] = to_url(project.get('floorplan_path'))
        value['cad_geometry_read_only'] = True
        cad_source = (project.get('cad_source')
                      if isinstance(project.get('cad_source'), dict) else {})
        source_path = str(cad_source.get('path') or '')
        value['cad_source'] = {
            'name': os.path.basename(source_path),
            'sha256': str(cad_source.get('sha256') or ''),
            'format': str(cad_source.get('format') or ''),
            'version': str(cad_source.get('version') or ''),
            'size_bytes': (os.path.getsize(source_path)
                           if source_path and os.path.isfile(source_path) else 0),
        }
        value['cad_space_draft'] = _cad_space_confirmation_view(project)
        value['cad_reparse_summary'] = latest_cad_reparse_operation_summary(
            CAD_ROOT, str(project.get('project_id') or ''))
        report = project.get('parse_report') if isinstance(project.get('parse_report'), dict) else {}
        if report:
            summary = cad_report_summary(report) if report.get('raw_faces') else report
            project_id = str(project.get('project_id') or '')
            value['parse_report'] = {
                'schema_version': summary.get('schema_version'),
                'source_sha256': summary.get('source_sha256') or '',
                'insunits': summary.get('insunits'),
                'resolved_insunits': summary.get('resolved_insunits', summary.get('insunits')),
                'declared_unit_scale_to_m': summary.get(
                    'declared_unit_scale_to_m', summary.get('unit_scale_to_m')),
                'unit_scale_to_m': summary.get('unit_scale_to_m'),
                'unit_resolution': copy.deepcopy(summary.get('unit_resolution') or {}),
                'structural_entity_count': summary.get('structural_entity_count'),
                'selected_structural_entity_count': summary.get(
                    'selected_structural_entity_count'),
                'ignored_nonstructural_count': summary.get(
                    'ignored_nonstructural_count'),
                'layer_count': summary.get('layer_count'),
                'block_count': summary.get('block_count'),
                'selected_candidate_id': summary.get('selected_candidate_id') or '',
                'candidate_plan_count': summary.get('candidate_plan_count') or 0,
                'raw_face_count': summary.get('raw_face_count') or 0,
                'alignment_metrics': copy.deepcopy(summary.get('alignment_metrics') or {}),
                'selected_entity_role_summary': copy.deepcopy(
                    summary.get('selected_entity_role_summary') or {}),
                'raw_opening_summary': copy.deepcopy(
                    summary.get('raw_opening_summary') or {}),
                'global_wall_topology': copy.deepcopy(
                    summary.get('global_wall_topology') or {}),
                'hard_error_summary': copy.deepcopy(summary.get('hard_error_summary') or []),
                'warning_summary': copy.deepcopy(summary.get('warning_summary') or []),
                'report_url': f'/api/whole-home/projects/{project_id}/cad/report',
                'candidate_plans': [{
                    'candidate_id': row.get('candidate_id') or '',
                    'bbox_m': copy.deepcopy(row.get('bbox_m') or []),
                    'selection_score': row.get('selection_score'),
                    'closed_region_count': row.get('closed_region_count'),
                    'structural_entity_count': row.get('structural_entity_count'),
                    'preview_url': (
                        f"/api/whole-home/projects/{project_id}/cad/candidates/"
                        f"{row.get('candidate_id')}/preview"),
                } for row in (summary.get('candidate_plans') or [])[:20]],
            }
    else:
        if list_mode:
            # Project lists are navigation metadata, not full project views.
            # Calling project_view() here also builds the learning projection
            # for every historical project and can turn a 16-row list into a
            # 40-second request.  The selected row is fetched through the
            # dedicated project endpoint immediately afterwards.
            value = {
                key: copy.deepcopy(project.get(key)) for key in (
                    'project_id', 'source_type', 'status', 'stage', 'error', 'summary',
                    'created_at', 'updated_at', 'revision', 'verified', 'verified_revision',
                    'lineage', 'generation_draft', 'history_read_only', 'history_snapshot_id')
                if key in project
            }
            value['floorplan_url'] = to_url(project.get('floorplan_path'))
            value['model_summary'] = cad_space_model_summary(project.get('model') or {})
            value['model_summary']['capture_count'] = len(project.get('captures') or [])
            value['model_summary']['reference_contract_id'] = str(
                (project.get('reference_contract') or {}).get('contract_id') or '')
            value['geometry_contract'] = _geometry_contract_view(project)
            return value
        value = project_view(project)
        if isinstance(value.get('model'), dict):
            value['model'].pop('geometry_manifest', None)
    value['professional'] = _professional_summary(project)
    value['geometry_contract'] = _geometry_contract_view(project)
    if list_mode:
        value['model_summary'] = cad_space_model_summary(project.get('model') or {})
        value['model_summary']['capture_count'] = len(project.get('captures') or [])
        value['model_summary']['reference_contract_id'] = str(
            (project.get('reference_contract') or {}).get('contract_id') or '')
        keep = {
            'project_id', 'source_type', 'status', 'stage', 'error', 'summary',
            'created_at', 'updated_at', 'revision', 'verified', 'verified_revision',
            'floorplan_url', 'cad_space_draft', 'cad_geometry_read_only', 'model_summary',
            'cad_reparse_summary',
            'geometry_contract',
            'professional',
        }
        value = {key: copy.deepcopy(item) for key, item in value.items() if key in keep}
    return value


def _whole_home_run_list_view(run: dict) -> dict:
    """Navigation summary for the run picker; full results load on replay."""
    value = {
        key: copy.deepcopy(run.get(key)) for key in (
            'run_id', 'project_id', 'status', 'stage', 'error', 'created_at', 'updated_at',
            'floor_path', 'style', 'lighting', 'prompt', 'model_keys', 'aspect_ratio',
            'resolution', 'model_revision', 'model_hash', 'replay_snapshot_ref',
            'variant_of_run_id', 'variant_batch_id', 'variant_item_id',
            'summary_counts', 'actual_generation_calls', 'actual_qa_calls')
        if key in run
    }
    value['floor_url'] = to_url(run.get('floor_path'))
    value['result_count'] = len(run.get('results') or [])
    # The client fetches immutable replay before displaying a run.  Keeping an
    # empty compatibility array avoids shipping all attempts and QA ledgers in
    # the initial navigation request.
    value['results'] = []
    return value


def _persist_run(run: dict) -> None:
    ledger = run.get('call_ledger') or []
    def dispatched(row: dict) -> bool:
        budget_status = str(row.get('budget_status') or '')
        return not budget_status or budget_status in (
            'dispatched', 'done', 'failed', 'uncertain_after_restart')

    run['actual_generation_calls'] = sum(
        row.get('kind') == 'generation' and dispatched(row) for row in ledger)
    run['actual_qa_calls'] = sum(
        row.get('kind') == 'qa' and dispatched(row) for row in ledger)
    run['reserved_generation_calls'] = sum(
        row.get('kind') == 'generation' and row.get('budget_status') == 'reserved'
        for row in ledger)
    run['reserved_qa_calls'] = sum(
        row.get('kind') == 'qa' and row.get('budget_status') == 'reserved'
        for row in ledger)
    run['actual_local_gate_calls'] = sum(row.get('kind') == 'local_gate' for row in ledger)
    run['successful_generation_calls'] = sum(
        row.get('kind') == 'generation' and row.get('status') == 'done' for row in ledger)
    run['review_manifest_path'] = save_review_manifest(run)
    save_run(run)


def _is_development_run(run: Optional[dict]) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get('execution_policy') == _DEVELOPMENT_POLICY
        and run.get('development_session_id')
    )


def _assert_manual_call_cap(run: Optional[dict], kind: str) -> None:
    """Fail closed before provider dispatch when a manual-run cap is spent."""
    if not isinstance(run, dict) or run.get('execution_policy') != MANUAL_POLICY:
        return
    caps = run.get('manual_call_caps') or {}
    cap_key = 'image_calls' if kind == 'generation' else 'qa_calls'
    default_cap = MANUAL_IMAGE_CALL_CAP if kind == 'generation' else MANUAL_QA_CALL_CAP
    cap = int(caps.get(cap_key) or default_cap)
    consumed = sum(
        row.get('kind') == kind
        and str(row.get('budget_status') or '') != 'cancelled_before_dispatch'
        for row in (run.get('call_ledger') or []))
    if consumed >= cap:
        raise DevelopmentAutopilotError(
            'manual_call_cap_exhausted',
            f'手动任务 {kind} 调用已达到上限 {cap}，禁止继续付费调用',
            409, {'kind': kind, 'cap': cap, 'consumed': consumed})


def _development_error_detail(ex: DevelopmentAutopilotError) -> dict:
    return ex.to_dict()


def _development_claim_proof(run: dict) -> dict:
    proof = copy.deepcopy(_DEVELOPMENT_CLAIM_PROOFS.get(
        str(run.get('run_id') or '')) or {})
    expected = {
        'run_claim_id': str(run.get('development_run_claim_id') or ''),
        'claim_generation': int(run.get('development_claim_generation') or 0),
        'request_fingerprint': str(
            run.get('development_request_fingerprint') or ''),
    }
    if (not proof.get('claim_token')
            or any(proof.get(key) != value for key, value in expected.items())):
        raise DevelopmentAutopilotError(
            'development_run_claim_proof_unavailable',
            '开发 run claim 的进程内 bearer proof 不可用；禁止恢复付费调用', 409)
    return proof


def _reserve_development_call(run: dict, ledger_row: dict) -> str:
    """Persist session capacity, then the run ledger, before provider dispatch."""
    if not _is_development_run(run):
        return ''
    try:
        proof = _development_claim_proof(run)
        reservation = reserve_logical_call(
            session_id=str(run.get('development_session_id') or ''),
            batch_index=int(run.get('development_batch_index') or 0),
            run_id=str(run.get('run_id') or ''),
            call_id=str(ledger_row.get('call_id') or ''),
            kind=str(ledger_row.get('kind') or ''),
            phase=str(ledger_row.get('phase') or ''),
            result_id=str(ledger_row.get('result_id') or ''),
            attempt_id=str(ledger_row.get('attempt_id') or ''),
            **proof,
        )
    except DevelopmentAutopilotError as ex:
        ledger_row.update(
            status='blocked', budget_status='denied',
            budget_accounting_scope=BUDGET_ACCOUNTING_SCOPE,
            error=f'{ex.code}: {ex.message}', finished_at=time.time(),
        )
        run.setdefault('call_ledger', []).append(ledger_row)
        run['development_stop_reason'] = f'{ex.code}: {ex.message}'
        _persist_run(run)
        raise
    ledger_row.update(
        status='reserved', budget_status='reserved',
        budget_accounting_scope=BUDGET_ACCOUNTING_SCOPE,
        reservation_id=reservation['reservation_id'],
        reserved_at=reservation.get('reserved_at'),
    )
    run.setdefault('call_ledger', []).append(ledger_row)
    _persist_run(run)
    return str(reservation['reservation_id'])


def _dispatch_development_call(run: dict, ledger_row: dict,
                               reservation_id: str) -> None:
    if not reservation_id:
        return
    try:
        proof = _development_claim_proof(run)
        reservation = mark_logical_call_dispatched(
            str(run.get('development_session_id') or ''), reservation_id,
            **proof)
    except DevelopmentAutopilotError as ex:
        ledger_row.update(
            status='cancelled', budget_status='cancelled_before_dispatch',
            finished_at=time.time(), error=f'{ex.code}: {ex.message}',
        )
        _persist_run(run)
        raise
    ledger_row.update(
        status='running', budget_status='dispatched',
        dispatched_at=reservation.get('dispatched_at'),
        started_at=reservation.get('dispatched_at') or time.time(),
    )
    _persist_run(run)


def _finish_development_call(run: dict, ledger_row: dict,
                             reservation_id: str, *, success: bool,
                             error: str = '') -> None:
    if not reservation_id:
        return
    proof = _development_claim_proof(run)
    finish_logical_call(
        str(run.get('development_session_id') or ''), reservation_id,
        success=success, error=error, **proof)
    ledger_row.update(
        status='done' if success else 'failed',
        budget_status='done' if success else 'failed',
    )


def _assert_cad_project_gate(project: dict) -> None:
    if project.get('source_type') != 'cad':
        return
    expected_hash = str((project.get('cad_import') or {}).get('cad_facts_hash') or '')
    current_hash = cad_facts_hash(project.get('model') or {})
    if not expected_hash or current_hash != expected_hash:
        raise HTTPException(409, {
            'message': 'CAD 权威几何哈希不一致；必须从原 CAD 重新解析，禁止继续 AI 或付费生成',
            'code': 'cad_facts_hash_mismatch', 'expected': expected_hash, 'actual': current_hash,
        })
    confirmation = (project.get('model') or {}).get('space_confirmation') or {}
    if (project.get('model') or {}).get('space_model_schema_version') == 1 and (
            confirmation.get('status') != 'confirmed'):
        raise HTTPException(409, {
            'message': 'CAD 物理空间/语义分区或门洞拓扑尚未全部人工确认',
            'code': 'cad_space_confirmation_required',
            'space_confirmation': copy.deepcopy(confirmation),
        })
    validation = validate_cad_model(project.get('model') or {}, project.get('parse_report') or {})
    if validation.get('hard_errors'):
        raise HTTPException(409, {
            'message': 'CAD 本地硬门禁未通过；禁止继续 AI 或付费生成',
            'code': 'cad_gate_blocked', **validation,
        })


def _sha256_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _input_manifest(paths: list[str], reference_assets: Optional[list[dict]] = None) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for path in paths:
        value = os.path.realpath(str(path or '')) if path else ''
        if not value or value in seen:
            continue
        seen.add(value)
        row = {'path': value, 'sha256': _sha256_file(value)}
        reference = next((
            asset for asset in (reference_assets or [])
            if str(asset.get('sha256') or '') and asset.get('sha256') == row['sha256']
        ), None)
        if reference:
            row.update({
                key: copy.deepcopy(reference.get(key))
                for key in ('role', 'slot_id', 'asset_id', 'width', 'height', 'mime',
                            'scene_id', 'scene_record_id', 'export_allowed')
            })
        rows.append(row)
    return rows


def _reference_asset_manifest(contract: dict, slot_ids: list[str], *, include_path: bool) -> list[dict]:
    wanted = set(str(value or '') for value in slot_ids)
    rows: list[dict] = []
    seen: set[str] = set()
    for slot in contract.get('slots') or []:
        slot_id = str(slot.get('slot_id') or '')
        if slot_id not in wanted or slot_id in seen:
            continue
        seen.add(slot_id)
        asset = slot.get('reference_asset') or {}
        viewpoint = slot.get('reference_viewpoint') or {}
        row = {
            'role': 'current_slot_reference',
            'slot_id': slot_id,
            'asset_id': asset.get('asset_id') or '',
            'sha256': asset.get('sha256') or '',
            'width': asset.get('width') or asset.get('expected_width'),
            'height': asset.get('height') or asset.get('expected_height'),
            'mime': asset.get('mime') or asset.get('expected_mime'),
            'scene_id': viewpoint.get('scene_id'),
            'scene_record_id': viewpoint.get('scene_record_id'),
            'export_allowed': False,
        }
        if include_path:
            row['path'] = asset.get('local_path') or ''
        rows.append(row)
    return rows


def _reference_slot_camera_error(slot_id: str, message: str) -> HTTPException:
    return HTTPException(409, {
        'code': 'reference_slot_camera_missing',
        'message': f'{slot_id}: {message}',
    })


def _reference_room_profile_binding(actual_profile: str, slot_profile: str) -> str:
    """Return the audited binding mode, or an empty string when incompatible.

    The public benchmark names three bathroom views, but those scene names do
    not prove three distinct CAD rooms.  The slot camera planner deliberately
    binds all three compositions to one CAD wet/dry suite when the DWG contains
    exactly one such room.  Keep that honest distinction in the run snapshot:
    the slot controls composition while the single CAD room controls geometry.
    """
    actual = str(actual_profile or '')
    expected = str(slot_profile or '')
    if actual and actual == expected:
        return 'exact_profile'
    bathroom_profiles = {
        'bathroom', 'bathroom_master', 'bathroom_secondary', 'dry_vanity',
    }
    if actual in bathroom_profiles and expected in bathroom_profiles:
        return 'shared_cad_wet_dry_suite'
    return ''


def _assert_reference_slot_camera(project_reference: dict, slot: dict, capture: dict,
                                  requested_slot_id: str) -> None:
    camera = capture.get('camera') or {}
    if (str(capture.get('reference_slot_id') or '') != requested_slot_id
            or str(camera.get('reference_slot_id') or '') != requested_slot_id):
        raise _reference_slot_camera_error(requested_slot_id, 'capture/camera 未精确绑定当前 slot')
    viewpoint = slot.get('reference_viewpoint') or {}
    scene_id = viewpoint.get('scene_id')
    landing = viewpoint.get('landing_policy') or {}
    if not scene_id:
        raise _reference_slot_camera_error(requested_slot_id, '审计 reference_viewpoint 缺少 scene_id')
    focal = float(camera.get('focal_length_mm') or 0)
    focal_range = slot.get('focal_length_mm') or {}
    if not (float(focal_range.get('min') or 24) <= focal <= float(focal_range.get('max') or 35)):
        raise _reference_slot_camera_error(
            requested_slot_id, f"焦距必须为 {focal_range.get('min')}-{focal_range.get('max')}mm")
    eye_height = float((camera.get('position') or {}).get('y') or 0)
    eye_range = (project_reference.get('camera') or {}).get('eye_height_m') or {}
    if not (float(eye_range.get('min') or 1.35) <= eye_height
            <= float(eye_range.get('max') or 1.55)):
        raise _reference_slot_camera_error(requested_slot_id, '机位眼高超出合同')
    position, target = camera.get('position') or {}, camera.get('target') or {}
    horizontal = max(1e-9, (
        (float(target.get('x') or 0) - float(position.get('x') or 0)) ** 2
        + (float(target.get('z') or 0) - float(position.get('z') or 0)) ** 2
    ) ** .5)
    vertical_deg = abs(math.degrees(math.atan2(
        float(target.get('y') or 0) - eye_height, horizontal)))
    if vertical_deg > float(
            (project_reference.get('camera') or {}).get('vertical_deviation_deg_max') or 1):
        raise _reference_slot_camera_error(requested_slot_id, '垂直偏移超过合同')
    evidence = camera.get('reference_contract_validation') or {}
    expected_source = 'inferred_from_reference_visual_and_cad_anchors'
    if (evidence.get('slot_id') != requested_slot_id
            or str(evidence.get('scene_id') or '') != str(scene_id)
            or evidence.get('landing_policy_mode') != 'cad_semantic_relative_region'
            or evidence.get('landing_policy_mode') != landing.get('mode')
            or evidence.get('landing_source') != expected_source
            or evidence.get('landing_source') != landing.get('source')
            or evidence.get('cad_position_pass') is not True
            or evidence.get('collision_pass') is not True
            or evidence.get('visibility_pass') is not True
            or evidence.get('safe_frame_pass') is not True):
        raise _reference_slot_camera_error(
            requested_slot_id, '缺少 slot/scene/relative-landing/CAD-position/collision/visibility/safe-frame 本地证据')
    safe_frame = (project_reference.get('camera') or {}).get('safe_frame') or {}
    bounds = evidence.get('must_show_bounds') or []
    # Only concrete object/opening anchors require subject-ID pixel bounds.
    # Scalar constraints such as circulation clearance and same floor elevation
    # remain mandatory, but are proven by deterministic camera/geometry fields
    # rather than by inventing an image-mask object for an abstract concept.
    required_subjects = {
        str(row.get('subject') or '')
        for row in (evidence.get('must_show_subjects') or [])
        if isinstance(row, dict) and str(row.get('subject') or '')
    }
    observed_subjects = {
        str(row.get('subject') or '') for row in bounds if isinstance(row, dict)
    }
    bounds_valid = bool(
        required_subjects and observed_subjects == required_subjects
        and len(bounds) == len(required_subjects))
    subject_overrides = slot.get('subject_safe_frame_overrides') or {}
    # Older persisted CAD projects predate per-subject framing overrides.
    # Camera capture already re-evaluates the canonical local contract, so the
    # paid-run gate must use the same audited policy instead of rejecting that
    # server-issued evidence merely because the project snapshot is older.
    if not subject_overrides:
        canonical = reference_contract_for_url(
            str(project_reference.get('public_reference_url') or ''))
        canonical_slot = next((
            row for row in (canonical.get('slots') or [])
            if str(row.get('slot_id') or '') == requested_slot_id
        ), {})
        subject_overrides = canonical_slot.get('subject_safe_frame_overrides') or {}
    for bound in bounds:
        subject = str(bound.get('subject') or '') if isinstance(bound, dict) else ''
        override = subject_overrides.get(subject) or {}
        if not isinstance(bound, dict) or not (
                float(bound.get('x_min') or 0) >= float(
                    override.get('x_min', safe_frame.get('x_min', .08)))
                and float(bound.get('x_max') or 1) <= float(
                    override.get('x_max', safe_frame.get('x_max', .92)))
                and float(bound.get('y_min') or 0) >= float(
                    override.get('y_min', safe_frame.get('y_min', .08)))
                and float(bound.get('y_max') or 1) <= float(
                    override.get('y_max', safe_frame.get('y_max', .94)))
                and float(bound.get('x_min') or 0) < float(bound.get('x_max') or 0)
                and float(bound.get('y_min') or 0) < float(bound.get('y_max') or 0)):
            bounds_valid = False
            break
    if not bounds_valid:
        raise _reference_slot_camera_error(requested_slot_id, 'must_show 安全画框证据缺失或越界')


def _qa_feedback(evaluation: dict, error: str = '') -> str:
    rows = []
    for check in evaluation.get('checks') or []:
        if check.get('status') != 'pass':
            rows.append(f"{check.get('constraint_id') or ''} {check.get('constraint')}: {check.get('evidence') or check.get('status')}")
    if error:
        rows.append(error)
    return '; '.join(rows)[:3000] or str(evaluation.get('summary') or 'Previous attempt did not pass the gate')[:3000]


def _record_local_gate(run: dict, result: dict, attempt_row: dict, gate: dict, *,
                       phase: str, material_row: Optional[dict] = None) -> None:
    now = time.time()
    run.setdefault('call_ledger', []).append({
        'call_id': new_id('gate'), 'kind': 'local_gate', 'phase': phase,
        'result_id': result.get('result_id'), 'attempt_id': attempt_row.get('attempt_id'),
        'material_attempt_id': (material_row or {}).get('material_attempt_id') or '',
        'started_at': now, 'finished_at': now, 'seconds': 0,
        'status': 'passed' if gate.get('gate_pass') else 'failed',
        'provider': 'local-opencv', 'model_id': gate.get('version') or '',
        'overlay_path': gate.get('overlay_path') or '',
        'overlay_sha256': _sha256_file(str(gate.get('overlay_path') or '')),
        'local_gate': copy.deepcopy(gate), 'error': '' if gate.get('gate_pass') else gate.get('summary') or '',
    })


async def _evaluate_with_retries(api_key: str, project: dict, capture: dict,
                                 result_path: str, floor_path: str, *, phase: str,
                                 structure_path: str = '', material_path: str = '',
                                 run: Optional[dict] = None, result: Optional[dict] = None,
                                 attempt_row: Optional[dict] = None,
                                 attempts: int = 2) -> tuple[dict, Optional[str], list[dict]]:
    """Serialize QA calls; two unavailable responses are a fail-closed gate."""
    history: list[dict] = []
    evaluation: dict = {
        'status': 'unavailable', 'phase': phase, 'hard_fail': True,
        'verification_incomplete': True, 'gate_pass': False,
        'eligible_for_recommendation': False, 'total': None,
        'summary': 'QA 尚未运行', 'checks': [],
    }
    last_error: Optional[str] = None
    for attempt in range(1, max(1, attempts) + 1):
        _assert_manual_call_cap(run, 'qa')
        call_id = new_id('call')
        reserved_at = time.time()
        ledger_row = {
            'call_id': call_id, 'kind': 'qa', 'phase': phase,
            'result_id': (result or {}).get('result_id'),
            'attempt_id': (attempt_row or {}).get('attempt_id'),
            'retry_index': attempt, 'reserved_at': reserved_at,
            'started_at': None, 'finished_at': None, 'seconds': 0,
            'status': 'pending', 'provider': 'google', 'model_id': '', 'error': '',
        }
        reservation_id = ''
        started = time.time()
        try:
            async with _QA_SEMAPHORE:
                reservation_id = (
                    _reserve_development_call(run, ledger_row)
                    if run is not None else '')
                _dispatch_development_call(run, ledger_row, reservation_id) if run is not None else None
                started = time.time()
                if reservation_id:
                    ledger_row['started_at'] = started
                    _persist_run(run)
                evaluation, last_error = await asyncio.to_thread(
                    evaluate_whole_home_phase, api_key, project, capture, result_path, floor_path,
                    phase=phase, structure_path=structure_path, material_path=material_path)
        except DevelopmentAutopilotError:
            raise
        except BaseException as ex:
            if reservation_id and run is not None:
                _finish_development_call(
                    run, ledger_row, reservation_id, success=False, error=str(ex))
                ledger_row.update(
                    finished_at=time.time(), seconds=round(time.time() - started, 1),
                    error=str(ex),
                )
                _persist_run(run)
            raise
        row = {
            'attempt': attempt, 'at': time.time(),
            'seconds': round(time.time() - started, 1),
            'status': evaluation.get('status') or 'unavailable', 'error': last_error or '',
        }
        history.append(row)
        if run is not None:
            finished = time.time()
            logical_success = evaluation.get('status') == 'done'
            if reservation_id:
                _finish_development_call(
                    run, ledger_row, reservation_id,
                    success=logical_success, error=last_error or '')
            else:
                run.setdefault('call_ledger', []).append(ledger_row)
            ledger_row.update(
                started_at=started, finished_at=finished,
                seconds=row['seconds'], status=row['status'],
                model_id=evaluation.get('evaluator_model') or '',
                error=last_error or '',
            )
            _persist_run(run)
        if evaluation.get('status') == 'done':
            break
        if attempt < attempts:
            await asyncio.sleep(2.0 * attempt)
    if evaluation.get('status') != 'done':
        evaluation.update(
            hard_fail=True, verification_incomplete=True, gate_pass=False,
            eligible_for_recommendation=False,
        )
    return evaluation, last_error, history


async def _analyze_project(project: dict, api_key: str) -> None:
    try:
        project.update(status='analyzing', stage='Gemini 正在提取整屋共墙、门窗和固定物', error='')
        _persist_project(project)
        model, error, ai_model = await asyncio.to_thread(
            analyze_whole_home, api_key, project['floorplan_path'])
        if error or not model:
            project.update(status='failed', stage='', error=error or '整屋识别返回为空')
        else:
            model, geometry_source_project_id = prefer_historical_geometry(
                model, list_projects(100), project['floorplan_path'])
            project['stage'] = 'Gemini 正在补全各房间语义灰模并执行本地校验'
            _persist_project(project)
            semantic_model, semantic_error, semantic_ai_model = await asyncio.to_thread(
                analyze_semantic_layout, api_key, project['floorplan_path'], model)
            model = semantic_model
            semantic_report = model.get('semantic_report') or validate_semantic_layout(model)
            semantic_ready = not semantic_report.get('hard_errors')
            project.update(
                status='done',
                stage=('整屋语义灰模草稿已生成，请校准并复核' if semantic_ready
                       else '整屋外壳已生成，语义布局仍需补全后才能锁定'),
                error='', model=model,
                ai_model=ai_model, summary=f"识别到 {len(model['walls'])} 段墙、{len(model['rooms'])} 个房间、{len(model['openings'])} 个门窗",
                revision=1, verified=False, verified_revision=0,
                geometry_source_project_id=geometry_source_project_id,
                semantic_ai_model=semantic_ai_model,
                semantic_error=semantic_error or '',
            )
    except Exception as ex:
        logger.exception('[整屋建模] AI 识别异常')
        project.update(status='failed', stage='', error=str(ex))
    finally:
        _persist_project(project)
        _ACTIVE_PROJECTS.pop(project['project_id'], None)


@router.post('/api/whole-home/projects')
async def create_whole_home_project(req: WholeHomeProjectRequest):
    project_id = new_id('home')
    if req.cad_path:
        reference_contract = resolve_reference_assets(
            reference_contract_for_url(req.reference_url), require_all=False)
        project = {
            'project_id': project_id, 'source_type': 'cad', 'status': 'parsing_cad',
            'stage': '本地转换并解析 CAD；此阶段不会调用 Gemini', 'error': '',
            'summary': '', 'created_at': time.time(), 'updated_at': time.time(),
            'floorplan_path': '', 'source_analysis_id': '', 'cad_path': req.cad_path,
            'reference_url': req.reference_url, 'reference_contract': reference_contract,
            'model': {}, 'revision': 0, 'verified': False, 'verified_revision': 0,
            'captures': [], 'operations': [], 'ai_model': '',
            'prompt_version': 'whole-home-cad-v1-local-only',
            'geometry_schema_version': 3, 'geometry_acceptance_required': True,
            'input_grade': 'vector_authoritative',
        }
        _persist_project(project)
        try:
            cad_path = require_managed_cad_path(req.cad_path)
            model, parse_report, preview_path = await asyncio.to_thread(
                ingest_cad, cad_path, project_id)
            facts_hash = cad_facts_hash(model)
            alignment_metrics = (
                parse_report.get('alignment_metrics')
                if isinstance(parse_report.get('alignment_metrics'), dict) else {}
            )
            unresolved_wall_count = int(
                alignment_metrics.get('production_unresolved_wall_assembly_count')
                or alignment_metrics.get('unresolved_wall_assembly_count') or 0)
            cad_review_required = unresolved_wall_count > 0
            project.update(
                status='needs_review' if cad_review_required else 'done',
                stage=(
                    f'CAD 已生成可检查的 3D 草稿；仍有 {unresolved_wall_count} 个墙体证据待解决'
                    if cad_review_required else 'CAD 本地硬门禁已通过，请复核并锁定'
                ),
                error='',
                summary=(f"CAD 主平面已建立：{len(model.get('walls') or [])} 段可追溯墙线、"
                         f"{len(model.get('rooms') or [])} 个闭合区域"),
                floorplan_path=preview_path, cad_path=cad_path, model=model,
                parse_report=cad_report_summary(parse_report),
                revision=1, verified=False, verified_revision=0,
                cad_source={
                    'path': cad_path, 'sha256': parse_report.get('source', {}).get('sha256') or _sha256_file(cad_path),
                    'format': parse_report.get('source', {}).get('format') or '',
                    'version': parse_report.get('source', {}).get('version') or '',
                    'converted_dxf_path': (parse_report.get('conversion') or {}).get('output_path') or '',
                },
                cad_import={
                    'schema_version': 1, 'cad_facts_hash': facts_hash,
                    'cad_to_model': copy.deepcopy(parse_report.get('cad_to_model') or {}),
                    'model_to_cad': copy.deepcopy(parse_report.get('model_to_cad') or {}),
                    'provenance_required': True, 'derivation_coverage_required': 1.0,
                },
            )
            try:
                source_hash = str(
                    (parse_report.get('source') or {}).get('sha256')
                    or (project.get('cad_source') or {}).get('sha256') or '')
                registration = build_cad_source_registration(
                    source_hash=source_hash, parse_report=parse_report, model=model)
                project['source_registration'] = registration
                project['registration_hash'] = registration['registration_hash']
                project['model']['source_registration'] = copy.deepcopy(registration)
                project['model']['input_grade'] = 'vector_authoritative'
                project['model']['geometry_schema_version'] = 3
            except (GeometryContractError, ValueError) as ex:
                project['source_registration_error'] = {
                    'code': getattr(ex, 'code', 'cad_registration_not_supported'),
                    'message': str(ex),
                }
            project.setdefault('operations', []).append({
                'type': ('cad_import_local_needs_review'
                         if cad_review_required else 'cad_import_local'),
                'payload': {'cad_facts_hash': facts_hash,
                            'reference_contract_id': reference_contract.get('contract_id') or '',
                            'production_unresolved_wall_assembly_count': unresolved_wall_count},
                'at': time.time(), 'revision': 1, 'actor': 'local-cad-parser',
            })
        except CadError as ex:
            details = copy.deepcopy(ex.details or {})
            blocked_report = details.get('parse_report') or {}
            blocked_summary = cad_report_summary(blocked_report) if blocked_report else {}
            draft_model = details.get('model') if isinstance(details.get('model'), dict) else {}
            # A fail-closed geometry gate is not the same thing as an import
            # crash.  When the parser produced a provenance-backed draft, put
            # that exact draft in the ordinary project view so the person who
            # uploaded the drawing can inspect and repair it in the 2D/3D UI.
            # Production remains blocked by the retained hard errors and the
            # geometry-acceptance contract.
            if draft_model and blocked_report:
                facts_hash = cad_facts_hash(draft_model)
                draft_model['cad_facts_hash'] = facts_hash
                draft_model.setdefault('input_grade', 'vector_authoritative')
                draft_model.setdefault('geometry_schema_version', 3)
                source = blocked_report.get('source') or {}
                source_hash = str(source.get('sha256') or _sha256_file(cad_path))
                project.update(
                    status='needs_review',
                    stage='CAD 已生成可检查的 3D 草稿；硬门禁项必须修正后才能锁定或生图',
                    error='', model=draft_model, revision=1,
                    cad_error={
                        'code': ex.code, 'message': ex.message,
                        'report_path': blocked_summary.get('report_path') or '',
                        'report_sha256': blocked_summary.get('report_sha256') or '',
                    }, parse_report=blocked_summary,
                    floorplan_path=(blocked_report.get('semantic_preview_path')
                                    or blocked_report.get('preview_path') or ''),
                    cad_source={
                        'path': cad_path, 'sha256': source_hash,
                        'format': source.get('format') or '',
                        'version': source.get('version') or '',
                        'converted_dxf_path': (
                            (blocked_report.get('conversion') or {}).get('output_path') or ''),
                    },
                    cad_import={
                        'schema_version': 2, 'cad_facts_hash': facts_hash,
                        'physical_facts_hash': draft_model.get('physical_facts_hash') or '',
                        'semantic_overlay_hash': draft_model.get('semantic_overlay_hash') or '',
                        'cad_to_model': copy.deepcopy(blocked_report.get('cad_to_model') or {}),
                        'model_to_cad': copy.deepcopy(blocked_report.get('model_to_cad') or {}),
                        'provenance_required': True, 'derivation_coverage_required': 1.0,
                    },
                )
                try:
                    registration = build_cad_source_registration(
                        source_hash=source_hash, parse_report=blocked_report,
                        model=draft_model)
                    project['source_registration'] = registration
                    project['registration_hash'] = registration['registration_hash']
                    project['model']['source_registration'] = copy.deepcopy(registration)
                except (GeometryContractError, ValueError) as registration_error:
                    project['source_registration_error'] = {
                        'code': getattr(
                            registration_error, 'code', 'cad_registration_not_supported'),
                        'message': str(registration_error),
                    }
                project['cad_space_draft_pointer'] = save_cad_draft_model(
                    project_id, draft_model, blocked_report.get('artifact_directory') or '')
                project['cad_candidate_model_summary'] = cad_space_model_summary(draft_model)
                project.setdefault('operations', []).append({
                    'type': 'cad_import_needs_review',
                    'payload': {
                        'code': ex.code,
                        'hard_error_codes': sorted({
                            str(row.get('code') or '')
                            for row in blocked_report.get('hard_errors') or []
                            if row.get('code')
                        }),
                        'cad_facts_hash': facts_hash,
                    },
                    'at': time.time(), 'revision': 1, 'actor': 'local-cad-parser',
                })
            else:
                project.update(
                    status='failed', stage='', error=ex.message,
                    cad_error={
                        'code': ex.code, 'message': ex.message,
                        'report_path': blocked_summary.get('report_path') or '',
                        'report_sha256': blocked_summary.get('report_sha256') or '',
                    }, parse_report=blocked_summary,
                    floorplan_path=blocked_report.get('semantic_preview_path') or '',
                )
                project.setdefault('operations', []).append({
                    'type': 'cad_import_failed', 'payload': {'code': ex.code},
                    'at': time.time(), 'revision': 0, 'actor': 'local-cad-parser',
                })
        except Exception as ex:
            logger.exception('[CAD建模] 本地导入异常')
            project.update(status='failed', stage='', error=f'CAD 本地解析异常: {ex}',
                           cad_error={'code': 'cad_unhandled_error', 'message': str(ex)})
        _persist_project(project)
        return _whole_home_project_view(project)
    if req.import_analysis_id:
        analysis = load_analysis(req.import_analysis_id)
        if not analysis:
            raise HTTPException(404, '旧户型标注不存在')
        source_path = analysis.get('floorplan_path') or (analysis.get('source') or {}).get('path')
        if not source_path or not os.path.isfile(source_path):
            raise HTTPException(400, '旧户型原图不存在')
        model = legacy_model_from_analysis(analysis, width_m=req.width_m or 12.0)
        project = {
            'project_id': project_id, 'source_type': 'import', 'status': 'done', 'stage': '旧标注已导入整屋模型，请复核共墙与门窗',
            'error': '', 'summary': f"从 {req.import_analysis_id} 导入", 'created_at': time.time(),
            'updated_at': time.time(), 'floorplan_path': source_path, 'source_analysis_id': req.import_analysis_id,
            'model': model, 'revision': 1, 'verified': False, 'verified_revision': 0,
            'captures': [], 'operations': [{'type': 'import_legacy_analysis', 'at': time.time()}],
            'ai_model': '', 'prompt_version': 'whole-home-v2-import',
            'geometry_schema_version': 3, 'geometry_acceptance_required': True,
            'input_grade': 'raster_draft',
        }
        _persist_project(project)
        return _whole_home_project_view(project)

    floorplan_path = require_upload_image_path(req.floorplan_path, '户型图', required=True)
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    project = {
        'project_id': project_id, 'source_type': 'floorplan', 'status': 'queued', 'stage': '等待整屋识别', 'error': '',
        'summary': '', 'created_at': time.time(), 'updated_at': time.time(),
        'floorplan_path': floorplan_path, 'source_analysis_id': '', 'model': {},
        'revision': 0, 'verified': False, 'verified_revision': 0, 'captures': [], 'operations': [],
        'ai_model': '', 'prompt_version': 'whole-home-v2',
        'geometry_schema_version': 3, 'geometry_acceptance_required': True,
        'input_grade': 'raster_draft',
    }
    _ACTIVE_PROJECTS[project_id] = project
    _persist_project(project)
    state.spawn(_analyze_project(project, api_key))
    return _whole_home_project_view(project)


@router.get('/api/whole-home/projects')
def get_whole_home_projects(limit: int = 30):
    rows = {entry['project_id']: entry for entry in list_projects(limit)}
    rows.update(_ACTIVE_PROJECTS)
    ordered = sorted(rows.values(), key=lambda item: item.get('updated_at', 0), reverse=True)
    return [_whole_home_project_view(item, list_mode=True)
            for item in ordered[:max(1, min(limit, 100))]]


@router.get('/api/whole-home/cad/status')
def get_whole_home_cad_status():
    return cad_runtime_status()


@router.get('/api/whole-home/professional/capabilities')
def get_whole_home_professional_capabilities():
    """Publish the deliberately narrow renovation-sales product contract."""
    return professional_capabilities()


@router.get('/api/whole-home/projects/{project_id}/floorplan-graph')
def get_whole_home_floorplan_graph(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    try:
        return build_floorplan_graph(project)
    except ProfessionalContractError as ex:
        _raise_professional_error(ex)


@router.get('/api/whole-home/projects/{project_id}/construction-profile')
def get_whole_home_construction_profile(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    profile = project.get('construction_profile')
    return copy.deepcopy(profile) if isinstance(profile, dict) else default_construction_profile(project)


@router.put('/api/whole-home/projects/{project_id}/construction-profile')
def confirm_whole_home_construction_profile(
        project_id: str, req: WholeHomeConstructionProfileRequest):
    project = _geometry_mutation_project(project_id, req.base_revision, req.base_state_hash)
    fingerprint = canonical_hash(req.model_dump(exclude={'operation_id'}))
    if _professional_operation_guard(project, req.operation_id, fingerprint):
        return _whole_home_project_view(project)
    try:
        profile = confirm_construction_profile(
            project, req.values, reviewer=req.reviewer)
    except ProfessionalContractError as ex:
        _raise_professional_error(ex)
    old_hash = str((project.get('construction_profile') or {}).get('profile_hash') or '')
    if old_hash and old_hash != profile['profile_hash']:
        for recipe in project.get('scene_recipes') or []:
            if recipe.get('status') != 'superseded':
                recipe['status'] = 'superseded'
                recipe['superseded_reason'] = 'construction_profile_changed'
        project['active_scene_recipe_id'] = ''
    project['construction_profile'] = profile
    project['professional_revision'] = int(project.get('professional_revision') or 0) + 1
    project['updated_at'] = time.time()
    project.setdefault('operations', []).append({
        'type': 'confirm_construction_profile',
        'payload': {
            'operation_id': req.operation_id,
            'request_fingerprint': fingerprint,
            'profile_hash': profile['profile_hash'],
            'professional_revision': project['professional_revision'],
        },
        'at': time.time(), 'revision': int(project.get('revision') or 0),
        'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.get('/api/whole-home/projects/{project_id}/scene-recipes')
def get_whole_home_scene_recipes(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    return {
        'project_id': project_id,
        'professional_revision': int(project.get('professional_revision') or 0),
        'active_scene_recipe_id': str(project.get('active_scene_recipe_id') or ''),
        'recipes': copy.deepcopy(project.get('scene_recipes') or []),
    }


@router.post('/api/whole-home/projects/{project_id}/scene-recipes/preview')
def preview_whole_home_scene_recipe(
        project_id: str, req: WholeHomeSceneRecipePreviewRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    profile = project.get('construction_profile')
    if not isinstance(profile, dict):
        profile = default_construction_profile(project)
    try:
        return generate_scene_recipe(project, profile, variant_index=req.variant_index)
    except ProfessionalContractError as ex:
        _raise_professional_error(ex)


@router.post('/api/whole-home/projects/{project_id}/scene-recipes')
def create_whole_home_scene_recipe(
        project_id: str, req: WholeHomeSceneRecipeCommitRequest):
    project = _geometry_mutation_project(project_id, req.base_revision, req.base_state_hash)
    fingerprint = canonical_hash(req.model_dump(exclude={'operation_id'}))
    if _professional_operation_guard(project, req.operation_id, fingerprint):
        return _whole_home_project_view(project)
    profile = project.get('construction_profile')
    if not isinstance(profile, dict) or profile.get('status') != 'confirmed':
        raise HTTPException(409, {
            'code': 'construction_profile_not_confirmed',
            'message': '保存正式方案前必须逐项确认层高、门高、窗台和窗顶假设',
        })
    try:
        recipe = generate_scene_recipe(project, profile, variant_index=req.variant_index)
    except ProfessionalContractError as ex:
        _raise_professional_error(ex)
    recipes = project.setdefault('scene_recipes', [])
    recipes.append(recipe)
    project['scene_recipes'] = recipes[-50:]
    project['active_scene_recipe_id'] = recipe['recipe_id']
    project['professional_revision'] = int(project.get('professional_revision') or 0) + 1
    project['updated_at'] = time.time()
    project.setdefault('operations', []).append({
        'type': 'create_scene_recipe',
        'payload': {
            'operation_id': req.operation_id,
            'request_fingerprint': fingerprint,
            'recipe_id': recipe['recipe_id'], 'recipe_hash': recipe['recipe_hash'],
            'scene_hash': recipe['scene_hash'], 'variant_index': req.variant_index,
            'professional_revision': project['professional_revision'],
        },
        'at': time.time(), 'revision': int(project.get('revision') or 0),
        'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.post('/api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}/review')
def review_whole_home_scene_recipe(
        project_id: str, recipe_id: str, req: WholeHomeSceneRecipeReviewRequest):
    project = _geometry_mutation_project(project_id, req.base_revision, req.base_state_hash)
    fingerprint = canonical_hash(req.model_dump(exclude={'operation_id'}))
    if _professional_operation_guard(project, req.operation_id, fingerprint):
        return _whole_home_project_view(project)
    recipes = project.get('scene_recipes') or []
    matches = [row for row in recipes if str(row.get('recipe_id') or '') == recipe_id]
    if len(matches) != 1:
        raise HTTPException(404, {'code': 'scene_recipe_not_found'})
    profile = project.get('construction_profile') or {}
    try:
        reviewed = review_scene_recipe(
            matches[0], reviewer=req.reviewer, note=req.note,
            lock=req.action == 'lock', project_verified=bool(project.get('verified')),
            construction_confirmed=profile.get('status') == 'confirmed')
    except ProfessionalContractError as ex:
        _raise_professional_error(ex)
    for index, row in enumerate(recipes):
        if str(row.get('recipe_id') or '') == recipe_id:
            recipes[index] = reviewed
    project['scene_recipes'] = recipes
    project['active_scene_recipe_id'] = recipe_id
    project['professional_revision'] = int(project.get('professional_revision') or 0) + 1
    project['updated_at'] = time.time()
    project.setdefault('operations', []).append({
        'type': 'lock_scene_recipe' if req.action == 'lock' else 'review_scene_recipe',
        'payload': {
            'operation_id': req.operation_id,
            'request_fingerprint': fingerprint,
            'recipe_id': recipe_id, 'recipe_hash': reviewed['recipe_hash'],
            'scene_hash': reviewed['scene_hash'],
            'professional_revision': project['professional_revision'],
        },
        'at': time.time(), 'revision': int(project.get('revision') or 0),
        'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.get('/api/whole-home/projects/{project_id}/marketing-proposal')
def get_whole_home_marketing_proposal(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    active_id = str(project.get('active_scene_recipe_id') or '')
    recipe = next((
        row for row in project.get('scene_recipes') or []
        if str(row.get('recipe_id') or '') == active_id
    ), None)
    return build_marketing_proposal(project, recipe)


@router.get('/api/whole-home/projects/{project_id}')
def get_whole_home_project(project_id: str):
    stored = load_project(project_id)
    project = stored if stored and stored.get('source_type') == 'cad' else _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    return _whole_home_project_view(project)


@router.get('/api/whole-home/projects/{project_id}/history')
def get_whole_home_project_history(project_id: str, limit: int = 100,
                                   cursor: str = ''):
    """Project-family timeline assembled from existing immutable facts."""
    try:
        return build_whole_home_history(
            project_id, list_projects(10_000), list_learning_runs(),
            list_variant_batches(), limit=limit, cursor=cursor)
    except WholeHomeHistoryError as ex:
        _raise_history_error(ex)


@router.get('/api/whole-home/projects/{project_id}/generation-draft')
def get_whole_home_generation_draft(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    draft = copy.deepcopy(project.get('generation_draft') or {
        'draft_version': 0, 'source_run_id': '', 'variant_label': '',
        'style': '现代自然', 'lighting': '自然日光', 'prompt': '',
        'material_mode': 'floor_sample', 'floor_path': '', 'style_ref_path': '',
        'model_keys': [], 'selected_artifact_ids': [], 'aspect_ratio': '4:3',
        'resolution': '2K', 'updated_at': 0, 'last_committed_batch_id': '',
    })
    draft['floor_url'] = to_url(draft.get('floor_path'))
    draft['style_ref_url'] = to_url(draft.get('style_ref_path'))
    return draft


@router.put('/api/whole-home/projects/{project_id}/generation-draft')
def save_whole_home_generation_draft(project_id: str,
                                     req: WholeHomeGenerationDraftRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    current = project.get('generation_draft') if isinstance(
        project.get('generation_draft'), dict) else {}
    current_version = int(current.get('draft_version') or 0)
    if req.expected_draft_version not in (0, current_version):
        raise HTTPException(409, {
            'code': 'generation_draft_conflict',
            'message': '生成草稿已在其他窗口更新',
            'current_draft': copy.deepcopy(current),
        })
    draft = req.model_dump()
    draft.pop('expected_draft_version', None)
    draft.update(
        draft_version=current_version + 1, updated_at=time.time(),
        last_committed_batch_id=str(current.get('last_committed_batch_id') or ''))
    project['generation_draft'] = draft
    _persist_project(project)
    response = copy.deepcopy(draft)
    response['floor_url'] = to_url(response.get('floor_path'))
    response['style_ref_url'] = to_url(response.get('style_ref_path'))
    return response


def _geometry_mutation_project(project_id: str, base_revision: int,
                               base_state_hash: str = '') -> dict:
    project = load_project(project_id) or _ACTIVE_PROJECTS.get(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if int(project.get('revision') or 0) != int(base_revision):
        raise HTTPException(409, {
            'code': 'geometry_revision_conflict',
            'message': '图纸/模型已更新，请刷新后重试',
            'current_revision': int(project.get('revision') or 0),
        })
    actual_hash = state_hash(project)
    if base_state_hash and base_state_hash != actual_hash:
        raise HTTPException(409, {
            'code': 'geometry_state_conflict', 'message': '项目状态已改变，请刷新后重试',
            'expected_state_hash': base_state_hash, 'current_state_hash': actual_hash,
        })
    return copy.deepcopy(project)


def _invalidate_geometry_lock(project: dict, reason: str) -> None:
    report = project.get('geometry_acceptance')
    if isinstance(report, dict) and report:
        project.setdefault('geometry_acceptance_history', []).append({
            'report_id': report.get('report_id') or '',
            'report_hash': report.get('report_hash') or '',
            'status': 'stale', 'reason': reason, 'invalidated_at': time.time(),
        })
    project.pop('geometry_acceptance', None)
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    model.pop('geometry_manifest', None)
    model.pop('model_facts_hash', None)
    project['verified'] = False
    project['verified_revision'] = 0


def _raise_professional_error(ex: ProfessionalContractError) -> None:
    raise HTTPException(409, {
        'code': ex.code, 'message': ex.message, **copy.deepcopy(ex.details),
    }) from ex


def _professional_operation(project: dict, operation_id: str) -> Optional[dict]:
    return next((
        row for row in reversed(project.get('operations') or [])
        if isinstance(row, dict)
        and str((row.get('payload') or {}).get('operation_id') or '') == operation_id
    ), None)


def _professional_operation_guard(project: dict, operation_id: str,
                                  fingerprint: str) -> bool:
    """Return True for an exact retry and reject operation-id body reuse."""
    existing = _professional_operation(project, operation_id)
    if not existing:
        return False
    previous = str((existing.get('payload') or {}).get('request_fingerprint') or '')
    if previous != fingerprint:
        raise HTTPException(409, {
            'code': 'professional_operation_id_conflict',
            'message': 'operation_id 已用于不同的专业方案请求',
        })
    return True


def _professional_summary(project: dict) -> dict:
    recipes = project.get('scene_recipes') or []
    active_id = str(project.get('active_scene_recipe_id') or '')
    active = next((row for row in recipes if str(row.get('recipe_id') or '') == active_id), {})
    return {
        'product_mode': 'raster_first_renovation_sales_proposal',
        'professional_revision': int(project.get('professional_revision') or 0),
        'construction_profile_status': str(
            (project.get('construction_profile') or {}).get('status') or 'assumptions_pending'),
        'scene_recipe_count': len(recipes),
        'active_scene_recipe_id': active_id,
        'active_scene_status': str(active.get('status') or ''),
        'marketing_proposal_status': str(
            build_marketing_proposal(project, active).get('status') if active else 'draft'),
    }


@router.put('/api/whole-home/projects/{project_id}/source-registration')
def save_whole_home_source_registration(
        project_id: str, req: WholeHomeSourceRegistrationRequest):
    project = _geometry_mutation_project(
        project_id, req.base_revision, req.base_state_hash)
    try:
        registration = validate_source_registration(req.registration)
    except GeometryContractError as ex:
        raise HTTPException(400, ex.to_dict()) from ex
    expected_source_type = 'cad' if project.get('source_type') == 'cad' else 'raster'
    if registration.get('source_type') != expected_source_type:
        raise HTTPException(409, {
            'code': 'registration_source_type_mismatch',
            'expected': expected_source_type, 'actual': registration.get('source_type'),
        })
    if expected_source_type == 'cad':
        source_hash = str(
            (project.get('cad_source') or {}).get('sha256')
            or (project.get('parse_report') or {}).get('source_sha256') or '')
        try:
            full_report = _cad_report(project)
            authoritative = build_cad_source_registration(
                source_hash=source_hash,
                parse_report=full_report,
                model=project.get('model') or {},
            )
        except (CadError, GeometryContractError, ValueError) as ex:
            raise HTTPException(409, {
                'code': getattr(ex, 'code', 'cad_registration_not_supported'),
                'message': str(ex),
            }) from ex
        if registration.get('registration_hash') != authoritative.get('registration_hash'):
            raise HTTPException(409, {
                'code': 'cad_registration_authority_mismatch',
                'message': 'CAD 配准必须由 $INSUNITS 和解析器选定平面变换生成，不能由客户端改写',
                'expected_registration_hash': authoritative.get('registration_hash'),
            })
        registration = authoritative
    else:
        source_hash = _sha256_file(str(project.get('floorplan_path') or ''))
        if not source_hash or registration.get('source_hash') != source_hash:
            raise HTTPException(409, {
                'code': 'raster_registration_source_hash_mismatch',
                'message': '配准记录并非当前户型图生成',
                'expected_source_hash': source_hash,
            })
    _invalidate_geometry_lock(project, 'source_registration_changed')
    revision = req.base_revision + 1
    project.update(
        revision=revision, geometry_schema_version=3,
        geometry_acceptance_required=True,
        input_grade=registration['input_grade'],
        source_registration=registration,
        registration_hash=registration['registration_hash'],
        status='done', stage='图纸坐标已配准，请运行 2D→3D 对应验收',
    )
    project.setdefault('model', {})['source_registration'] = copy.deepcopy(registration)
    project['model']['input_grade'] = registration['input_grade']
    project['model']['geometry_schema_version'] = 3
    project.setdefault('operations', []).append({
        'type': 'save_source_registration',
        'payload': {'registration_hash': registration['registration_hash'],
                    'operation_id': req.operation_id},
        'at': time.time(), 'revision': revision, 'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


def _raster_wall_alignment_metrics(model: dict, registration: dict, mask_path: str) -> dict:
    """Back-project model wall axes into canonical pixels and measure ink support."""
    matrix = registration.get('model_to_canonical') or []
    if not (isinstance(matrix, list) and len(matrix) == 3):
        raise RasterRegistrationError('model_to_canonical is missing')

    def point(value: object) -> Optional[list[float]]:
        try:
            if isinstance(value, dict):
                x, z = float(value.get('x')), float(value.get('z'))
            else:
                x, z = float(value[0]), float(value[1])  # type: ignore[index]
            denominator = matrix[2][0] * x + matrix[2][1] * z + matrix[2][2]
            if abs(denominator) <= 1e-12:
                return None
            return [
                (matrix[0][0] * x + matrix[0][1] * z + matrix[0][2]) / denominator,
                (matrix[1][0] * x + matrix[1][1] * z + matrix[1][2]) / denominator,
            ]
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    segments = []
    assemblies = [
        row for row in model.get('wall_assemblies') or []
        if isinstance(row, dict) and row.get('review_status') in {'accepted', 'confirmed'}
    ]
    if assemblies:
        for row in assemblies:
            centerline = row.get('centerline') or []
            if not isinstance(centerline, list) or len(centerline) < 2:
                continue
            start, end = point(centerline[0]), point(centerline[-1])
            if start and end:
                segments.append({'id': str(row.get('id') or ''),
                                 'start_px': start, 'end_px': end})
    else:
        for row in model.get('walls') or []:
            if not isinstance(row, dict):
                continue
            start, end = point(row.get('start') or {}), point(row.get('end') or {})
            if start and end:
                segments.append({'id': str(row.get('id') or ''),
                                 'start_px': start, 'end_px': end})
    if not segments:
        raise RasterRegistrationError('model contains no wall axes for raster alignment')
    support = wall_ink_support(mask_path, segments)
    p95_px = support.get('distance_p95_px')
    if p95_px is None:
        raise RasterRegistrationError('raster wall alignment produced no samples')
    return {
        'wall_axis_count': len(segments),
        'wall_sample_count': int(support.get('sample_count') or 0),
        'wall_ink_support_ratio': float(support.get('support_ratio') or 0),
        'wall_centerline_p95_px': float(p95_px),
        'wall_centerline_p95_m': round(
            float(p95_px) * float(registration.get('uniform_scale') or 0), 9),
    }


@router.post('/api/whole-home/projects/{project_id}/source-registration/raster')
def prepare_whole_home_raster_registration(
        project_id: str, req: WholeHomeRasterRegistrationPrepareRequest):
    """Server-authoritative raster hashing, scale lock and wall-ink measurement."""
    project = _geometry_mutation_project(
        project_id, req.base_revision, req.base_state_hash)
    if project.get('source_type') == 'cad':
        raise HTTPException(409, {
            'code': 'raster_registration_not_cad',
            'message': 'CAD 项目的尺度只能来自 $INSUNITS',
        })
    source_path = str(project.get('floorplan_path') or '')
    if not source_path or not os.path.isfile(source_path):
        raise HTTPException(409, {
            'code': 'raster_source_missing', 'message': '当前户型原图不存在',
        })
    safe_project = ''.join(
        character if character.isalnum() or character in '-_' else '_'
        for character in str(project_id))[:100] or 'project'
    output_dir = os.path.join(
        MAIN_OUTPUT_DIR, '_whole_home', 'registration', safe_project,
        f"rev_{req.base_revision}_{req.operation_id}",
    )
    try:
        draft = prepare_raster_source(source_path, output_dir)
        registration = lock_raster_scale(
            draft, req.scale_anchors, reviewer=req.reviewer,
            origin_px=req.origin_px,
        )
        evidence = build_structure_evidence(
            registration['canonical_artifact_path'], os.path.join(output_dir, 'evidence'))
        alignment = _raster_wall_alignment_metrics(
            project.get('model') or {}, registration, evidence['mask_path'])
        registration = validate_source_registration(registration)
    except (RasterRegistrationError, GeometryContractError, OSError, ValueError) as ex:
        raise HTTPException(409, {
            'code': getattr(ex, 'code', 'raster_registration_failed'),
            'message': str(ex),
        }) from ex
    if registration.get('source_hash') != _sha256_file(source_path):
        raise HTTPException(409, {
            'code': 'raster_registration_source_hash_mismatch',
            'message': '配准期间原始户型图发生变化',
        })
    _invalidate_geometry_lock(project, 'source_registration_changed')
    revision = req.base_revision + 1
    project.update(
        revision=revision, geometry_schema_version=3,
        geometry_acceptance_required=True, input_grade='raster_human_locked',
        source_registration=registration,
        registration_hash=registration['registration_hash'],
        raster_alignment_metrics=alignment,
        raster_evidence={
            'evidence_hash': evidence['evidence_hash'],
            'canonical_hash': evidence['canonical_hash'],
            'mask_hash': evidence['mask_hash'],
            'ink_fraction': evidence['ink_fraction'],
        },
        status='done', stage='普通户型图尺度已锁定，请逐项复核房间和门窗',
    )
    project.setdefault('model', {})['source_registration'] = copy.deepcopy(registration)
    project['model']['input_grade'] = 'raster_human_locked'
    project['model']['geometry_schema_version'] = 3
    project.setdefault('operations', []).append({
        'type': 'prepare_raster_source_registration',
        'payload': {
            'registration_hash': registration['registration_hash'],
            'evidence_hash': evidence['evidence_hash'],
            'wall_centerline_p95_m': alignment['wall_centerline_p95_m'],
            'operation_id': req.operation_id,
        },
        'at': time.time(), 'revision': revision, 'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.post('/api/whole-home/projects/{project_id}/geometry-acceptance')
def evaluate_whole_home_geometry_acceptance(
        project_id: str, req: WholeHomeGeometryAcceptanceRequest):
    project = _geometry_mutation_project(
        project_id, req.base_revision, req.base_state_hash)
    registration = project.get('source_registration')
    if not isinstance(registration, dict):
        raise HTTPException(409, {
            'code': 'source_registration_missing',
            'message': '请先保存当前图纸到米制 3D 模型的可逆配准',
        })
    unknown_metrics = sorted(set(req.raster_metrics) - set(RASTER_REVIEW_METRICS))
    if unknown_metrics:
        raise HTTPException(400, {
            'code': 'raster_review_metric_unknown', 'fields': unknown_metrics,
        })
    target = copy.deepcopy(project)
    if req.commit:
        # The acceptance commit itself creates the locked geometry revision.
        # Verification below only approves this same revision and does not
        # immediately make its own report stale.
        target['revision'] = req.base_revision + 1
    try:
        manifest, report, metrics = build_project_geometry_acceptance(
            target, raster_review=req.raster_metrics,
            reviewer=req.reviewer, review_note=req.review_note,
            assumptions_confirmed=req.assumptions_confirmed,
        )
    except (GeometryContractError, ValueError) as ex:
        raise HTTPException(409, {
            'code': getattr(ex, 'code', 'geometry_acceptance_failed'),
            'message': str(ex),
            **(getattr(ex, 'details', {}) or {}),
        }) from ex
    response = {
        'committed': False, 'report': report, 'metrics': metrics,
        'manifest_summary': {
            'manifest_hash': manifest['manifest_hash'],
            'model_facts_hash': manifest['model_facts_hash'],
            'geometry_kernel_version': manifest['geometry_kernel_version'],
            'vertex_count': len(manifest.get('vertices') or []),
            'wall_part_count': len(manifest.get('wall_parts') or []),
            'floor_part_count': len(manifest.get('floor_parts') or []),
        },
    }
    if not req.commit:
        return response
    if report.get('status') != 'passed':
        raise HTTPException(409, {
            'code': 'geometry_acceptance_not_passed',
            'message': '对应验收仍有阻断项；报告已返回但未写成生产锁',
            'report': report,
        })
    revision = req.base_revision + 1
    target['geometry_acceptance'] = report
    target['geometry_acceptance_required'] = True
    target['geometry_schema_version'] = 3
    target['verified'] = False
    target['verified_revision'] = 0
    target['status'] = 'geometry_accepted'
    target['stage'] = '图纸与 3D 对应验收通过，请锁定同一模型 revision'
    target.setdefault('model', {})['geometry_manifest'] = manifest
    target['model']['model_facts_hash'] = manifest['model_facts_hash']
    target['model']['geometry_schema_version'] = 3
    target.setdefault('operations', []).append({
        'type': 'commit_geometry_acceptance',
        'payload': {
            'operation_id': req.operation_id, 'report_id': report.get('report_id'),
            'report_hash': report.get('report_hash'), 'manifest_hash': manifest['manifest_hash'],
        },
        'at': time.time(), 'revision': revision, 'actor': req.reviewer,
    })
    _persist_project(target)
    response['committed'] = True
    response['project'] = _whole_home_project_view(target)
    return response


@router.get('/api/whole-home/projects/{project_id}/geometry-acceptance')
def get_whole_home_geometry_acceptance(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    return _geometry_contract_view(project)


@router.get('/api/whole-home/projects/{project_id}/geometry-manifest')
def get_whole_home_geometry_manifest(project_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    manifest = ((project.get('model') or {}).get('geometry_manifest')
                if isinstance(project.get('model'), dict) else None)
    if not isinstance(manifest, dict):
        raise HTTPException(404, {
            'code': 'geometry_manifest_missing',
            'message': '当前 revision 尚未生成正式 GeometryManifest',
        })
    return manifest


@router.post('/api/whole-home/projects/{project_id}/cad/wall-assemblies/{assembly_id}/confirm')
def confirm_whole_home_cad_wall_assembly(
        project_id: str, assembly_id: str,
        req: WholeHomeCadWallAssemblyConfirmRequest):
    project = _geometry_mutation_project(
        project_id, req.base_revision, req.base_state_hash)
    if project.get('source_type') != 'cad':
        raise HTTPException(409, {'code': 'cad_project_required'})
    assemblies = (project.get('model') or {}).get('wall_assemblies') or []
    matches = [row for row in assemblies if str(row.get('id') or '') == assembly_id]
    if len(matches) != 1:
        raise HTTPException(404, {'code': 'cad_wall_assembly_not_found'})
    try:
        confirmed = confirm_ambiguous_assembly(
            matches[0], thickness_m=req.thickness_m,
            reviewer=req.reviewer, reason=req.reason,
            height_m=req.height_m, height_source='human_confirmed_project_height',
        )
    except WallAssemblyError as ex:
        raise HTTPException(409, ex.to_dict()) from ex
    assemblies[assemblies.index(matches[0])] = confirmed
    for wall in (project.get('model') or {}).get('walls') or []:
        if str(wall.get('wall_assembly_id') or '') != assembly_id:
            continue
        wall.update(
            start=copy.deepcopy(confirmed['start']), end=copy.deepcopy(confirmed['end']),
            thickness_m=confirmed['thickness_m'], height_m=confirmed['height_m'],
            boundary_kind='centerline', source='cad', confidence=.9,
            review_status='accepted',
        )
        # The parser may have exposed this source face as an amber review-only
        # trace.  Once a person has supplied an audited centreline thickness it
        # is canonical geometry, so stale display metadata must not keep it in
        # the review material after refresh.
        wall.pop('display_mode', None)
    _invalidate_geometry_lock(project, 'wall_assembly_confirmed')
    revision = req.base_revision + 1
    facts_hash = cad_facts_hash(project.get('model') or {})
    project['model']['cad_facts_hash'] = facts_hash
    project.setdefault('cad_import', {})['cad_facts_hash'] = facts_hash
    remaining_wall_reviews = [
        row for row in assemblies
        if row.get('review_status') not in {'accepted', 'confirmed', 'rejected', 'reject'}
    ]
    semantic_reviews = (
        ((project.get('model') or {}).get('semantic_report') or {}).get('hard_errors')
        or [])
    review_count = len(remaining_wall_reviews) + len(semantic_reviews)
    project.update(
        revision=revision,
        status='done' if review_count == 0 else 'needs_review',
        stage=('墙体表示已人工确认，请重新运行对应验收'
               if review_count == 0
               else f'本条墙体已确认；仍有 {review_count} 项结构/语义证据待复核'),
    )
    project.setdefault('operations', []).append({
        'type': 'confirm_cad_wall_assembly',
        'payload': {'assembly_id': assembly_id, 'operation_id': req.operation_id,
                    'thickness_m': req.thickness_m, 'reason': req.reason},
        'at': time.time(), 'revision': revision, 'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.put('/api/whole-home/projects/{project_id}/cad/opening-annotations')
def save_whole_home_cad_opening_annotations(
        project_id: str, req: WholeHomeCadOpeningAnnotationsRequest):
    project = _geometry_mutation_project(
        project_id, req.base_revision, req.base_state_hash)
    if project.get('source_type') != 'cad':
        raise HTTPException(409, {'code': 'cad_project_required'})
    annotations = []
    for row in req.annotations:
        value = copy.deepcopy(row)
        value.setdefault('reviewer', req.reviewer)
        value.setdefault('reason', '人工在权威 CAD 墙体上标注开口')
        value['base_revision'] = req.base_revision
        value['operation_id'] = req.operation_id
        annotations.append(value)
    model = project.get('model') or {}
    try:
        bound = bind_manual_opening_annotations(
            model.get('wall_assemblies') or [], annotations,
            existing_openings=model.get('openings') or [],
        )
    except WallAssemblyError as ex:
        raise HTTPException(409, ex.to_dict()) from ex
    model.setdefault('openings', []).extend(bound)
    _invalidate_geometry_lock(project, 'cad_opening_annotations_changed')
    revision = req.base_revision + 1
    facts_hash = cad_facts_hash(model)
    model['cad_facts_hash'] = facts_hash
    project.setdefault('cad_import', {})['cad_facts_hash'] = facts_hash
    project.update(revision=revision, status='done', stage='CAD 开口标注已保存，请重新运行对应验收')
    project.setdefault('operations', []).append({
        'type': 'save_cad_opening_annotations',
        'payload': {'operation_id': req.operation_id,
                    'opening_ids': [row['id'] for row in bound]},
        'at': time.time(), 'revision': revision, 'actor': req.reviewer,
    })
    _persist_project(project)
    return _whole_home_project_view(project)


@router.get('/api/whole-home/projects/{project_id}/cad/candidates/{candidate_id}/preview')
def get_whole_home_cad_candidate_preview(project_id: str, candidate_id: str):
    project = load_project(project_id)
    if not project or project.get('source_type') != 'cad':
        raise HTTPException(404, 'CAD 项目不存在')
    try:
        report = _cad_report(project)
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    matches = [row for row in report.get('candidate_plans') or []
               if str(row.get('candidate_id') or '') == str(candidate_id or '')]
    if len(matches) != 1:
        raise HTTPException(404, 'CAD 平面候选不存在')
    path = os.path.realpath(str(matches[0].get('preview_path') or ''))
    artifact_root = os.path.realpath(str(report.get('artifact_directory') or ''))
    # A successful ingest persists its immutable report in an ``ingest_*``
    # directory, while the parser-generated candidate thumbnails remain in the
    # sibling ``parse_*`` directory.  Restricting the response to only the
    # report directory therefore rejected every valid candidate after the
    # report was externalised.  Both directories are owned by the same CAD
    # project, so the correct security boundary is the project artifact root,
    # never the individual ingest run directory.
    project_artifact_root = os.path.realpath(os.path.join(CAD_ROOT, project_id))
    allowed_roots = [root for root in (artifact_root, project_artifact_root) if root]
    try:
        within_project_artifacts = any(
            os.path.commonpath([root, path]) == root for root in allowed_roots)
    except ValueError:
        within_project_artifacts = False
    if (not path or not os.path.isfile(path) or not within_project_artifacts
            or os.path.splitext(path)[1].lower() not in {'.png', '.svg'}):
        raise HTTPException(404, 'CAD 候选预览不存在或路径无效')
    return FileResponse(
        path,
        media_type='image/png' if os.path.splitext(path)[1].lower() == '.png' else 'image/svg+xml',
    )


@router.get('/api/whole-home/projects/{project_id}/cad/report')
def get_whole_home_cad_report(project_id: str):
    project = load_project(project_id)
    if not project or project.get('source_type') != 'cad':
        raise HTTPException(404, 'CAD 项目不存在')
    try:
        report = _cad_report(project)
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    return {
        'schema_version': report.get('schema_version'),
        'source_sha256': report.get('source_sha256') or '',
        'selected_candidate_id': report.get('selected_candidate_id') or '',
        'raw_face_count': int(report.get('raw_face_count') or len(report.get('raw_faces') or [])),
        'text_anchor_count': len(report.get('text_anchors') or []),
        'hard_errors': copy.deepcopy(report.get('hard_errors') or [])[:200],
        'warnings': copy.deepcopy(report.get('warnings') or [])[:200],
        'alignment_metrics': copy.deepcopy(report.get('alignment_metrics') or {}),
    }


@router.get('/api/whole-home/projects/{project_id}/cad/space-draft')
def get_whole_home_cad_space_draft(project_id: str):
    project = load_project(project_id)
    if not project or project.get('source_type') != 'cad':
        raise HTTPException(404, 'CAD 项目不存在')
    try:
        report = _cad_report(project)
        model = copy.deepcopy(project.get('model') or {})
        if not model.get('physical_spaces') and project.get('cad_space_draft_pointer'):
            model = load_cad_draft_model(project['cad_space_draft_pointer'])
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    raw_faces = _cad_space_raw_faces(project, report, model)
    forward = report.get('cad_to_model') or {}
    text_anchors = copy.deepcopy(report.get('text_anchors') or [])
    for anchor in text_anchors:
        if not isinstance(anchor.get('point'), dict) and isinstance(anchor.get('point_m'), list) and len(anchor['point_m']) >= 2:
            model_x, model_z = cad_plan_to_model(anchor['point_m'], forward)
            anchor['point'] = {
                'x': round(model_x, 5),
                'z': round(model_z, 5),
            }
    physical_spaces, semantic_zones = _cad_space_editor_model(model)
    return {
        'project_id': project_id, 'revision': int(project.get('revision') or 0),
        'state_hash': state_hash(project),
        'physical_spaces': physical_spaces,
        'semantic_zones': semantic_zones,
        'excluded_face_ids': copy.deepcopy(model.get('excluded_face_ids') or [
            row.get('face_id') for row in raw_faces if row.get('disposition') == 'excluded']),
        'raw_faces': raw_faces,
        'text_anchors': text_anchors,
        'space_confirmation': copy.deepcopy(model.get('space_confirmation') or {
            'status': 'needs_review', 'reason_codes': ['cad_space_draft_unconfirmed']}),
    }


@router.put('/api/whole-home/projects/{project_id}/cad/space-draft')
def save_whole_home_cad_space_draft(project_id: str, req: WholeHomeCadSpaceDraftRequest):
    current = load_project(project_id)
    if not current or current.get('source_type') != 'cad':
        raise HTTPException(404, 'CAD 项目不存在')
    current_hash = state_hash(current)
    operation_id = req.operation_id or new_id('cadspace')
    existing = next((row for row in current.get('operations') or []
                     if row.get('operation_id') == operation_id), None)
    fingerprint = state_hash(req.model_dump(exclude={'operation_id'}))
    if existing:
        if existing.get('request_fingerprint') != fingerprint:
            raise HTTPException(409, {'code': 'cad_space_idempotency_conflict',
                                      'message': 'operation_id 已用于其他空间修正'})
        return {
            'project_id': project_id, 'revision': current.get('revision'),
            'status': current.get('status'),
            'space_confirmation': _cad_space_confirmation_view(current),
            'model_summary': cad_space_model_summary(current.get('model') or {}),
        }
    if int(current.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'code': 'whole_home_revision_conflict',
                                  'current_revision': current.get('revision', 0),
                                  'current_state_hash': current_hash})
    if req.base_state_hash and req.base_state_hash != current_hash:
        raise HTTPException(409, {'code': 'whole_home_state_conflict',
                                  'current_revision': current.get('revision', 0),
                                  'current_state_hash': current_hash})
    try:
        report = _cad_report(current)
        base_model = copy.deepcopy(current.get('model') or {})
        if not base_model.get('physical_spaces') and current.get('cad_space_draft_pointer'):
            base_model = load_cad_draft_model(current['cad_space_draft_pointer'])
        updated_model, confirmation = apply_space_draft(
            base_model, _cad_space_raw_faces(current, report, base_model),
            [row.model_dump() for row in req.physical_spaces],
            [row.model_dump(exclude_none=True) for row in req.semantic_zones],
            req.excluded_face_ids)
    except (CadError, CadSpaceError) as ex:
        detail = ex.to_dict() if hasattr(ex, 'to_dict') else {
            'code': ex.code, 'message': ex.message, **ex.details}
        raise HTTPException(getattr(ex, 'status_code', 422), detail) from ex
    updated_model['space_model_schema_version'] = 1
    updated_model['physical_facts_hash'] = physical_facts_hash(updated_model)
    updated_model['semantic_overlay_hash'] = semantic_overlay_hash(updated_model)
    updated_model['cad_facts_hash'] = cad_facts_hash(updated_model)

    def commit(project: dict) -> dict:
        if int(project.get('revision') or 0) != req.base_revision:
            raise RuntimeError('whole_home_revision_conflict')
        revision = req.base_revision + 1
        for collection_name in ('captures', 'pano_captures'):
            for capture in project.get(collection_name) or []:
                capture.update(
                    status='stale', stale_reason='cad_space_draft_updated',
                    stale_at_revision=revision,
                )
        project.update(
            model=updated_model, revision=revision, verified=False, verified_revision=0,
            status='done' if confirmation['status'] == 'confirmed' else 'needs_review',
            stage=('CAD 空间修正已保存，请复核门洞后锁定' if confirmation['status'] != 'confirmed'
                   else 'CAD 物理空间和语义分区已人工确认，请锁定'),
            error='', cad_space_draft_pointer={},
            cad_import={
                **copy.deepcopy(project.get('cad_import') or {}),
                'cad_facts_hash': updated_model['cad_facts_hash'],
                'physical_facts_hash': updated_model['physical_facts_hash'],
                'semantic_overlay_hash': updated_model['semantic_overlay_hash'],
            },
        )
        project.setdefault('operations', []).append({
            'operation_id': operation_id, 'type': 'cad_space_draft_saved',
            'request_fingerprint': fingerprint, 'actor': req.editor_id,
            'at': time.time(), 'revision': revision,
            'payload': {'physical_facts_hash': updated_model['physical_facts_hash'],
                        'semantic_overlay_hash': updated_model['semantic_overlay_hash']},
        })
        return project

    try:
        updated, _, _ = cas_update_project(
            project_id, commit, expected_state_hash=current_hash)
    except (RuntimeError, FileNotFoundError) as ex:
        latest = load_project(project_id) or {}
        raise HTTPException(409, {'code': 'whole_home_state_conflict',
                                  'current_revision': latest.get('revision', 0),
                                  'current_state_hash': state_hash(latest) if latest else ''}) from ex
    return {
        'project_id': project_id, 'revision': updated.get('revision'),
        'status': updated.get('status'),
        'space_confirmation': _cad_space_confirmation_view(updated),
        'model_summary': cad_space_model_summary(updated.get('model') or {}),
    }


@router.post('/api/whole-home/projects/{project_id}/cad/reparse')
async def reparse_whole_home_cad(project_id: str, req: WholeHomeCadReparseRequest):
    # Reparse is deliberately queued before expensive DXF work.  Completion
    # re-reads the project and commits through cross-process CAS.
    project = load_project(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if project.get('source_type') != 'cad':
        raise HTTPException(400, '只有 CAD 来源项目可以重新解析 CAD')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型版本冲突',
                                  'current_revision': project.get('revision', 0),
                                  'current_state_hash': state_hash(project)})
    try:
        cad_path = require_managed_cad_path(
            str((project.get('cad_source') or {}).get('path') or project.get('cad_path') or ''))
        operation, created = create_cad_reparse_operation(
            CAD_ROOT, project_id=project_id,
            operation_id=req.operation_id or new_id('cadreparse'),
            base_revision=req.base_revision, base_state_hash=state_hash(project),
            source_path=cad_path, source_sha256=_sha256_file(cad_path),
            candidate_id=req.candidate_id, actor=req.annotator_id)
    except CadReparseOperationError as ex:
        raise HTTPException(ex.status_code, {
            'code': ex.code, 'message': ex.message, **ex.details}) from ex
    if created:
        state.spawn(_run_cad_reparse_operation(operation))
    return JSONResponse(status_code=202, content=public_cad_reparse_operation(operation))


async def _run_cad_reparse_operation(operation: dict) -> None:
    project_id, operation_id = operation['project_id'], operation['operation_id']
    update_cad_reparse_operation(
        CAD_ROOT, project_id, operation_id, status='running', stage='parsing_cad',
        progress=10, started_at=time.time())
    try:
        model, parse_report, preview_path = await asyncio.to_thread(
            ingest_cad, operation['source_path'], project_id,
            preferred_candidate_id=operation.get('candidate_id') or '')
        source_sha = _sha256_file(operation['source_path'])
        if source_sha != operation['source_sha256']:
            raise CadReparseOperationError(
                'cad_reparse_source_changed', 'CAD 源文件在重解析期间发生变化')
        facts_hash = cad_facts_hash(model)
        report_pointer = cad_report_summary(parse_report)
        alignment_metrics = (
            parse_report.get('alignment_metrics')
            if isinstance(parse_report.get('alignment_metrics'), dict) else {}
        )
        unresolved_wall_count = int(
            alignment_metrics.get('production_unresolved_wall_assembly_count')
            or alignment_metrics.get('unresolved_wall_assembly_count') or 0)
        cad_review_required = unresolved_wall_count > 0

        def commit(current: dict) -> dict:
            if (int(current.get('revision') or 0) != int(operation['base_revision'])
                    or state_hash(current) != operation['base_state_hash']):
                raise RuntimeError('cad_reparse_project_conflict')
            revision = int(operation['base_revision']) + 1
            for collection_name in ('captures', 'pano_captures'):
                for capture in current.get(collection_name) or []:
                    capture.update(
                        status='stale', stale_reason='cad_reparsed',
                        stale_at_revision=revision,
                    )
            current.update(
                status='needs_review' if cad_review_required else 'done',
                stage=(
                    f'CAD 已重新解析；仍有 {unresolved_wall_count} 个墙体证据待解决'
                    if cad_review_required else 'CAD 已重新解析，请人工确认物理空间与语义分区'
                ),
                error='', cad_error={}, floorplan_path=preview_path, model=model,
                parse_report=report_pointer, revision=revision,
                verified=False, verified_revision=0,
                cad_import={
                    'schema_version': 2, 'cad_facts_hash': facts_hash,
                    'physical_facts_hash': model.get('physical_facts_hash') or '',
                    'semantic_overlay_hash': model.get('semantic_overlay_hash') or '',
                    'cad_to_model': copy.deepcopy(parse_report.get('cad_to_model') or {}),
                    'model_to_cad': copy.deepcopy(parse_report.get('model_to_cad') or {}),
                    'provenance_required': True, 'derivation_coverage_required': 1.0,
                },
            )
            current.setdefault('operations', []).append({
                'operation_id': operation_id,
                'type': ('cad_reparse_local_needs_review'
                         if cad_review_required else 'cad_reparse_local'),
                'payload': {'cad_facts_hash': facts_hash,
                            'candidate_id': operation.get('candidate_id') or '',
                            'production_unresolved_wall_assembly_count': unresolved_wall_count},
                'at': time.time(), 'revision': revision, 'actor': operation.get('actor') or '',
            })
            return current

        updated, _, after_hash = cas_update_project(
            project_id, commit, expected_state_hash=operation['base_state_hash'])
        update_cad_reparse_operation(
            CAD_ROOT, project_id, operation_id,
            status='needs_review' if cad_review_required else 'done',
            stage='needs_review' if cad_review_required else 'done', progress=100,
            result_revision=updated.get('revision'), result_state_hash=after_hash)
    except (CadError, CadReparseOperationError) as ex:
        report = copy.deepcopy(getattr(ex, 'details', {}).get('parse_report') or {})
        draft_model = copy.deepcopy(getattr(ex, 'details', {}).get('model') or {})
        summary = cad_report_summary(report) if report else {}
        result_revision = None
        result_state = ''
        if draft_model and report:
            pointer = save_cad_draft_model(
                project_id, draft_model, report.get('artifact_directory') or '')

            def retain_editable_draft(current: dict) -> dict:
                if (int(current.get('revision') or 0) != int(operation['base_revision'])
                        or state_hash(current) != operation['base_state_hash']):
                    raise RuntimeError('cad_reparse_project_conflict')
                facts_hash = cad_facts_hash(draft_model)
                draft_model['cad_facts_hash'] = facts_hash
                draft_model.setdefault('input_grade', 'vector_authoritative')
                draft_model.setdefault('geometry_schema_version', 3)
                current.update(
                    status='needs_review', stage='CAD 重解析需要人工修正物理空间/语义分区',
                    error='', model=draft_model, verified=False,
                    verified_revision=0, parse_report=summary,
                    cad_space_draft_pointer=pointer,
                    cad_candidate_model_summary=cad_space_model_summary(draft_model),
                    revision=int(operation['base_revision']) + 1,
                    cad_import={
                        **copy.deepcopy(current.get('cad_import') or {}),
                        'schema_version': 2, 'cad_facts_hash': facts_hash,
                        'physical_facts_hash': draft_model.get('physical_facts_hash') or '',
                        'semantic_overlay_hash': draft_model.get('semantic_overlay_hash') or '',
                        'cad_to_model': copy.deepcopy(report.get('cad_to_model') or {}),
                        'model_to_cad': copy.deepcopy(report.get('model_to_cad') or {}),
                        'provenance_required': True, 'derivation_coverage_required': 1.0,
                    },
                    cad_error={
                        'code': getattr(ex, 'code', 'cad_reparse_failed'),
                        'message': getattr(ex, 'message', str(ex))[:500],
                        'report_path': summary.get('report_path') or '',
                        'report_sha256': summary.get('report_sha256') or '',
                    },
                )
                current.setdefault('operations', []).append({
                    'operation_id': operation_id,
                    'type': 'cad_reparse_needs_manual_space_review',
                    'at': time.time(), 'revision': current['revision'],
                    'actor': operation.get('actor') or '',
                    'payload': {
                        'code': getattr(ex, 'code', 'cad_reparse_failed'),
                        'cad_facts_hash': facts_hash,
                        'hard_error_codes': sorted({
                            str(row.get('code') or '') for row in report.get('hard_errors') or []
                            if row.get('code')
                        }),
                    },
                })
                return current
            try:
                retained, _, result_state = cas_update_project(
                    project_id, retain_editable_draft,
                    expected_state_hash=operation['base_state_hash'])
                result_revision = retained.get('revision')
            except (RuntimeError, FileNotFoundError):
                update_cad_reparse_operation(
                    CAD_ROOT, project_id, operation_id, status='conflict', stage='conflict', progress=100,
                    error_code='cad_reparse_project_conflict',
                    error='项目在重解析期间已被修改；人工草稿未覆盖当前项目')
                return
        update_cad_reparse_operation(
            CAD_ROOT, project_id, operation_id,
            status=('needs_review' if result_revision is not None else 'failed'),
            stage=('needs_review' if result_revision is not None else 'failed'), progress=100,
            error_code=getattr(ex, 'code', 'cad_reparse_failed'),
            error=getattr(ex, 'message', str(ex)),
            result_revision=result_revision, result_state_hash=result_state,
            failure_evidence={
                'report_path': summary.get('report_path') or '',
                'report_sha256': summary.get('report_sha256') or '',
                'hard_error_summary': summary.get('hard_error_summary') or [],
            })
    except (RuntimeError, FileNotFoundError):
        # CadError and CadReparseOperationError both inherit RuntimeError, so
        # this conflict handler must remain after their evidence-retention
        # branch.  Otherwise a valid fail-closed draft is silently discarded.
        update_cad_reparse_operation(
            CAD_ROOT, project_id, operation_id, status='conflict', stage='conflict', progress=100,
            error_code='cad_reparse_project_conflict',
            error='项目在重解析期间发生变化；解析结果未覆盖当前项目')
    except Exception as ex:
        logger.exception('[CAD建模] 异步重解析异常')
        update_cad_reparse_operation(
            CAD_ROOT, project_id, operation_id, status='failed', stage='failed', progress=100,
            error_code='cad_reparse_unhandled', error=str(ex)[:500])


@router.get('/api/whole-home/projects/{project_id}/cad/reparse/{operation_id}')
def get_whole_home_cad_reparse_operation(project_id: str, operation_id: str):
    try:
        operation = get_cad_reparse_operation(CAD_ROOT, project_id, operation_id)
    except CadReparseOperationError as ex:
        raise HTTPException(ex.status_code, {'code': ex.code, 'message': ex.message}) from ex
    if not operation:
        raise HTTPException(404, 'CAD 重解析任务不存在')
    return public_cad_reparse_operation(operation)


_CAD_AI_ASSIST_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'summary': {'type': 'STRING'},
        'orientation_assessment': {
            'type': 'OBJECT',
            'properties': {
                'sky_to_ground': {'type': 'BOOLEAN'},
                'cad_y_screen_direction': {'type': 'STRING'},
                'confidence': {'type': 'NUMBER'},
                'reason': {'type': 'STRING'},
            },
            'required': ['sky_to_ground', 'cad_y_screen_direction', 'confidence', 'reason'],
        },
        'room_label_proposals': {
            'type': 'ARRAY', 'items': {
                'type': 'OBJECT',
                'properties': {
                    'physical_space_id': {'type': 'STRING'}, 'label': {'type': 'STRING'},
                    'zone_type': {'type': 'STRING'}, 'confidence': {'type': 'NUMBER'},
                    'evidence_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                    'reason': {'type': 'STRING'},
                },
                'required': ['physical_space_id', 'label', 'zone_type', 'confidence', 'evidence_ids', 'reason'],
            },
        },
        'wall_role_reviews': {
            'type': 'ARRAY', 'items': {
                'type': 'OBJECT',
                'properties': {
                    'evidence_id': {'type': 'STRING'},
                    'disposition': {'type': 'STRING', 'enum': ['keep_wall', 'exclude_nonwall', 'needs_review']},
                    'confidence': {'type': 'NUMBER'}, 'reason': {'type': 'STRING'},
                },
                'required': ['evidence_id', 'disposition', 'confidence', 'reason'],
            },
        },
        'opening_reviews': {
            'type': 'ARRAY', 'items': {
                'type': 'OBJECT',
                'properties': {
                    'candidate_id': {'type': 'STRING'},
                    'disposition': {'type': 'STRING', 'enum': ['accept', 'reject', 'needs_review']},
                    'kind': {'type': 'STRING', 'enum': ['door', 'window', 'opening', 'unknown']},
                    'confidence': {'type': 'NUMBER'}, 'reason': {'type': 'STRING'},
                },
                'required': ['candidate_id', 'disposition', 'kind', 'confidence', 'reason'],
            },
        },
        'risks': {
            'type': 'ARRAY', 'items': {
                'type': 'OBJECT',
                'properties': {
                    'code': {'type': 'STRING'},
                    'severity': {'type': 'STRING', 'enum': ['warning', 'review', 'hard']},
                    'reason': {'type': 'STRING'},
                },
                'required': ['code', 'severity', 'reason'],
            },
        },
    },
    'required': [
        'summary', 'orientation_assessment', 'room_label_proposals',
        'wall_role_reviews', 'opening_reviews', 'risks',
    ],
}


def _cad_ai_assist_evidence(project: dict, report: dict) -> dict:
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    role_rows = []
    for row in report.get('selected_entity_role_evidence') or []:
        if not isinstance(row, dict):
            continue
        role_rows.append({key: copy.deepcopy(row.get(key)) for key in (
            'evidence_id', 'role', 'confidence', 'reason_codes', 'bbox_m',
            'source_handles', 'entity_types', 'layers') if key in row})
    opening_rows = []
    for row in report.get('raw_opening_candidates') or []:
        if not isinstance(row, dict):
            continue
        opening_rows.append({key: copy.deepcopy(row.get(key)) for key in (
            'candidate_id', 'kind', 'status', 'confidence', 'width_m',
            'center_cad_m', 'reason_codes', 'source_handles') if key in row})
    spaces = [{
        'physical_space_id': str(row.get('id') or ''),
        'current_label': str(row.get('label') or ''),
        'space_type': str(row.get('space_type') or ''),
        'face_ids': [str(value) for value in row.get('face_ids') or []],
    } for row in model.get('physical_spaces') or [] if isinstance(row, dict)]
    anchors = [{key: copy.deepcopy(row.get(key)) for key in (
        'id', 'text', 'point_m', 'layer', 'root_handle') if key in row}
               for row in report.get('text_anchors') or [] if isinstance(row, dict)]
    return {
        'coordinate_contract': {
            'version': int(model.get('coordinate_contract_version') or 0),
            'coordinate_system': str(model.get('coordinate_system') or ''),
            'cad_to_model': copy.deepcopy(model.get('cad_to_model') or {}),
            'camera_required': 'sky-to-ground; CAD +X screen-right; CAD +Y screen-up',
        },
        'selected_candidate_id': str(report.get('selected_candidate_id') or ''),
        'role_summary': copy.deepcopy(report.get('selected_entity_role_summary') or {}),
        'role_evidence': role_rows[:160],
        'opening_summary': copy.deepcopy(report.get('raw_opening_summary') or {}),
        'opening_candidates': opening_rows[:120],
        'physical_spaces': spaces[:100],
        'text_anchors': anchors[:160],
        'hard_errors': copy.deepcopy(report.get('hard_errors') or [])[:100],
        'warnings': copy.deepcopy(report.get('warnings') or [])[:100],
    }


def _validate_cad_ai_references(payload: dict, evidence: dict) -> tuple[dict, list[dict]]:
    value = copy.deepcopy(payload)
    issues: list[dict] = []
    valid_spaces = {str(row.get('physical_space_id') or '') for row in evidence['physical_spaces']}
    valid_roles = {str(row.get('evidence_id') or '') for row in evidence['role_evidence']}
    valid_openings = {str(row.get('candidate_id') or '') for row in evidence['opening_candidates']}
    for row in value.get('room_label_proposals') or []:
        if str(row.get('physical_space_id') or '') not in valid_spaces:
            issues.append({'code': 'ai_reference_unknown_physical_space',
                           'reference': str(row.get('physical_space_id') or '')})
        unknown = sorted({str(item) for item in row.get('evidence_ids') or []} - valid_roles)
        if unknown:
            issues.append({'code': 'ai_reference_unknown_role_evidence', 'references': unknown})
    for row in value.get('wall_role_reviews') or []:
        if str(row.get('evidence_id') or '') not in valid_roles:
            issues.append({'code': 'ai_reference_unknown_role_evidence',
                           'reference': str(row.get('evidence_id') or '')})
    for row in value.get('opening_reviews') or []:
        if str(row.get('candidate_id') or '') not in valid_openings:
            issues.append({'code': 'ai_reference_unknown_opening_candidate',
                           'reference': str(row.get('candidate_id') or '')})
    value.pop('_floor_engine_model', None)
    value.pop('_floor_engine_usage_metadata', None)
    return value, issues


@router.post('/api/whole-home/projects/{project_id}/cad/ai-assist')
async def review_whole_home_cad_with_ai(project_id: str, req: WholeHomeCadAiAssistRequest):
    """Ask Gemini for bounded proposals; never write its answer into geometry."""
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if project.get('source_type') != 'cad':
        raise HTTPException(400, '只有 CAD 项目可以使用 CAD AI 辅助复核')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型版本冲突',
                                  'current_revision': project.get('revision', 0)})
    try:
        report = _cad_report(project)
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    preview_path = str(report.get('semantic_preview_path') or project.get('floorplan_path') or '')
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(409, {'code': 'cad_ai_preview_missing',
                                  'message': '缺少已选 CAD 平面预览，请先重新解析'})
    model = project.get('model') if isinstance(project.get('model'), dict) else {}
    if (int(model.get('coordinate_contract_version') or 0) < 2
            or str(model.get('coordinate_system') or '') != 'right-handed-y-up-x-east-z-south-v2'):
        raise HTTPException(409, {'code': 'cad_coordinate_contract_legacy_invalid',
                                  'message': '请先用坐标契约 V2 重新解析 CAD，再让 Gemini 复核'})
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    evidence = _cad_ai_assist_evidence(project, report)
    input_facts = {
        'source_sha256': str((project.get('cad_source') or {}).get('sha256') or
                             report.get('source_sha256') or ''),
        'cad_facts_hash': str(model.get('cad_facts_hash') or ''),
        'preview_sha256': _sha256_file(preview_path),
        'evidence': evidence,
        'review_passes': req.review_passes,
    }
    input_hash = canonical_hash(input_facts)
    operation_id = req.operation_id or f"cadai_{input_hash[:20]}"
    existing_operation = next((row for row in project.get('operations') or []
                               if row.get('operation_id') == operation_id), None)
    if existing_operation:
        if str(existing_operation.get('request_fingerprint') or '') != input_hash:
            raise HTTPException(409, {'code': 'cad_ai_idempotency_conflict',
                                      'message': 'operation_id 已用于其他 CAD AI 输入'})
        advisory_id = str((existing_operation.get('payload') or {}).get('advisory_id') or '')
        existing = next((row for row in project.get('cad_ai_advisories') or []
                         if row.get('advisory_id') == advisory_id), None)
        if existing:
            return copy.deepcopy(existing)
    base_prompt = (
        '你是 CAD 平面图复核员。下方 JSON 是确定性解析器证据，附图是同一候选的 CAD 正向预览。'
        '你只能提出建议，绝不能创造墙、坐标、尺寸或实体 ID。所有建议必须引用 JSON 中已存在的 ID；'
        '看不清就输出 needs_review。家具、床、柜子、桌子和洁具不得当墙。'
        '相机合同必须理解为从天空向地面看，CAD +X 向屏幕右，CAD +Y 向屏幕上。'
        '输出重点：可疑墙角色、门窗候选、房间标签和当前解析风险。\nEVIDENCE_JSON:\n'
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )
    passes = []
    previous = None
    for index in range(req.review_passes):
        prompt = base_prompt
        if previous is not None:
            prompt += (
                '\n这是第一遍建议，请作为第二位审计员只修正无证据、方向误读、家具误判和过度自信项；'
                '输出一份完整替代建议：\n' + json.dumps(previous, ensure_ascii=False, sort_keys=True))
        raw_result, error = await asyncio.to_thread(
            call_gemini_json, api_key, prompt, [preview_path], _CAD_AI_ASSIST_SCHEMA,
            max_output_tokens=7000)
        if error or not isinstance(raw_result, dict):
            failure = {
                'pass_index': index + 1, 'status': 'failed',
                'error': str(error or 'Gemini 返回为空')[:1000],
            }
            passes.append(failure)
            break
        model_name = str(raw_result.get('_floor_engine_model') or '')
        usage = copy.deepcopy(raw_result.get('_floor_engine_usage_metadata') or {})
        normalized, reference_issues = _validate_cad_ai_references(raw_result, evidence)
        passes.append({
            'pass_index': index + 1, 'status': 'done', 'model': model_name,
            'usage_metadata': usage, 'reference_issues': reference_issues,
            'proposal': normalized,
        })
        previous = normalized
    if previous is None:
        project.setdefault('operations', []).append({
            'type': 'cad_ai_assist_failed', 'operation_id': operation_id,
            'request_fingerprint': input_hash,
            'payload': {'call_count': len(passes), 'errors': passes},
            'at': time.time(), 'revision': req.base_revision, 'actor': req.annotator_id,
        })
        _persist_project(project)
        raise HTTPException(502, {'code': 'cad_ai_assist_failed',
                                  'message': 'Gemini CAD 复核失败', 'passes': passes})
    advisory = {
        'schema_version': 1,
        'advisory_id': f"cadadv_{canonical_hash({'input_hash': input_hash, 'passes': passes})[:20]}",
        'project_id': project_id,
        'base_revision': req.base_revision,
        'input_hash': input_hash,
        'authority': 'advisory_only',
        'geometry_mutated': False,
        'revision_unchanged': True,
        'call_cap': 2,
        'call_count': len(passes),
        'passes': passes,
        'proposal': previous,
        'reference_validation': {
            'status': ('needs_review' if any(row.get('reference_issues') for row in passes) else 'passed'),
            'issue_count': sum(len(row.get('reference_issues') or []) for row in passes),
        },
        'created_at': time.time(),
    }
    project.setdefault('cad_ai_advisories', []).append(copy.deepcopy(advisory))
    project['cad_ai_advisories'] = project['cad_ai_advisories'][-20:]
    project.setdefault('operations', []).append({
        'type': 'cad_ai_assist_advisory', 'operation_id': operation_id,
        'request_fingerprint': input_hash,
        'payload': {
            'advisory_id': advisory['advisory_id'], 'authority': 'advisory_only',
            'geometry_mutated': False, 'call_count': advisory['call_count'],
            'reference_validation': copy.deepcopy(advisory['reference_validation']),
        },
        'at': advisory['created_at'], 'revision': req.base_revision, 'actor': req.annotator_id,
    })
    _persist_project(project)
    return advisory


@router.post('/api/whole-home/projects/{project_id}/cad/semantic-reconstruct')
async def reconstruct_whole_home_cad_semantics(
        project_id: str, req: WholeHomeCadSemanticReconstructRequest):
    """Use Gemini for CAD room semantics while retaining the audited CAD wall graph."""
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if project.get('source_type') != 'cad':
        raise HTTPException(400, '只有 CAD 来源项目可以执行 CAD 语义重建')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型版本冲突',
                                  'current_revision': project.get('revision', 0)})
    try:
        parse_report = _cad_report(project)
        candidate_model = copy.deepcopy(project.get('model') or {})
        if not candidate_model.get('physical_spaces') and project.get('cad_space_draft_pointer'):
            candidate_model = load_cad_draft_model(project['cad_space_draft_pointer'])
    except CadError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    unresolved = {str(row.get('code') or '') for row in parse_report.get('hard_errors') or []}
    disallowed = unresolved - {'cad_room_semantics_unresolved'}
    if disallowed:
        raise HTTPException(409, {
            'code': 'cad_structural_gate_blocked',
            'message': '请先明确选择正确 CAD 平面并消除结构硬错误，再运行 AI 语义重建',
            'hard_error_codes': sorted(disallowed),
        })
    preview_path = str(parse_report.get('semantic_preview_path') or '')
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(409, {
            'code': 'cad_semantic_preview_missing',
            'message': 'CAD 语义预览缺失，请重新解析所选平面',
        })
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    attempt = {
        'attempt_id': new_id('cadsem'), 'started_at': time.time(),
        'candidate_id': parse_report.get('selected_candidate_id') or '',
        'preview_sha256': _sha256_file(preview_path), 'status': 'running',
    }
    project.setdefault('cad_semantic_attempts', []).append(attempt)
    project.update(status='analyzing', stage='Gemini 正在读取已选 CAD 平面；CAD 墙线保持只读', error='')
    _persist_project(project)
    try:
        cached_attempt = next((
            row for row in reversed((project.get('cad_semantic_attempts') or [])[:-1])
            if row.get('candidate_id') == attempt['candidate_id']
            and row.get('preview_sha256') == attempt['preview_sha256']
            and isinstance(row.get('ai_model_snapshot'), dict)
        ), None)
        if cached_attempt:
            ai_model = copy.deepcopy(cached_attempt['ai_model_snapshot'])
            error = ''
            ai_model_name = str(cached_attempt.get('ai_model') or ai_model.get('ai_model') or '')
            attempt['topology_source'] = 'cached_previous_attempt'
            attempt['cached_from_attempt_id'] = cached_attempt.get('attempt_id') or ''
        else:
            ai_model, error, ai_model_name = await asyncio.to_thread(
                analyze_whole_home, api_key, preview_path)
        if error or not ai_model:
            raise CadError('cad_ai_semantic_failed', error or 'CAD 语义识别返回为空', status_code=502)
        ai_model['ai_model'] = ai_model_name
        attempt.update(
            ai_model=ai_model_name,
            ai_model_snapshot=copy.deepcopy(ai_model),
            topology_finished_at=time.time(),
        )
        _persist_project(project)
        hybrid_model, hybrid_report = await asyncio.to_thread(
            cad_hybrid_model_from_ai, candidate_model, parse_report, ai_model)
        cad_validation = validate_cad_model(hybrid_model, hybrid_report)
        geometry_report = validate_model(hybrid_model, preview_path)
        if cad_validation.get('hard_errors') or geometry_report.get('hard_errors'):
            raise CadError('cad_ai_local_validation_failed', 'CAD+AI 混合模型未通过本地几何核对',
                           details={'cad_validation': cad_validation,
                                    'geometry_report': geometry_report})

        semantic_model, semantic_error, semantic_model_name = await asyncio.to_thread(
            analyze_semantic_layout, api_key, preview_path, hybrid_model)
        if (not isinstance(semantic_model.get('cad_semantic_derivation'), dict)
                and isinstance(hybrid_model.get('cad_semantic_derivation'), dict)):
            semantic_model['cad_semantic_derivation'] = copy.deepcopy(
                hybrid_model['cad_semantic_derivation'])
        overlay_validation = validate_cad_semantic_overlay(hybrid_model, semantic_model)
        semantic_cad_validation = validate_cad_model(semantic_model, hybrid_report)
        if not overlay_validation.get('hard_errors') and not semantic_cad_validation.get('hard_errors'):
            hybrid_model = semantic_model
        else:
            semantic_error = (
                f"AI 布局补全被 CAD 本地门禁拒绝；保留房间重建结果。"
                f" overlay={overlay_validation.get('hard_errors') or []};"
                f" cad={semantic_cad_validation.get('hard_errors') or []}"
            )
        refresh_hybrid_reference_anchor_report(hybrid_model)
        hybrid_model['geometry_report'] = validate_model(hybrid_model, preview_path)
        hybrid_model['semantic_report'] = validate_semantic_layout(hybrid_model)
        facts_hash = cad_facts_hash(hybrid_model)
        hybrid_model['cad_facts_hash'] = facts_hash
        hybrid_report = persist_cad_report(project_id, hybrid_report, 'semantic_reconstruct')
        revision = req.base_revision + 1
        for capture in project.get('captures') or []:
            capture['status'] = 'stale'
            capture['stale_reason'] = 'cad_semantics_reconstructed'
        attempt.update(
            status='done', finished_at=time.time(), ai_model=ai_model_name,
            semantic_ai_model=semantic_model_name, room_count=len(hybrid_model.get('rooms') or []),
            cad_facts_hash=facts_hash, semantic_error=semantic_error or '',
        )
        project.update(
            status='done', stage=(
                'CAD 房间与语义灰模已重建，请复核并锁定'
                if not hybrid_model['semantic_report'].get('hard_errors')
                else 'CAD 房间已重建；缺失语义锚点已列出，可继续补全'),
            error='', floorplan_path=preview_path, model=hybrid_model,
            parse_report=cad_report_summary(hybrid_report), revision=revision, verified=False,
            verified_revision=0, cad_candidate_model={}, ai_model=ai_model_name,
            semantic_ai_model=semantic_model_name, semantic_error=semantic_error or '',
            summary=(f"CAD+AI 混合模型：{len(hybrid_model.get('rooms') or [])} 个空间、"
                     f"{len(hybrid_model.get('openings') or [])} 个门窗、"
                     f"{len(hybrid_model.get('fixed_objects') or [])} 个语义锚点"),
            cad_import={
                **copy.deepcopy(project.get('cad_import') or {}),
                'schema_version': 2, 'cad_facts_hash': facts_hash,
                'cad_to_model': copy.deepcopy(hybrid_report.get('cad_to_model') or {}),
                'model_to_cad': copy.deepcopy(hybrid_report.get('model_to_cad') or {}),
                'semantic_derivation': copy.deepcopy(hybrid_report.get('cad_semantic_derivation') or {}),
            },
        )
        project.setdefault('operations', []).append({
            'type': 'cad_ai_semantic_reconstruct',
            'payload': {
                'attempt_id': attempt['attempt_id'], 'cad_facts_hash': facts_hash,
                'ai_model': ai_model_name, 'semantic_ai_model': semantic_model_name,
            },
            'at': time.time(), 'revision': revision, 'actor': req.annotator_id,
        })
        _persist_project(project)
        return _whole_home_project_view(project)
    except CadError as ex:
        attempt.update(status='failed', finished_at=time.time(), error=ex.to_dict())
        project.update(status='failed', stage='', error=ex.message, cad_error=ex.to_dict(),
                       verified=False, verified_revision=0)
        _persist_project(project)
        if ex.status_code >= 500:
            raise HTTPException(ex.status_code, ex.to_dict())
        return _whole_home_project_view(project)
    except Exception as ex:
        logger.exception('[CAD建模] AI 语义重建异常')
        attempt.update(status='failed', finished_at=time.time(),
                       error={'code': 'cad_ai_unhandled_error', 'message': str(ex)})
        project.update(status='failed', stage='', error=f'CAD AI 语义重建异常: {ex}',
                       verified=False, verified_revision=0)
        _persist_project(project)
        raise HTTPException(500, {'code': 'cad_ai_unhandled_error', 'message': str(ex)})


@router.put('/api/whole-home/projects/{project_id}/model')
def save_whole_home_model(project_id: str, req: WholeHomeModelSaveRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型已在其他页面更新，请刷新', 'current_revision': project.get('revision', 0)})
    if project.get('source_type') == 'cad':
        raise HTTPException(409, {
            'message': 'CAD v1 的权威墙、房间、开口、尺度及观察物为只读；请使用 CAD 重新解析，语义代理需走专用语义补全',
            'code': 'cad_geometry_read_only',
        })
    source = project.get('floorplan_path')
    try:
        from PIL import Image
        with Image.open(source) as image:
            width, height = image.size
    except Exception:
        width = height = 1
    model = normalize_model(req.model, source_width=width, source_height=height, source='human')
    model['geometry_report'] = validate_model(model, source)
    old_hash = model_hash(project.get('model') or {}) if project.get('model') else ''
    new_hash = model_hash(model)
    revision = req.base_revision + 1
    operations = [
        {'type': str(item.get('type') or 'edit_model')[:80], 'payload': item.get('payload') or {},
         'at': time.time(), 'revision': revision, 'actor': req.annotator_id}
        for item in req.operations
    ] or [{'type': 'save_model', 'payload': {}, 'at': time.time(), 'revision': revision, 'actor': req.annotator_id}]
    captures = project.get('captures') or []
    if old_hash and old_hash != new_hash:
        for capture in captures:
            capture['status'] = 'stale'
        _invalidate_geometry_lock(project, 'model_geometry_changed')
    project.update(
        model=model, revision=revision, status='done', stage='整屋模型草稿已保存', error='',
        verified=False, verified_revision=0, captures=captures,
    )
    project.setdefault('operations', []).extend(operations)
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/semantic-layout')
async def rebuild_whole_home_semantic_layout(project_id: str, req: WholeHomeSemanticLayoutRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型版本冲突', 'current_revision': project.get('revision', 0)})
    _assert_cad_project_gate(project)
    geometry_report = validate_model(project.get('model') or {}, project.get('floorplan_path'))
    if geometry_report.get('hard_errors'):
        raise HTTPException(400, {'message': '请先修正整屋几何，再重建语义布局', **geometry_report})
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    semantic_model, semantic_error, semantic_ai_model = await asyncio.to_thread(
        analyze_semantic_layout, api_key, project['floorplan_path'], project.get('model') or {})
    if project.get('source_type') == 'cad':
        overlay_validation = validate_cad_semantic_overlay(project.get('model') or {}, semantic_model)
        post_validation = validate_cad_model(semantic_model, project.get('parse_report') or {})
        if overlay_validation.get('hard_errors') or post_validation.get('hard_errors'):
            raise HTTPException(409, {
                'message': 'AI 语义补全试图改变 CAD 权威事实或注入观察物，结果已丢弃',
                'code': 'cad_semantic_facts_changed',
                'semantic_overlay_validation': overlay_validation,
                'cad_validation': post_validation,
            })
    if semantic_error and int((semantic_model.get('semantic_report') or {}).get('audit_passes') or 0) == 0:
        raise HTTPException(502, f'语义布局重建失败，原模型未改动：{semantic_error}')
    old_hash = model_hash(project.get('model') or {})
    new_hash = model_hash(semantic_model)
    captures = project.get('captures') or []
    if old_hash != new_hash:
        for capture in captures:
            capture['status'] = 'stale'
            capture['stale_reason'] = 'semantic_layout_changed'
        _invalidate_geometry_lock(project, 'semantic_layout_changed')
    revision = req.base_revision + 1
    semantic_report = semantic_model.get('semantic_report') or validate_semantic_layout(semantic_model)
    project.update(
        model=semantic_model, captures=captures, revision=revision, verified=False, verified_revision=0,
        status='done', error='', semantic_error=semantic_error or '', semantic_ai_model=semantic_ai_model,
        stage=('语义灰模已重建，请复核并锁定' if not semantic_report.get('hard_errors')
               else '语义灰模已重建，但仍有必须修正的问题'),
    )
    project.setdefault('operations', []).append({
        'type': 'rebuild_semantic_layout',
        'payload': {
            'semantic_ai_model': semantic_ai_model,
            'hard_error_count': len(semantic_report.get('hard_errors') or []),
            'warning_count': len(semantic_report.get('warnings') or []),
        },
        'at': time.time(), 'revision': revision, 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/verify')
def verify_whole_home_model(project_id: str, req: WholeHomeVerifyRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if int(project.get('revision') or 0) != req.base_revision:
        raise HTTPException(409, {'message': '整屋模型版本冲突', 'current_revision': project.get('revision', 0)})
    _assert_cad_project_gate(project)
    _assert_geometry_production_gate(project)
    report = validate_model(project.get('model') or {}, project.get('floorplan_path'))
    if report['hard_errors']:
        project['model']['geometry_report'] = report
        _persist_project(project)
        raise HTTPException(400, {'message': '整屋模型存在必须修正的几何问题', **report})
    semantic_report = validate_semantic_layout(project.get('model') or {})
    project['model']['semantic_report'] = semantic_report
    if semantic_report['hard_errors']:
        _persist_project(project)
        raise HTTPException(400, {
            'message': '整屋模型缺少可用于自动机位的房间语义，必须先补全',
            'semantic_report': semantic_report,
        })
    warning_codes = {item.get('code') for item in report['warnings'] if item.get('code')}
    semantic_warning_codes = {item.get('code') for item in semantic_report['warnings'] if item.get('code')}
    missing = sorted((warning_codes | semantic_warning_codes) - set(req.acknowledged_warning_codes))
    if missing:
        raise HTTPException(400, {
            'message': '请确认整屋模型警告后再锁定', 'warning_codes': missing,
            **report, 'semantic_report': semantic_report,
        })
    # Correspondence Lock commit already created the immutable geometry
    # revision.  Verification approves that exact revision; incrementing here
    # would make the acceptance report stale immediately.  Legacy projects
    # retain the historical revision behaviour until enrolled in v1.
    revision = (req.base_revision if project.get('geometry_acceptance_required')
                else req.base_revision + 1)
    project['model']['geometry_report'] = report
    project['model']['semantic_report'] = semantic_report
    project.update(
        verified=True, verified_revision=revision, revision=revision, status='verified',
        stage='图纸与整屋 3D 对应已锁定，可以进入机位与全景', error='', verified_at=time.time(),
        verified_by=req.annotator_id,
    )
    project.setdefault('operations', []).append({
        'type': 'verify_whole_home', 'payload': {
            'warning_codes': list(warning_codes | semantic_warning_codes),
            'geometry_report_hash': str((project.get('geometry_acceptance') or {}).get('report_hash') or ''),
            'geometry_manifest_hash': str(((project.get('model') or {}).get('geometry_manifest') or {}).get('manifest_hash') or ''),
        },
        'at': time.time(), 'revision': revision, 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


def _capture_hash(model: dict, camera: dict, aspect_ratio: str) -> str:
    raw = json.dumps({'model': model_hash(model), 'camera': camera, 'aspect_ratio': aspect_ratio}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _reference_batch_model_hash(model: dict) -> str:
    geometry = copy.deepcopy(model or {})
    for key in ('cameras', 'geometry_report', 'semantic_report'):
        geometry.pop(key, None)
    return model_hash(geometry)


def _reference_owner_hash(token: str) -> str:
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _assert_reference_ownership(document: dict, ownership: dict) -> tuple[dict, dict]:
    batch = next((
        row for row in document.get('reference_software_capture_batches') or []
        if str(row.get('batch_id') or '') == str(ownership.get('batch_id') or '')),
        None)
    if not batch:
        raise RuntimeError('reference_capture_batch_missing')
    model = document.get('model') or {}
    stable = (
        int(document.get('revision') or 0) == int(batch.get('project_revision') or -1)
        and str(batch.get('proposal_id') or '') == str(ownership.get('proposal_id') or '')
        and str(batch.get('proposal_hash') or '') == str(ownership.get('proposal_hash') or '')
        and str(batch.get('cad_facts_hash') or '') == str(model.get('cad_facts_hash') or '')
        and str(batch.get('model_facts_hash') or '') == reference_model_facts_hash(model)
        and str(batch.get('model_hash') or '') == _reference_batch_model_hash(model)
        and int(batch.get('batch_version') or 0)
        == int(ownership.get('batch_version') or -1)
    )
    if not stable:
        raise RuntimeError('reference_capture_batch_stale')
    slot = next((
        row for row in batch.get('slots') or []
        if str(row.get('slot_id') or '') == str(ownership.get('slot_id') or '')),
        None)
    if not slot:
        raise RuntimeError('reference_capture_slot_missing')
    token_hash = _reference_owner_hash(str(ownership.get('owner_token') or ''))
    if (str(slot.get('status') or '') != 'rendering'
            or int(slot.get('slot_version') or 0)
            != int(ownership.get('slot_version') or -1)
            or not hmac.compare_digest(
                str(slot.get('owner_token_hash') or ''), token_hash)
            or float(slot.get('owner_expires_at') or 0) <= time.time()):
        raise RuntimeError('reference_capture_slot_owner_fenced')
    return batch, slot


def _reference_capture_candidate(project: dict, req: WholeHomeCaptureRequest) -> tuple[dict, dict]:
    if not (req.reference_proposal_id and req.reference_proposal_hash and req.reference_slot_id
            and req.candidate_id):
        raise HTTPException(409, {'code': 'reference_proposal_missing',
                                  'message': 'reference capture 必须携带 proposal/slot/candidate/hash'})
    proposal_records = [
        row for row in project.get('reference_camera_proposals') or []
        if str(row.get('proposal_id') or '') == req.reference_proposal_id
        and str(row.get('proposal_hash') or '') == req.reference_proposal_hash
    ]
    if len(proposal_records) != 1:
        raise HTTPException(409, {'code': 'reference_proposal_not_found_or_tampered'})
    proposal = load_reference_camera_proposal(proposal_records[0])
    if not proposal:
        raise HTTPException(409, {'code': 'reference_proposal_storage_missing'})
    if any(isinstance(row, dict) and row.get('candidates')
           for row in project.get('reference_camera_proposals') or []):
        compact_records = []
        for row in project.get('reference_camera_proposals') or []:
            full = load_reference_camera_proposal(row)
            if not full:
                continue
            compact_records.append({
                'proposal_id': full.get('proposal_id') or '',
                'proposal_hash': full.get('proposal_hash') or '',
                'status': full.get('status') or '',
                'project_revision': full.get('project_revision'),
                'cad_facts_hash': full.get('cad_facts_hash') or '',
                'model_facts_hash': full.get('model_facts_hash') or '',
                'slot_pool_count': len(full.get('slot_pools') or []),
                'candidate_count': len(full.get('candidates') or []),
                'storage_key': save_reference_camera_proposal(
                    str(project.get('project_id') or ''), full),
            })
        project['reference_camera_proposals'] = compact_records
        project.setdefault('operations', []).append({
            'type': 'reference_proposals_externalized',
            'payload': {'proposal_count': len(compact_records),
                        'preservation': 'full_json_files_retained'},
            'at': time.time(), 'revision': project.get('revision', 0),
            'actor': 'local-software-renderer',
        })
        _persist_project(project)
    model = project.get('model') or {}
    stale = (
        int(proposal.get('project_revision') or -1) != int(project.get('revision') or 0)
        or str(proposal.get('cad_facts_hash') or '') != str(model.get('cad_facts_hash') or '')
        or str(proposal.get('model_facts_hash') or '') != reference_model_facts_hash(model)
    )
    if stale:
        raise HTTPException(409, {'code': 'reference_proposal_stale',
                                  'message': 'CAD/model/revision 已变化，请重新生成 reference 候选'})
    candidate = find_reference_candidate(proposal, req.candidate_id, req.reference_slot_id)
    if not candidate:
        raise HTTPException(409, {'code': 'reference_candidate_not_in_proposal'})
    expected = candidate.get('camera') or {}
    submitted = req.camera if isinstance(req.camera, dict) else {}
    for key in ('position', 'target'):
        for axis in ('x', 'y', 'z'):
            if not math.isclose(float((submitted.get(key) or {}).get(axis) or 0),
                                float((expected.get(key) or {}).get(axis) or 0),
                                rel_tol=0, abs_tol=1e-5):
                raise HTTPException(409, {'code': 'reference_camera_tampered',
                                          'field': f'{key}.{axis}'})
    if not math.isclose(float(submitted.get('focal_length_mm') or 0),
                        float(expected.get('focal_length_mm') or 0), rel_tol=0, abs_tol=1e-5):
        raise HTTPException(409, {'code': 'reference_camera_tampered', 'field': 'focal_length_mm'})
    return proposal, candidate


def _bound_scene_recipe(project: dict, recipe_id: str, scene_hash: str) -> dict:
    recipe_id = str(recipe_id or '')
    scene_hash = str(scene_hash or '')
    if not recipe_id and not scene_hash:
        return {}
    if not recipe_id or not scene_hash:
        raise HTTPException(409, {'code': 'scene_recipe_binding_incomplete'})
    recipe = next((
        copy.deepcopy(row) for row in project.get('scene_recipes') or []
        if str(row.get('recipe_id') or '') == recipe_id
    ), {})
    if (not recipe or recipe.get('status') != 'locked'
            or str(project.get('active_scene_recipe_id') or '') != recipe_id):
        raise HTTPException(409, {'code': 'locked_scene_recipe_required'})
    if not hmac.compare_digest(str(recipe.get('scene_hash') or ''), scene_hash):
        raise HTTPException(409, {'code': 'scene_hash_mismatch'})
    return recipe


def _save_whole_home_capture(project_id: str, req: WholeHomeCaptureRequest,
                             reference_ownership: Optional[dict] = None):
    project = (load_project(project_id) if reference_ownership
               else _project_entry(project_id))
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定整屋几何，再保存 3D 机位')
    _assert_geometry_production_gate(project)
    scene_recipe = _bound_scene_recipe(project, req.scene_recipe_id, req.scene_hash)
    reference_proposal, reference_candidate = ({}, {})
    if req.reference_slot_id:
        reference_proposal, reference_candidate = _reference_capture_candidate(project, req)
        if not req.subject_id_data_url:
            raise HTTPException(409, {'code': 'reference_subject_id_missing'})
    expected_project_hash = ''
    if reference_ownership:
        project = load_project(project_id) or project
        try:
            _assert_reference_ownership(project, reference_ownership)
        except RuntimeError as ex:
            raise HTTPException(409, {
                'code': 'reference_capture_slot_owner_fenced',
                'message': str(ex),
            }) from ex
        expected_project_hash = state_hash(project)
    if req.plan_id or req.candidate_id:
        render_gate = req.camera.get('render_gate') if isinstance(req.camera, dict) else None
        if not isinstance(render_gate, dict) or render_gate.get('pass') is not True:
            raise HTTPException(400, '自动机位未通过灰模渲染门禁，禁止保存和付费生成')
    model = project.get('model') or {}
    camera_input = copy.deepcopy((reference_candidate.get('camera') if reference_candidate else req.camera) or {})
    if reference_candidate and isinstance(req.camera, dict):
        camera_input['render_gate'] = copy.deepcopy(req.camera.get('render_gate'))
    normalized = normalize_model({**model, 'cameras': [camera_input]}, source='human')
    if not normalized['cameras']:
        raise HTTPException(400, '机位参数无效')
    camera = normalized['cameras'][0]
    room_id = (str(reference_candidate.get('room_id') or '') if reference_candidate
               else req.room_id or infer_camera_room_id(model, camera))
    if not room_id:
        raise HTTPException(400, '无法判断机位所属房间，请先把机位放在有效房间内')
    camera['room_id'] = room_id
    if req.plan_id:
        camera['auto_plan_id'] = req.plan_id
    if req.candidate_id:
        camera['candidate_id'] = req.candidate_id
    camera['pool_rank'] = req.pool_rank
    camera['is_primary'] = req.is_primary
    if req.reference_slot_id:
        camera['reference_slot_id'] = req.reference_slot_id
    if camera.get('source') not in ('auto_geometry', 'ai_selected'):
        camera['source'] = 'human_3d'
    capture_id = new_id('capture')
    try:
        rgb_path = save_capture_data(project_id, capture_id, 'rgb', req.rgb_data_url)
        depth_path = save_capture_data(project_id, capture_id, 'depth', req.depth_data_url)
        normal_path = save_capture_data(project_id, capture_id, 'normal', req.normal_data_url)
        edge_path = save_capture_data(project_id, capture_id, 'edge', req.edge_data_url) if req.edge_data_url else ''
        semantic_path = save_capture_data(project_id, capture_id, 'semantic', req.semantic_data_url)
        subject_id_path = (save_capture_data(project_id, capture_id, 'subject_id', req.subject_id_data_url)
                           if req.subject_id_data_url else '')
        plan_overlay_path = save_camera_plan_overlay(
            project_id, capture_id, project.get('floorplan_path') or '', model, camera)
    except Exception as ex:
        raise HTTPException(400, f'保存机位缓冲图失败: {ex}') from ex
    if reference_candidate:
        from PIL import Image

        contract = split_reference_contract(project.get('reference_contract') or {})
        slots = [row for row in contract.get('slots') or []
                 if str(row.get('slot_id') or '') == req.reference_slot_id]
        if len(slots) != 1:
            raise HTTPException(409, {'code': 'reference_slot_contract_missing'})
        expected_subject_rows = ((reference_candidate.get('camera') or {})
                                 .get('reference_contract_validation') or {}).get('must_show_subjects') or []
        expected_subjects = [str(row.get('subject') or '') for row in expected_subject_rows]
        expected_anchors = {str(row.get('subject') or ''): str(row.get('anchor_id') or '')
                            for row in expected_subject_rows}
        actual_anchors = {
            str(row.get('subject') or ''): str(row.get('anchor_id') or '')
            for row in (req.subject_id_legend.get('subjects') or []) if isinstance(row, dict)
        }
        if actual_anchors != expected_anchors:
            raise HTTPException(409, {'code': 'reference_subject_legend_tampered',
                                      'expected': expected_anchors, 'actual': actual_anchors})
        safe_frame = dict((contract.get('camera') or {}).get('safe_frame') or {})
        safe_frame['subject_overrides'] = copy.deepcopy(
            slots[0].get('subject_safe_frame_overrides') or {})
        with Image.open(subject_id_path) as subject_image:
            pixel_evidence = evaluate_subject_id_pixels(
                subject_image, req.subject_id_legend, expected_subjects,
                safe_frame,
            )
        if not pixel_evidence.get('pass'):
            raise HTTPException(409, {'code': 'reference_subject_id_gate_failed',
                                      'evidence': pixel_evidence})
        submitted_bounds = ((req.camera.get('reference_contract_validation') or {}).get('must_show_bounds')
                            if isinstance(req.camera, dict) else None)
        if not isinstance(submitted_bounds, list) or len(submitted_bounds) != len(pixel_evidence['must_show_bounds']):
            raise HTTPException(409, {'code': 'reference_subject_bounds_tampered',
                                      'message': '浏览器 bounds 缺失或数量与服务端 PNG 重算不一致'})
        submitted_by_subject = {str(row.get('subject') or ''): row for row in submitted_bounds
                                if isinstance(row, dict)}
        for actual in pixel_evidence['must_show_bounds']:
            submitted = submitted_by_subject.get(str(actual.get('subject') or ''))
            if (not submitted or str(submitted.get('anchor_id') or '') != str(actual.get('anchor_id') or '')
                    or any(not math.isclose(float(submitted.get(key) or 0), float(actual.get(key) or 0),
                                            rel_tol=0, abs_tol=1e-6)
                           for key in ('x_min', 'x_max', 'y_min', 'y_max'))):
                raise HTTPException(409, {'code': 'reference_subject_bounds_tampered',
                                          'subject': actual.get('subject')})
        validation = copy.deepcopy((reference_candidate.get('camera') or {})
                                   .get('reference_contract_validation') or {})
        validation.update({
            'width': pixel_evidence['width'], 'height': pixel_evidence['height'],
            'pixel_origin': 'top-left', 'buffer_sha': _sha256_file(subject_id_path),
            'must_show_bounds': pixel_evidence['must_show_bounds'],
            'safe_frame_status': 'pass', 'safe_frame_pass': True,
            'pixel_gate_version': pixel_evidence.get('version') or '',
            'proposal_id': req.reference_proposal_id,
            'proposal_hash': req.reference_proposal_hash,
        })
        camera['reference_contract_validation'] = validation
        camera['reference_proposal_id'] = req.reference_proposal_id
        camera['reference_proposal_hash'] = req.reference_proposal_hash
    existing_index = next((index for index, row in enumerate(model.get('cameras') or []) if row.get('id') == camera['id']), -1)
    if existing_index >= 0:
        model['cameras'][existing_index] = camera
    else:
        model.setdefault('cameras', []).append(camera)
    model['geometry_report'] = validate_model(model, project.get('floorplan_path'))
    project['model'] = model
    capture = {
        'capture_id': capture_id, 'camera_id': camera['id'], 'camera': camera,
        'aspect_ratio': req.aspect_ratio, 'rgb_path': rgb_path, 'depth_path': depth_path,
        'normal_path': normal_path, 'edge_path': edge_path,
        'semantic_path': semantic_path, 'semantic_legend': copy.deepcopy(req.semantic_legend),
        'subject_id_path': subject_id_path, 'subject_id_legend': copy.deepcopy(req.subject_id_legend),
        'plan_overlay_path': plan_overlay_path, 'room_id': room_id,
        'plan_id': req.plan_id, 'candidate_id': req.candidate_id,
        'reference_slot_id': req.reference_slot_id,
        'reference_proposal_id': req.reference_proposal_id,
        'reference_proposal_hash': req.reference_proposal_hash,
        'scene_recipe_id': str(scene_recipe.get('recipe_id') or ''),
        'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        'pool_rank': req.pool_rank, 'is_primary': req.is_primary,
        'source_hash': _capture_hash(model, camera, req.aspect_ratio),
        'status': 'confirmed', 'created_at': time.time(), 'created_by': req.annotator_id,
    }
    project.setdefault('captures', []).append(capture)
    project.setdefault('operations', []).append({
        'type': 'capture_3d_camera', 'payload': {
            'capture_id': capture_id, 'camera_id': camera['id'],
            'scene_recipe_id': str(scene_recipe.get('recipe_id') or ''),
            'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        },
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    if reference_ownership:
        try:
            batch, slot = _assert_reference_ownership(project, reference_ownership)
            now = time.time()
            slot.update(
                status='saved',
                attempts=copy.deepcopy(reference_ownership.get('attempts') or []),
                candidate_id=str(req.candidate_id or ''),
                capture_id=capture_id, reason='', updated_at=now,
                owner_token_hash='', owner_claimed_at=None,
                owner_expires_at=None,
                slot_version=int(slot.get('slot_version') or 0) + 1,
            )
            batch.update(
                batch_version=int(batch.get('batch_version') or 0) + 1,
                updated_at=now)
            project, _, _ = cas_update_project(
                project_id, lambda current: project,
                expected_state_hash=expected_project_hash)
        except (FileNotFoundError, RuntimeError) as ex:
            raise HTTPException(409, {
                'code': 'reference_capture_commit_cas_conflict',
                'message': str(ex),
            }) from ex
    else:
        _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/captures')
def save_whole_home_capture(project_id: str, req: WholeHomeCaptureRequest):
    return _save_whole_home_capture(
        project_id, req,
        reference_ownership=getattr(req, '_reference_ownership', None))


_PANO_ATLAS_KINDS = ('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id')

_PANO_HOTSPOT_CENTER_RADIUS_M = 0.20   # 文档 §7.2: 0.18–0.25m
_PANO_HOTSPOT_MIN_SPACING_M = 1.5      # 文档 §7.2: 同房间多热点至少约 1.5–2.5m


def _pano_hotspot_center_clear(model: dict, center_x: float, center_z: float) -> bool:
    """热点中心球(半径 0.20m)不穿墙/家具:中心 + 8 方位采样全部通过碰撞检查。"""
    probes = [(center_x, center_z)]
    for angle_index in range(8):
        angle = angle_index * math.pi / 4
        probes.append((center_x + _PANO_HOTSPOT_CENTER_RADIUS_M * math.cos(angle),
                       center_z + _PANO_HOTSPOT_CENTER_RADIUS_M * math.sin(angle)))
    return all(pano_hotspot_origin_clear(model, probe) for probe in probes)


def _assert_pano_hotspot_safe(project: dict, model: dict, pano_id: str,
                              center_x: float, center_z: float, room_id: str) -> None:
    if not _pano_hotspot_center_clear(model, center_x, center_z):
        raise HTTPException(409, {
            'code': 'pano_hotspot_collision',
            'message': '热点中心球(0.20m)内接触墙体或家具 footprint，请移动热点',
        })
    for row in project.get('pano_hotspots') or []:
        if str(row.get('pano_id') or '') == pano_id or str(row.get('room_id') or '') != room_id:
            continue
        other = row.get('camera_center_m') or {}
        distance = math.hypot(
            float((other.get('x') or 0)) - center_x, float((other.get('z') or 0)) - center_z)
        if distance < _PANO_HOTSPOT_MIN_SPACING_M:
            raise HTTPException(409, {
                'code': 'pano_hotspot_too_close',
                'message': f'同房间已有热点({row.get("pano_id")}),间距 {distance:.2f}m < '
                           f'{_PANO_HOTSPOT_MIN_SPACING_M}m;超大开放区多热点请保持至少 1.5m',
            })


def _pano_contract_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _pano_manifest_from_request(project: dict, req: WholeHomePanoCaptureRequest,
                                atlas_paths: dict[str, str], *, capture_id: str,
                                capture_revision: int) -> dict:
    model = project.get('model') or {}
    render_contract = copy.deepcopy(req.render_contract)
    materials = render_contract.get('materials') or {}
    lighting = render_contract.get('lighting') or {}
    material_graph_hash = _pano_contract_hash(materials)
    lighting_hash = _pano_contract_hash(lighting)
    if req.material_graph_hash and not hmac.compare_digest(req.material_graph_hash, material_graph_hash):
        raise HTTPException(409, {'code': 'pano_material_hash_mismatch'})
    if req.lighting_hash and not hmac.compare_digest(req.lighting_hash, lighting_hash):
        raise HTTPException(409, {'code': 'pano_lighting_hash_mismatch'})
    manifest = {
        'schema_version': 1,
        'capture_id': capture_id,
        'capture_revision': capture_revision,
        'pano_id': req.pano_id,
        'projection': req.projection,
        'coordinate_system': req.coordinate_system,
        'camera_center_m': copy.deepcopy(req.camera_center_m),
        'canonical_forward': req.canonical_forward,
        'heading_deg': req.heading_deg,
        'pitch_deg': req.pitch_deg,
        'roll_deg': req.roll_deg,
        'horizontal_fov_deg': req.horizontal_fov_deg,
        'vertical_fov_deg': req.vertical_fov_deg,
        'erp_width': req.erp_width,
        'erp_height': req.erp_height,
        'cube_face_size': req.cube_face_size,
        'cube_face_order': list(req.cube_face_order),
        'near_m': req.near_m,
        'far_m': req.far_m,
        'depth_encoding': req.depth_encoding,
        'normal_encoding': req.normal_encoding,
        'model_facts_hash': req.model_facts_hash or reference_model_facts_hash(model),
        'material_graph_hash': material_graph_hash,
        'lighting_hash': lighting_hash,
        'scene_recipe_id': req.scene_recipe_id,
        'scene_hash': req.scene_hash,
        'render_contract': render_contract,
        'channels': {
            'rgb_atlas': atlas_paths.get('rgb', ''),
            'depth_atlas': atlas_paths.get('depth', ''),
            'normal_atlas': atlas_paths.get('normal', ''),
            'edge_atlas': atlas_paths.get('edge', ''),
            'semantic_atlas': atlas_paths.get('semantic', ''),
            'subject_id_atlas': atlas_paths.get('subject_id', ''),
            'rgb_erp': '', 'depth_erp': '', 'normal_erp': '',
            'edge_erp': '', 'semantic_erp': '', 'subject_id_erp': '',
        },
        'channel_hashes': {
            f'{kind}_atlas': pano_file_sha256(path)
            for kind, path in atlas_paths.items() if path and os.path.isfile(path)
        },
        'source_hash': '',
    }
    manifest['source_hash'] = pano_manifest_hash(manifest)
    return manifest


def _generate_pano_erp_channels(project_id: str, pano_id: str, atlas_paths: dict[str, str],
                                erp_width: int, erp_height: int, *,
                                capture_id: str = '', cube_face_size: int = 0) -> dict[str, str]:
    """从 3×2 atlas 确定性拆六面并转 ERP(文档 §8:只做投影,不做相机求解)。"""
    from PIL import Image

    result: dict[str, str] = {}
    for kind in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'subject_id'):
        atlas_path = atlas_paths.get(kind) or ''
        if not atlas_path or not os.path.isfile(atlas_path):
            continue
        try:
            atlas = Image.open(atlas_path)
            atlas.load()
            expected = (int(cube_face_size) * 3, int(cube_face_size) * 2)
            if cube_face_size and atlas.size != expected:
                raise ValueError(
                    f'atlas {atlas.size[0]}x{atlas.size[1]} 与 cube_face_size={cube_face_size} '
                    f'不一致(应为 {expected[0]}x{expected[1]})')
            faces = atlas_to_cube_faces(atlas, PANO_CUBE_FACE_ORDER)
            erp = cube_to_erp(
                faces, erp_width, erp_height, PANO_CUBE_FACE_ORDER,
                interpolation='nearest' if kind in {'semantic', 'subject_id'} else 'bilinear')
            result[f'{kind}_erp'] = save_pano_image_file(
                project_id, pano_id, f'{kind}_erp', erp, capture_id=capture_id)
        except Exception as ex:
            raise HTTPException(400, f'{kind} 通道 cube→ERP 失败: {ex}') from ex
    # A binary edge face projected independently on six cube faces creates
    # false great-circle seams.  Derive the authoritative edge channel after
    # depth/semantic have reached their final ERP sampling domain instead.
    if result.get('depth_erp') and result.get('semantic_erp'):
        try:
            import cv2
            import numpy as np

            depth = np.asarray(Image.open(result['depth_erp']).convert('L'))
            semantic = np.asarray(Image.open(result['semantic_erp']).convert('RGB'))
            semantic_gray = cv2.cvtColor(semantic, cv2.COLOR_RGB2GRAY)
            combined = cv2.bitwise_or(
                cv2.Canny(depth, 18, 42), cv2.Canny(semantic_gray, 8, 24))
            combined = cv2.dilate(combined, np.ones((2, 2), dtype=np.uint8))
            edge = np.full(depth.shape, 255, dtype=np.uint8)
            edge[combined > 0] = 0
            result['edge_erp'] = save_pano_image_file(
                project_id, pano_id, 'edge_erp', Image.fromarray(edge, 'L').convert('RGB'),
                capture_id=capture_id)
        except Exception as ex:
            raise HTTPException(400, f'edge 通道 ERP 后处理失败: {ex}') from ex
    return result


def _save_whole_home_pano_capture(project_id: str, req: WholeHomePanoCaptureRequest) -> dict:
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定整屋几何，再保存球面热点 capture')
    _assert_geometry_production_gate(project)
    scene_recipe = _bound_scene_recipe(project, req.scene_recipe_id, req.scene_hash)
    model = project.get('model') or {}
    camera_input = copy.deepcopy(req.camera or {})
    normalized = normalize_model({**model, 'cameras': [camera_input]}, source='human')
    if not normalized['cameras']:
        raise HTTPException(400, '热点相机参数无效')
    camera = normalized['cameras'][0]
    center_x = float((req.camera_center_m or {}).get('x') or 0)
    center_z = float((req.camera_center_m or {}).get('z') or 0)
    eye_y = float((req.camera_center_m or {}).get('y') or 0)
    if not (1.2 <= eye_y <= 2.0):
        raise HTTPException(400, '热点眼高必须在 1.2m–2.0m 范围内')
    room_id = req.room_id or infer_camera_room_id(model, camera)
    if not room_id:
        raise HTTPException(400, '无法判断热点所属房间，请把热点放在有效房间内')
    _assert_pano_hotspot_safe(project, model, req.pano_id, center_x, center_z, room_id)
    captures = project.setdefault('pano_captures', [])
    prior = [row for row in captures if str(row.get('pano_id') or '') == req.pano_id]
    capture_revision = max([int(row.get('capture_revision') or 0) for row in prior] or [0]) + 1
    capture_id = new_id('panocap')
    try:
        atlas_paths = {
            kind: save_pano_data(
                project_id, req.pano_id, f'{kind}_atlas',
                getattr(req, f'{kind}_atlas_data_url') or '', capture_id=capture_id)
            for kind in _PANO_ATLAS_KINDS
            if getattr(req, f'{kind}_atlas_data_url') or ''
        }
    except Exception as ex:
        raise HTTPException(400, f'保存全景通道失败: {ex}') from ex
    manifest = _pano_manifest_from_request(
        project, req, atlas_paths, capture_id=capture_id, capture_revision=capture_revision)
    erp_channels = _generate_pano_erp_channels(
        project_id, req.pano_id, atlas_paths, req.erp_width, req.erp_height,
        capture_id=capture_id, cube_face_size=req.cube_face_size)
    if erp_channels:
        manifest['channels'].update(erp_channels)
        manifest['channel_hashes'].update({
            key: pano_file_sha256(path)
            for key, path in erp_channels.items() if path and os.path.isfile(path)
        })
        manifest['source_hash'] = pano_manifest_hash(manifest)
    if req.source_hash and req.source_hash != manifest['source_hash']:
        raise HTTPException(409, {
            'code': 'pano_source_hash_mismatch',
            'expected': manifest['source_hash'],
        })
    capture = {
        'capture_id': capture_id, 'capture_revision': capture_revision,
        'active': True, 'pano_id': req.pano_id, 'camera': camera,
        'camera_center_m': copy.deepcopy(req.camera_center_m),
        'manifest': manifest, 'room_id': room_id,
        'scene_recipe_id': str(scene_recipe.get('recipe_id') or ''),
        'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        'semantic_legend': copy.deepcopy(req.semantic_legend),
        'subject_id_legend': copy.deepcopy(req.subject_id_legend),
        'status': 'confirmed', 'created_at': time.time(), 'created_by': req.annotator_id,
    }
    for old in prior:
        if old.get('active', True):
            old['active'] = False
            old['status'] = 'superseded'
            old['superseded_by'] = capture_id
    captures.append(capture)
    project.setdefault('operations', []).append({
        'type': 'pano_capture_3d', 'payload': {
            'pano_id': req.pano_id, 'camera_id': camera.get('id') or '',
            'scene_recipe_id': str(scene_recipe.get('recipe_id') or ''),
            'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        },
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/pano-captures')
def save_whole_home_pano_capture(project_id: str, req: WholeHomePanoCaptureRequest):
    return _save_whole_home_pano_capture(project_id, req)


@router.post('/api/whole-home/projects/{project_id}/pano-hotspots')
def save_whole_home_pano_hotspot(project_id: str, req: WholeHomePanoHotspotRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定整屋几何，再添加球面热点')
    _assert_geometry_production_gate(project)
    model = project.get('model') or {}
    camera_input = copy.deepcopy(req.camera or {})
    normalized = normalize_model({**model, 'cameras': [camera_input]}, source='human')
    if not normalized['cameras']:
        raise HTTPException(400, '热点相机参数无效')
    camera = normalized['cameras'][0]
    center_x = float((req.camera_center_m or {}).get('x') or 0)
    center_z = float((req.camera_center_m or {}).get('z') or 0)
    eye_y = float((req.camera_center_m or {}).get('y') or 0)
    if not (1.2 <= eye_y <= 2.0):
        raise HTTPException(400, '热点眼高必须在 1.2m–2.0m 范围内')
    room_id = req.room_id or infer_camera_room_id(model, camera)
    if not room_id:
        raise HTTPException(400, '无法判断热点所属房间，请把热点放在有效房间内')
    _assert_pano_hotspot_safe(project, model, req.pano_id, center_x, center_z, room_id)
    hotspot = {
        'pano_id': req.pano_id, 'camera': camera,
        'camera_center_m': copy.deepcopy(req.camera_center_m),
        'projection': 'equirectangular', 'canonical_forward': '+Z',
        'heading_deg': req.heading_deg, 'pitch_deg': req.pitch_deg, 'roll_deg': req.roll_deg,
        'room_id': room_id, 'status': 'confirmed',
        'created_at': time.time(), 'created_by': req.annotator_id,
    }
    hotspots = project.setdefault('pano_hotspots', [])
    existing = next((row for row in hotspots if str(row.get('pano_id') or '') == req.pano_id), None)
    if existing:
        existing.update(hotspot)
    else:
        hotspots.append(hotspot)
    project.setdefault('operations', []).append({
        'type': 'pano_hotspot_save', 'payload': {'pano_id': req.pano_id},
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


def _pano_capture_row(project: dict, pano_id: str, expected_hash: str) -> dict:
    rows = [row for row in project.get('pano_captures') or []
            if str(row.get('pano_id') or '') == pano_id]
    if not rows:
        raise HTTPException(404, '全景 capture 不存在')
    if expected_hash:
        rows = [row for row in rows
                if str((row.get('manifest') or {}).get('source_hash') or '') == expected_hash]
    else:
        active = [row for row in rows if row.get('active', True)]
        rows = active or rows
    if not rows:
        newest = max(
            [row for row in project.get('pano_captures') or []
             if str(row.get('pano_id') or '') == pano_id],
            key=lambda row: int(row.get('capture_revision') or 0))
        raise HTTPException(409, {
            'code': 'pano_source_hash_mismatch',
            'expected': (newest.get('manifest') or {}).get('source_hash'),
        })
    return max(rows, key=lambda row: int(row.get('capture_revision') or 0))


_PANO_REQUIRED_ERP_CHANNELS = (
    'rgb_erp', 'depth_erp', 'normal_erp', 'edge_erp', 'semantic_erp', 'subject_id_erp')


def _assert_pano_manifest_integrity(capture: dict, *, require_p0_size: bool = False) -> dict:
    manifest = capture.get('manifest') or {}
    if require_p0_size and (
            int(manifest.get('erp_width') or 0) != GPT_IMAGE_2_ERP_WIDTH
            or int(manifest.get('erp_height') or 0) != GPT_IMAGE_2_ERP_HEIGHT):
        raise HTTPException(409, {
            'code': 'pano_p0_size_required',
            'message': f'P0 付费编辑固定要求 {GPT_IMAGE_2_ERP_WIDTH}x{GPT_IMAGE_2_ERP_HEIGHT}',
        })
    channels = manifest.get('channels') or {}
    channel_hashes = manifest.get('channel_hashes') or {}
    missing = []
    for key in _PANO_REQUIRED_ERP_CHANNELS:
        path = str(channels.get(key) or '')
        expected = str(channel_hashes.get(key) or '')
        if not path or not os.path.isfile(path) or not expected:
            missing.append(key)
            continue
        if not hmac.compare_digest(pano_file_sha256(path), expected):
            raise HTTPException(409, {
                'code': 'pano_channel_hash_mismatch', 'channel': key,
                'message': f'{key} 文件内容已变化，请重新 capture',
            })
    if missing:
        raise HTTPException(409, {
            'code': 'pano_erp_channels_missing', 'channels': missing,
            'message': '六通道 ERP 不完整，请重新保存全景 capture',
        })
    actual_source_hash = pano_manifest_hash(manifest)
    if not hmac.compare_digest(actual_source_hash, str(manifest.get('source_hash') or '')):
        raise HTTPException(409, {
            'code': 'pano_manifest_hash_mismatch', 'expected': actual_source_hash,
        })
    return manifest


def _pano_erp_channel_paths(project_id: str, capture: dict) -> list[str]:
    channels = (capture.get('manifest') or {}).get('channels') or {}
    paths = []
    # 顺序与 whole_home_pano_edit.build_erp_edit_prompt 的 Image 1..6 严格一致。
    for kind in ('rgb_erp', 'depth_erp', 'normal_erp', 'edge_erp',
                 'semantic_erp', 'subject_id_erp'):
        path = channels.get(kind) or ''
        if path and os.path.isfile(path):
            paths.append(path)
    return paths


def _pano_generation_targets(project: dict, capture: dict) -> list[str]:
    """Return only explicitly unresolved movable roles for this room."""
    room_id = str(capture.get('room_id') or '')
    contract = next((row for row in (project.get('model') or {}).get('room_contracts') or []
                     if str(row.get('room_id') or '') == room_id), {})
    targets = []
    for group in contract.get('missing_role_groups') or []:
        roles = [str(value).strip() for value in group or [] if str(value).strip()]
        if roles:
            # A group is an OR contract.  Picking one stable representative
            # avoids asking the image model to create every synonym.
            targets.append(roles[0])
    return sorted(set(targets))


_PANO_WHOLE_HOME_APPEARANCE_CONTRACT = (
    'warm contemporary minimal home; the same warm-ivory matte wall paint, light natural-oak '
    'flooring, warm-greige built-ins, restrained walnut and charcoal accents, warm-white textiles, '
    'and neutral daylight around 4000K in every room; realistic residential scale, no hotel-lobby '
    'luxury, no mirror-polished marble, no fisheye lens effect'
)

_PANO_FLUX_CANNY_PARAMS = {
    'core_width': FLUX_CANNY_ERP_CORE_WIDTH,
    'core_height': FLUX_CANNY_ERP_CORE_HEIGHT,
    'gutter_px': FLUX_CANNY_ERP_GUTTER_PX,
    'provider_width': FLUX_CANNY_ERP_PROVIDER_WIDTH,
    'provider_height': FLUX_CANNY_ERP_PROVIDER_HEIGHT,
    'seed': 24681357,
    'strength': .82,
    'control_lora_strength': 1.25,
    'num_inference_steps': 32,
    'guidance_scale': 3.0,
    # Fal currently bills this 1536x704 canvas as two rounded megapixels.
    'estimated_cost_usd': .08,
}


def _pano_consistency_references(project: dict, capture: dict) -> list[tuple[str, str]]:
    """Bind at most two previously accepted hotspot outputs as appearance-only refs."""
    current_id = str(capture.get('capture_id') or '')
    rows = []
    for row in project.get('pano_captures') or []:
        if str(row.get('capture_id') or '') == current_id:
            continue
        review = row.get('human_review') or {}
        gate = row.get('gate') or {}
        if not review.get('accepted') or not gate.get('gate_pass'):
            continue
        path = str(row.get('repaired_rgb_path') or row.get('edited_rgb_path') or '')
        if not path or not os.path.isfile(path):
            continue
        expected = str(review.get('candidate_sha256') or '')
        actual = pano_file_sha256(path)
        if not expected or not hmac.compare_digest(actual, expected):
            continue
        rows.append((float(review.get('reviewed_at') or 0), path, actual))
    rows.sort(key=lambda value: (-value[0], value[2]))
    return [(path, digest) for _, path, digest in rows[:2]]


def _pano_preview_consistency_paths(preview: dict) -> list[str]:
    paths = [str(value) for value in preview.get('consistency_reference_paths') or []]
    hashes = [str(value) for value in preview.get('consistency_reference_hashes') or []]
    if len(paths) != len(hashes) or len(paths) > 10:
        raise HTTPException(409, {'code': 'pano_consistency_reference_contract_invalid'})
    for path, expected in zip(paths, hashes):
        if (not path or not os.path.isfile(path) or not expected
                or not hmac.compare_digest(pano_file_sha256(path), expected)):
            raise HTTPException(409, {
                'code': 'pano_consistency_reference_hash_mismatch',
                'message': '跨点位风格参考已变化；不会执行已确认的付费请求',
            })
    return paths


def _begin_pano_call(project: dict, capture: dict, preview: dict, kind: str) -> dict:
    capture_id = str(capture.get('capture_id') or '')
    prior = next((row for row in project.get('pano_calls') or []
                  if str(row.get('capture_id') or '') == capture_id
                  and str(row.get('kind') or '') == kind), None)
    if prior:
        # claimed/submitted/failed 也可能已经在 provider 侧计费，不能自动重放。
        raise HTTPException(409, {
            'code': f'pano_{kind}_cap_exhausted',
            'message': f'此 capture 的 {kind} 付费调用上限已用完',
            'call_id': prior.get('call_id'), 'status': prior.get('status'),
        })
    row = {
        'call_id': new_id('panocall'), 'preview_id': preview.get('preview_id'),
        'capture_id': capture.get('capture_id'), 'pano_id': capture.get('pano_id'),
        'kind': kind, 'provider': preview.get('provider'),
        'endpoint': preview.get('endpoint'), 'model_id': preview.get('model_id'),
        'snapshot_locked': bool(preview.get('snapshot_locked')),
        'source_hash': (capture.get('manifest') or {}).get('source_hash') or '',
        'status': 'claimed', 'success': False, 'error': '', 'at': time.time(),
        'extra': {'output_size_requested': preview.get('output_size')},
    }
    project.setdefault('pano_calls', []).append(row)
    _persist_project(project)
    return row


def _finish_pano_call(project: dict, row: dict, *, success: bool, error: str,
                      extra: Optional[dict] = None) -> None:
    row['success'] = bool(success)
    row['status'] = 'succeeded' if success else 'failed'
    row['error'] = str(error or '')
    row['finished_at'] = time.time()
    row.setdefault('extra', {}).update(extra or {})
    _persist_project(project)


def _pano_call_resume_eligible(call: Optional[dict]) -> bool:
    """True only when querying the same durable Fal request can recover output.

    Geometry/model contract failures (for example a wrong provider size) are
    deliberately excluded: polling them again cannot produce a different file.
    """
    if not isinstance(call, dict) or int(call.get('resume_attempts') or 0) >= 3:
        return False
    handle = call.get('queue_handle') or {}
    if (not str(call.get('request_id') or '')
            or not str(handle.get('status_url') or '').startswith('https://queue.fal.run/')
            or not str(handle.get('response_url') or '').startswith('https://queue.fal.run/')):
        return False
    if str(call.get('status') or '') in {'submitted', 'resuming'}:
        return True
    error = str(call.get('error') or '')
    return str(call.get('status') or '') == 'failed' and error.startswith((
        '解码失败:', '队列状态网络错误:', 'Fal 队列等待超时',
        '队列取结果 HTTP 5',
    ))


def _restore_pano_resume_preview(project: dict, req: WholeHomePanoEditRequest,
                                 project_id: str, pano_id: str) -> dict:
    row = next((item for item in project.get('pano_paid_previews') or []
                if str(item.get('preview_id') or '') == req.preview_id), None)
    if not row:
        raise HTTPException(409, {'code': 'pano_paid_preview_missing'})
    try:
        restore_pano_paid_preview(row)
    except ValueError as ex:
        raise HTTPException(409, {'code': str(ex)}) from ex
    comparisons = (
        (row.get('confirmation_phrase'), req.confirmation_phrase),
        (row.get('project_id'), project_id), (row.get('pano_id'), pano_id),
        (row.get('source_hash'), req.source_hash),
    )
    if (not row.get('edit_claimed') or any(not hmac.compare_digest(
            str(left or '').encode('utf-8'), str(right or '').encode('utf-8'))
            for left, right in comparisons)):
        raise HTTPException(409, {'code': 'pano_paid_resume_contract_mismatch'})
    return copy.deepcopy(row)


def _managed_output_relative_path(path: str) -> str:
    """只允许历史记录引用 MAIN_OUTPUT_DIR 内的托管文件。"""
    try:
        root = os.path.realpath(MAIN_OUTPUT_DIR)
        candidate = os.path.realpath(str(path or ''))
        if not candidate or not os.path.isfile(candidate):
            return ''
        if os.path.commonpath([root, candidate]) != root:
            return ''
        return os.path.relpath(candidate, root).replace('\\', '/')
    except (OSError, ValueError):
        return ''


def _archive_pano_gate_record(project: dict, capture: dict, candidate_path: str,
                              gate: dict) -> Optional[dict]:
    """把全景候选写入通用历史页；按候选 hash + gate 版本幂等。

    记录被标为 immutable_audit，前端不得从这里触发二改、inpaint 或删除，
    避免绕开逐 capture 的付费确认与调用上限。
    """
    candidate_rel = _managed_output_relative_path(candidate_path)
    if not candidate_rel:
        # 单测临时文件和外部路径不进入正式历史；生产 capture 必须在托管目录。
        return None
    manifest = capture.get('manifest') or {}
    reference_path = str((manifest.get('channels') or {}).get('rgb_erp') or '')
    reference_rel = _managed_output_relative_path(reference_path)
    candidate_sha = pano_file_sha256(candidate_path)
    capture_id = str(capture.get('capture_id') or manifest.get('capture_id') or '')
    gate_version = str(gate.get('version') or 'unknown')
    history_key = ':'.join((str(project.get('project_id') or ''), capture_id,
                            candidate_sha, gate_version))
    call = next((copy.deepcopy(row) for row in reversed(project.get('pano_calls') or [])
                 if str(row.get('capture_id') or '') == capture_id
                 and row.get('kind') in {'edit', 'repair'}), {})
    failures = [str(value) for value in gate.get('failures') or []]
    passed = bool(gate.get('gate_pass'))
    now = time.time()
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
    result_id = f'panoresult_{candidate_sha[:16]}'
    record_id = f'panoaudit_{capture_id}_{candidate_sha[:12]}'
    note = ('自动 P0 RGB/结构门禁通过，等待 Viewer 人工验收'
            if passed else f"自动 P0 RGB/结构门禁未通过：{','.join(failures) or 'unknown'}")
    tags = []
    if {'wrap_seam', 'cube_edges'} & set(failures):
        tags.append('球面接缝')
    if {'structure_views', 'opening_identity'} & set(failures):
        tags.append('结构漂移')
    row = {
        'id': record_id,
        'timestamp': timestamp,
        'workflow_mode': '定点球面全景（只读审计）',
        'room_type': str(capture.get('room_id') or '全景热点'),
        'property_type': '', 'style': '', 'location': '', 'seam': '',
        'params_summary': (
            f"{manifest.get('erp_width')}x{manifest.get('erp_height')} · "
            f"{call.get('provider') or 'unknown'} · {call.get('model_id') or 'unknown'} · "
            f"P0 {'pass' if passed else 'fail'}"),
        '_schema_version': 2,
        'immutable_audit': True,
        'pano_history_key': history_key,
        'gen_context': {
            'room_path': reference_path if reference_rel else '',
        },
        'pano_audit': {
            'project_id': project.get('project_id'), 'pano_id': capture.get('pano_id'),
            'capture_id': capture_id, 'source_hash': manifest.get('source_hash'),
            'projection': manifest.get('projection') or 'equirectangular',
            'erp_width': int(manifest.get('erp_width') or 0),
            'erp_height': int(manifest.get('erp_height') or 0),
            'canonical_forward': manifest.get('canonical_forward') or '+Z',
            'heading_deg': float(manifest.get('heading_deg') or 0),
            'candidate_sha256': candidate_sha, 'gate': copy.deepcopy(gate),
            'provider_call': call,
        },
        'results': [{
            'result_id': result_id, 'result_image_file': candidate_rel,
            'model': call.get('model_id') or 'gpt-image-2',
            'model_label': f"{call.get('model_id') or 'unknown'} / {call.get('provider') or 'unknown'}",
            'comment': note, 'favorite': False, 'best': False,
            'review_status': 'unreviewed' if passed else 'rejected',
            'review_tags': tags, 'review_note': note,
            'result_timestamp': timestamp,
        }],
    }
    folder = os.path.join(MAIN_OUTPUT_DIR, '定点球面全景')
    os.makedirs(folder, exist_ok=True)
    json_path = os.path.join(folder, '定点球面全景_记录.json')
    with record_file_lock(json_path):
        records = load_records_file(json_path)
        existing = next((item for item in records if isinstance(item, dict)
                         and item.get('pano_history_key') == history_key), None)
        if not existing:
            records.append(row)
            save_records_file(json_path, records)
        else:
            record_id = str(existing.get('id') or record_id)
    return {'json_path': json_path, 'record_id': record_id,
            'result_id': result_id, 'archived': True}


def _claim_pano_preview(req: WholeHomePanoEditRequest, *, stage: str,
                        project_id: str, pano_id: str, project: dict) -> dict:
    if str(req.pano_id) != str(pano_id):
        raise HTTPException(400, {'code': 'pano_path_body_mismatch'})
    try:
        persisted = next((row for row in project.get('pano_paid_previews') or []
                          if str(row.get('preview_id') or '') == req.preview_id), None)
        if persisted:
            restore_pano_paid_preview(persisted)
        claimed = claim_pano_paid_stage(
            req.preview_id, req.confirmation_phrase, stage=stage,
            project_id=project_id, pano_id=pano_id, source_hash=req.source_hash)
        rows = project.setdefault('pano_paid_previews', [])
        rows[:] = [row for row in rows if str(row.get('preview_id') or '') != req.preview_id]
        rows.append(copy.deepcopy(claimed))
        _persist_project(project)
        return claimed
    except ValueError as ex:
        raise HTTPException(409, {'code': str(ex), 'message': '付费预览确认失效，请重新预览'}) from ex


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/materialize')
def materialize_whole_home_pano(project_id: str, pano_id: str,
                                req: WholeHomePanoMaterializeRequest):
    """Free deterministic material pass; geometry channels remain authoritative."""
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    _assert_geometry_production_gate(project)
    capture = _pano_capture_row(project, pano_id, req.source_hash)
    manifest = _assert_pano_manifest_integrity(capture, require_p0_size=True)
    if str(capture.get('edited_rgb_path') or '') or any(
            str(row.get('capture_id') or '') == str(capture.get('capture_id') or '')
            and str(row.get('kind') or '') == 'edit'
            for row in project.get('pano_calls') or []):
        raise HTTPException(409, {
            'code': 'pano_edit_cap_exhausted',
            'message': '此 capture 已有候选；请新建 capture，不能覆盖已有审计证据',
        })
    channels = manifest.get('channels') or {}
    try:
        from PIL import Image

        required = ('rgb_erp', 'depth_erp', 'normal_erp', 'edge_erp', 'semantic_erp')
        images = {}
        for key in required:
            path = str(channels.get(key) or '')
            image = Image.open(path)
            image.load()
            images[key] = image
        # The deterministic engine has a stronger pixel-exact identity-grid
        # replay proof, so retain only a one-pixel-ish architectural sample
        # band here.  The wider 0.5 degree band remains mandatory for opaque
        # generative providers, where no such spatial proof exists.
        holdout = build_structure_holdout_mask(images['edge_erp'], protection_deg=.08)
        holdout_path = save_pano_image_file(
            project_id, pano_id, 'structure_holdout_mask', holdout,
            capture_id=str(capture.get('capture_id') or ''))
        candidate = materialize_geometry_locked_erp(
            images['rgb_erp'], images['depth_erp'], images['normal_erp'],
            images['semantic_erp'], manifest, holdout_mask=holdout,
            preset=req.preset)
        if candidate.size != (int(manifest.get('erp_width') or 0),
                              int(manifest.get('erp_height') or 0)):
            raise ValueError('geometry_material_output_size_mismatch')
        edited_path = save_pano_image_file(
            project_id, pano_id, 'materialized_rgb', candidate,
            capture_id=str(capture.get('capture_id') or ''))
    except Exception as ex:
        raise HTTPException(409, {
            'code': 'pano_geometry_material_failed', 'message': str(ex),
        }) from ex
    capture['structure_holdout_mask_path'] = holdout_path
    capture['structure_holdout_mask_sha256'] = pano_file_sha256(holdout_path)
    capture['edited_rgb_path'] = edited_path
    capture['edit_engine'] = MATERIAL_ENGINE_VERSION
    capture['edited_at'] = time.time()
    capture['status'] = 'edited'
    call = {
        'call_id': new_id('panocall'), 'preview_id': '',
        'capture_id': capture.get('capture_id'), 'pano_id': pano_id,
        'kind': 'edit', 'provider': 'local', 'endpoint': MATERIAL_ENGINE_VERSION,
        'model_id': MATERIAL_ENGINE_VERSION, 'snapshot_locked': True,
        'source_hash': manifest.get('source_hash'), 'status': 'succeeded',
        'success': True, 'error': '', 'at': time.time(), 'finished_at': time.time(),
        'extra': {
            'cost_usd': 0.0, 'preset': req.preset,
            'output_size': list(candidate.size),
            'output_sha256': pano_file_sha256(edited_path),
            'holdout_sha256': pano_file_sha256(holdout_path),
            'geometry_locked': True,
        },
    }
    project.setdefault('pano_calls', []).append(call)
    project.setdefault('operations', []).append({
        'type': 'pano_materialize', 'payload': {
            'pano_id': pano_id, 'engine': MATERIAL_ENGINE_VERSION,
            'preset': req.preset, 'call_id': call['call_id']},
        'at': time.time(), 'revision': project.get('revision', 0),
        'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/paid-preview')
def preview_whole_home_pano_edit(project_id: str, pano_id: str,
                                 req: WholeHomePanoPaidPreviewRequest):
    """只创建可审计确认短语，绝不调用 provider。"""
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    _assert_geometry_production_gate(project)
    capture = _pano_capture_row(project, pano_id, req.source_hash)
    manifest = _assert_pano_manifest_integrity(capture, require_p0_size=True)
    capture_id = str(capture.get('capture_id') or '')
    prior_edit = next((row for row in project.get('pano_calls') or []
                       if str(row.get('capture_id') or '') == capture_id
                       and str(row.get('kind') or '') == 'edit'), None)
    if prior_edit:
        # 浏览器/服务重启后，允许免费恢复执行 edit 时的原确认合同，供唯一一次
        # 条件式 repair 使用。这里绝不生成新 preview，也不恢复 edit 额度。
        persisted = next((row for row in project.get('pano_paid_previews') or []
                          if str(row.get('source_hash') or '') == req.source_hash
                          and str(row.get('project_id') or '') == project_id
                          and str(row.get('pano_id') or '') == pano_id
                          and row.get('edit_claimed') and not row.get('repair_claimed')), None)
        if persisted and _pano_call_resume_eligible(prior_edit):
            try:
                restore_pano_paid_preview(persisted)
                public = public_pano_paid_preview(persisted)
                public['resume_only'] = True
                public['resume_request_id'] = prior_edit.get('request_id')
                return public
            except ValueError as ex:
                raise HTTPException(409, {
                    'code': str(ex),
                    'message': '原付费合同无法安全恢复；不会查询已有 provider 请求',
                }) from ex
        if persisted and str(persisted.get('engine') or 'gpt-image-2') == 'flux-canny':
            raise HTTPException(409, {
                'code': 'pano_repair_engine_unsupported',
                'message': 'FLUX Canny 使用环形 gutter，一次 edit 已包含接缝上下文，不开放第二次付费修缝',
            })
        if (persisted and prior_edit.get('status') == 'succeeded'
                and str(capture.get('edited_rgb_path') or '')):
            try:
                restore_pano_paid_preview(persisted)
                if time.time() > float(persisted.get('expires_at') or 0):
                    raise ValueError('pano_paid_preview_expired')
                return public_pano_paid_preview(persisted)
            except ValueError as ex:
                raise HTTPException(409, {
                    'code': str(ex),
                    'message': '原付费预览无法安全恢复，repair 不会执行',
                }) from ex
        raise HTTPException(409, {
            'code': 'pano_edit_cap_exhausted',
            'message': '此 capture 已经认领过唯一一次 edit；不会创建可再次付费的预览',
            'call_id': prior_edit.get('call_id'), 'status': prior_edit.get('status'),
        })
    prior_claim = next((row for row in project.get('pano_paid_previews') or []
                        if str(row.get('source_hash') or '') == req.source_hash
                        and row.get('edit_claimed')), None)
    if prior_claim:
        raise HTTPException(409, {
            'code': 'pano_edit_cap_exhausted',
            'message': '此 capture 已认领过唯一一次 edit；即使提交前重启也不会重放',
        })
    cfg = load_config()
    engine = str(req.engine or 'gpt-image-2')
    generation_params = {}
    consistency_references = []
    if engine == 'flux-canny':
        if req.provider != 'fal':
            raise HTTPException(400, {
                'code': 'flux_canny_requires_fal',
                'message': 'FLUX Canny 当前只允许显式 fal provider，禁止隐式跨 provider 回退',
            })
        if not str(cfg.get('fal_api_key') or '').strip():
            raise HTTPException(400, {'code': 'fal_api_key_missing'})
        endpoint = str(cfg.get('fal_flux_canny_erp_endpoint')
                       or FAL_FLUX_CANNY_ERP_ENDPOINT)
        model_id = FLUX_CANNY_ERP_MODEL
        generation_params = copy.deepcopy(_PANO_FLUX_CANNY_PARAMS)
        output_size = (
            f'{FLUX_CANNY_ERP_PROVIDER_WIDTH}x{FLUX_CANNY_ERP_PROVIDER_HEIGHT}')
        prompt = build_flux_canny_erp_prompt(
            manifest, req.style_description,
            generation_targets=_pano_generation_targets(project, capture),
            consistency_contract=_PANO_WHOLE_HOME_APPEARANCE_CONTRACT,
            gutter_px=FLUX_CANNY_ERP_GUTTER_PX,
            core_width=FLUX_CANNY_ERP_CORE_WIDTH)
    elif req.provider == 'fal':
        if not str(cfg.get('fal_api_key') or '').strip():
            raise HTTPException(400, {'code': 'fal_api_key_missing'})
        endpoint = str(cfg.get('fal_gpt_image_endpoint') or FAL_GPT_IMAGE_2_ENDPOINT)
        model_id = 'gpt-image-2'
        output_size = f'{GPT_IMAGE_2_ERP_WIDTH}x{GPT_IMAGE_2_ERP_HEIGHT}'
        consistency_references = _pano_consistency_references(project, capture)
        prompt = build_erp_edit_prompt(
            manifest, req.style_description,
            generation_targets=_pano_generation_targets(project, capture),
            consistency_contract=_PANO_WHOLE_HOME_APPEARANCE_CONTRACT,
            appearance_reference_count=len(consistency_references))
    else:
        if not str(cfg.get('openai_api_key') or '').strip():
            raise HTTPException(400, {'code': 'openai_api_key_missing'})
        endpoint = OPENAI_IMAGE_EDITS_URL
        model_id = req.model_id
        output_size = f'{GPT_IMAGE_2_ERP_WIDTH}x{GPT_IMAGE_2_ERP_HEIGHT}'
        consistency_references = _pano_consistency_references(project, capture)
        prompt = build_erp_edit_prompt(
            manifest, req.style_description,
            generation_targets=_pano_generation_targets(project, capture),
            consistency_contract=_PANO_WHOLE_HOME_APPEARANCE_CONTRACT,
            appearance_reference_count=len(consistency_references))
    if req.edit_instruction:
        prompt += f'\n\nAdditional appearance-only instruction: {req.edit_instruction}'
    public = create_pano_paid_preview(
        project_id=project_id, pano_id=pano_id, source_hash=req.source_hash,
        provider=req.provider, endpoint=endpoint, model_id=model_id,
        output_size=output_size,
        edit_prompt=prompt, repair_band_deg=req.repair_band_deg,
        actor=req.annotator_id,
        consistency_reference_paths=[row[0] for row in consistency_references],
        consistency_reference_hashes=[row[1] for row in consistency_references],
        engine=engine, generation_params=generation_params)
    project.setdefault('pano_paid_previews', []).append(
        persistable_pano_paid_preview(public['preview_id']))
    _persist_project(project)
    return public


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/edit')
def edit_whole_home_pano(project_id: str, pano_id: str, req: WholeHomePanoEditRequest):
    """整张 ERP 一次性编辑；引擎和所有计费参数由付费预览签名绑定。

    付费保护:manual-safe 且未 -AllowPaid 时拒绝(与 manual commit 同政策)。
    修缝不在此端点:gate 判定 wrap seam 失败后由 repair 端点做唯一一次受控修补。
    """
    if manual_safe_enabled() and not manual_paid_enabled():
        raise HTTPException(402, {
            'code': 'pano_edit_paid_disabled',
            'message': 'manual-safe 模式已关闭付费调用;请用 -AllowPaid 启动后重试',
        })
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    _assert_geometry_production_gate(project)
    capture = _pano_capture_row(project, pano_id, req.source_hash)
    manifest = _assert_pano_manifest_integrity(capture, require_p0_size=True)
    prior_edit = next((row for row in project.get('pano_calls') or []
                       if str(row.get('capture_id') or '') == str(capture.get('capture_id') or '')
                       and str(row.get('kind') or '') == 'edit'), None)
    resume_mode = _pano_call_resume_eligible(prior_edit)
    if resume_mode:
        preview = _restore_pano_resume_preview(project, req, project_id, pano_id)
    elif prior_edit:
        raise HTTPException(409, {
            'code': 'pano_edit_cap_exhausted',
            'message': '已有 edit 不是可恢复的同一队列请求；不会提交第二次付费调用',
            'call_id': prior_edit.get('call_id'), 'status': prior_edit.get('status'),
        })
    else:
        preview = _claim_pano_preview(
            req, stage='edit', project_id=project_id, pano_id=pano_id, project=project)
    erp_width = int(manifest.get('erp_width') or 0)
    erp_height = int(manifest.get('erp_height') or 0)
    if erp_width != erp_height * 2:
        raise HTTPException(409, {'code': 'pano_erp_not_2_1'})
    channel_paths = _pano_erp_channel_paths(project_id, capture)
    if len(channel_paths) != 6 or not (manifest.get('channels') or {}).get('rgb_erp'):
        raise HTTPException(409, {'code': 'pano_erp_channels_missing',
                                  'message': 'ERP 通道不完整,请重新保存全景 capture'})
    engine = str(preview.get('engine') or 'gpt-image-2')
    if engine == 'gpt-image-2':
        channel_paths.extend(_pano_preview_consistency_paths(preview))
    prompt = str(preview.get('edit_prompt') or '')
    try:
        from PIL import Image

        edge_path = str((manifest.get('channels') or {}).get('edge_erp') or '')
        edge_image = Image.open(edge_path)
        edge_image.load()
        holdout_mask = build_structure_holdout_mask(edge_image, protection_deg=.5)
        holdout_mask_path = save_pano_image_file(
            project_id, req.pano_id, 'structure_holdout_mask', holdout_mask,
            capture_id=str(capture.get('capture_id') or ''))
        capture['structure_holdout_mask_path'] = holdout_mask_path
        capture['structure_holdout_mask_sha256'] = pano_file_sha256(holdout_mask_path)
        _persist_project(project)
    except Exception as ex:
        raise HTTPException(409, {
            'code': 'pano_structure_holdout_mask_failed', 'message': str(ex),
        }) from ex
    flux_inputs = None
    if engine == 'flux-canny':
        params = dict(preview.get('generation_params') or {})
        try:
            rgb_path = str((manifest.get('channels') or {}).get('rgb_erp') or '')
            with Image.open(rgb_path) as source_rgb, Image.open(edge_path) as source_edge:
                flux_rgb, flux_control = prepare_flux_canny_inputs(
                    source_rgb, source_edge,
                    core_width=int(params.get('core_width') or 0),
                    core_height=int(params.get('core_height') or 0),
                    gutter_px=int(params.get('gutter_px') or 0))
            flux_rgb_path = save_pano_image_file(
                project_id, req.pano_id, 'flux_rgb_input', flux_rgb,
                capture_id=str(capture.get('capture_id') or ''))
            flux_control_path = save_pano_image_file(
                project_id, req.pano_id, 'flux_canny_control', flux_control,
                capture_id=str(capture.get('capture_id') or ''))
            flux_inputs = (flux_rgb_path, flux_control_path)
            capture['flux_canny_inputs'] = {
                'rgb_path': flux_rgb_path,
                'rgb_sha256': pano_file_sha256(flux_rgb_path),
                'control_path': flux_control_path,
                'control_sha256': pano_file_sha256(flux_control_path),
                'params': copy.deepcopy(params),
            }
            _persist_project(project)
        except Exception as ex:
            raise HTTPException(409, {
                'code': 'pano_flux_canny_input_failed', 'message': str(ex),
            }) from ex
    elif engine != 'gpt-image-2':
        raise HTTPException(409, {'code': 'pano_paid_engine_unknown', 'engine': engine})
    if resume_mode:
        call = prior_edit
        call['resume_attempts'] = int(call.get('resume_attempts') or 0) + 1
        call['status'] = 'resuming'
        call['error'] = ''
        call['resumed_at'] = time.time()
        _persist_project(project)
    else:
        call = _begin_pano_call(project, capture, preview, 'edit')

    def on_submitted(handle):
        call['queue_handle'] = copy.deepcopy(handle)
        call['request_id'] = str(handle.get('request_id') or '')
        call['status'] = 'submitted'
        _persist_project(project)

    try:
        if engine == 'flux-canny':
            params = dict(preview.get('generation_params') or {})
            image, error = call_fal_flux_canny_edit(
                '', prompt, flux_inputs[0], flux_inputs[1],
                size=str(preview.get('output_size') or ''),
                seed=int(params.get('seed') or 0),
                strength=float(params.get('strength') or 0),
                control_lora_strength=float(params.get('control_lora_strength') or 0),
                num_inference_steps=int(params.get('num_inference_steps') or 0),
                guidance_scale=float(params.get('guidance_scale') or 0),
                endpoint=str(preview.get('endpoint') or ''), on_submitted=on_submitted,
                resume_handle=copy.deepcopy(call.get('queue_handle') or {}) if resume_mode else None)
            if image is not None:
                provider_output_path = save_pano_image_file(
                    project_id, req.pano_id, 'flux_provider_output', image,
                    capture_id=str(capture.get('capture_id') or ''))
                capture['flux_canny_provider_output_path'] = provider_output_path
                capture['flux_canny_provider_output_sha256'] = pano_file_sha256(
                    provider_output_path)
                image = finalize_flux_canny_output(
                    image, target_width=erp_width, target_height=erp_height,
                    core_width=int(params.get('core_width') or 0),
                    core_height=int(params.get('core_height') or 0),
                    gutter_px=int(params.get('gutter_px') or 0))
        else:
            image, error = call_gpt_image_edit(
                '', prompt, channel_paths, model_id=str(preview.get('model_id') or ''),
                size=f'{erp_width}x{erp_height}', provider=str(preview.get('provider') or 'fal'),
                endpoint=str(preview.get('endpoint') or ''), on_submitted=on_submitted,
                mask_image_path=holdout_mask_path,
                resume_handle=copy.deepcopy(call.get('queue_handle') or {}) if resume_mode else None)
    except Exception as ex:
        _finish_pano_call(project, call, success=False, error=str(ex))
        raise HTTPException(502, f'{engine} 编辑异常: {ex}') from ex
    if image is None:
        _finish_pano_call(project, call, success=False, error=str(error or ''))
        raise HTTPException(502, f'{engine} 编辑失败: {error}')
    width, height = image.size
    if width != erp_width or height != erp_height or width != height * 2:
        _finish_pano_call(
            project, call, success=False, error=f'输出尺寸 {width}x{height} 违反 2:1 契约',
            extra={'output_size': [width, height]})
        raise HTTPException(502, {
            'code': 'pano_erp_size_contract_broken',
            'message': f'模型输出 {width}x{height} 不是 {erp_width}x{erp_height},已拒绝',
        })
    edited_path = save_pano_image_file(
        project_id, req.pano_id, 'edited_rgb', image,
        capture_id=str(capture.get('capture_id') or ''))
    capture['edited_rgb_path'] = edited_path
    capture['edit_engine'] = engine
    capture['edited_at'] = time.time()
    capture['status'] = 'edited'
    call_extra = {
        'output_size': [width, height], 'output_sha256': pano_file_sha256(edited_path),
    }
    if engine == 'flux-canny':
        call_extra.update({
            'provider_output_sha256': capture.get('flux_canny_provider_output_sha256'),
            'input_rgb_sha256': (capture.get('flux_canny_inputs') or {}).get('rgb_sha256'),
            'control_sha256': (capture.get('flux_canny_inputs') or {}).get('control_sha256'),
            'generation_params': copy.deepcopy(preview.get('generation_params') or {}),
        })
    _finish_pano_call(project, call, success=True, error='', extra=call_extra)
    project.setdefault('operations', []).append({
        'type': 'pano_edit', 'payload': {
            'pano_id': req.pano_id, 'model_id': preview.get('model_id'),
            'engine': engine, 'provider': preview.get('provider'),
            'preview_id': req.preview_id},
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/repair')
def repair_whole_home_pano_seam(project_id: str, pano_id: str, req: WholeHomePanoEditRequest):
    """环形移位修缝(文档 §7.5):原接缝移到中央,窄带 mask 编辑一次,再移回。

    失败不回退多轮:直接返回错误,由调用方决定是否丢弃候选。
    """
    if manual_safe_enabled() and not manual_paid_enabled():
        raise HTTPException(402, {
            'code': 'pano_edit_paid_disabled',
            'message': 'manual-safe 模式已关闭付费调用;请用 -AllowPaid 启动后重试',
        })
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    persisted_preview = next((row for row in project.get('pano_paid_previews') or []
                              if str(row.get('preview_id') or '') == req.preview_id), None)
    if (persisted_preview
            and str(persisted_preview.get('engine') or 'gpt-image-2') == 'flux-canny'):
        raise HTTPException(409, {
            'code': 'pano_repair_engine_unsupported',
            'message': 'FLUX Canny 结果不允许进入 GPT mask 修缝链路',
        })
    _assert_geometry_production_gate(project)
    capture = _pano_capture_row(project, req.pano_id, req.source_hash)
    edited_path = capture.get('edited_rgb_path') or ''
    if not edited_path or not os.path.isfile(edited_path):
        raise HTTPException(409, {'code': 'pano_edited_missing', 'message': '请先执行 edit'})
    manifest = _assert_pano_manifest_integrity(capture, require_p0_size=True)
    gate = capture.get('gate') or {}
    failures = set(gate.get('failures') or [])
    if gate.get('gate_pass') or not failures or not failures.issubset({'wrap_seam', 'cube_edges'}):
        raise HTTPException(409, {
            'code': 'pano_repair_not_eligible',
            'message': '仅当上一次 gate 只失败 wrap_seam/cube_edges 时允许一次修缝',
        })
    preview = _claim_pano_preview(
        req, stage='repair', project_id=project_id, pano_id=pano_id, project=project)
    erp_width = int(manifest.get('erp_width') or 0)
    erp_height = int(manifest.get('erp_height') or 0)
    try:
        from PIL import Image

        edited = Image.open(edited_path)
        edited.load()
        shifted = circular_shift_erp(edited, erp_width // 2)
        shifted_path = save_pano_image_file(
            project_id, req.pano_id, 'repair_shifted', shifted,
            capture_id=str(capture.get('capture_id') or ''))
        mask = build_seam_repair_mask(
            erp_width, erp_height, float(preview.get('repair_band_deg') or 12))
        mask_path = save_pano_image_file(
            project_id, req.pano_id, 'repair_mask', mask,
            capture_id=str(capture.get('capture_id') or ''))
        prompt = build_seam_repair_prompt()
        call = _begin_pano_call(project, capture, preview, 'repair')

        def on_submitted(handle):
            call['queue_handle'] = copy.deepcopy(handle)
            call['request_id'] = str(handle.get('request_id') or '')
            call['status'] = 'submitted'
            _persist_project(project)

        image, error = call_gpt_image_edit(
            '', prompt, [shifted_path], mask_image_path=mask_path,
            model_id=str(preview.get('model_id') or ''), size=f'{erp_width}x{erp_height}',
            provider=str(preview.get('provider') or 'fal'), endpoint=str(preview.get('endpoint') or ''),
            on_submitted=on_submitted)
    except Exception as ex:
        if 'call' in locals():
            _finish_pano_call(project, call, success=False, error=str(ex))
        raise HTTPException(502, f'修缝异常: {ex}') from ex
    if image is None:
        _finish_pano_call(project, call, success=False, error=str(error or ''))
        raise HTTPException(502, f'修缝失败: {error}')
    width, height = image.size
    if width != erp_width or height != erp_height:
        _finish_pano_call(project, call, success=False,
                          error=f'修缝输出 {width}x{height} 违反契约')
        raise HTTPException(502, {
            'code': 'pano_erp_size_contract_broken',
            'message': f'修缝输出 {width}x{height} 不是 {erp_width}x{erp_height},已拒绝',
        })
    restored = circular_shift_erp(image, -erp_width // 2)
    repaired_path = save_pano_image_file(
        project_id, req.pano_id, 'repaired_rgb', restored,
        capture_id=str(capture.get('capture_id') or ''))
    capture['repaired_rgb_path'] = repaired_path
    capture['repaired_at'] = time.time()
    capture['status'] = 'repaired'
    _finish_pano_call(project, call, success=True, error='', extra={
        'output_size': [width, height], 'output_sha256': pano_file_sha256(repaired_path)})
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/gate')
def gate_whole_home_pano(project_id: str, pano_id: str, req: WholeHomePanoGateRequest):
    """球面硬门禁(文档 §9):本地计算,无付费调用。

    候选顺序:repaired_rgb > edited_rgb;参考通道来自 clay 渲染的确定性 ERP。
    失败项给出 check_id,由调用方决定一次受控修缝(repair)或丢弃候选。
    """
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    capture = _pano_capture_row(project, pano_id, req.source_hash)
    candidate_path = capture.get('repaired_rgb_path') or capture.get('edited_rgb_path') or ''
    if not candidate_path or not os.path.isfile(candidate_path):
        raise HTTPException(409, {'code': 'pano_candidate_missing', 'message': '请先执行 edit/repair'})
    manifest = _assert_pano_manifest_integrity(capture, require_p0_size=True)
    model = project.get('model') or {}
    result = gate_pano_erp(
        candidate_path, manifest.get('channels') or {}, manifest, model,
        face_size=req.face_size,
        protected_mask_path=str(capture.get('structure_holdout_mask_path') or ''))
    if str(capture.get('edit_engine') or '') == MATERIAL_ENGINE_VERSION:
        local_call = next((row for row in reversed(project.get('pano_calls') or [])
                           if str(row.get('capture_id') or '')
                           == str(capture.get('capture_id') or '')
                           and str(row.get('model_id') or '') == MATERIAL_ENGINE_VERSION), {})
        replay = verify_geometry_locked_replay(
            candidate_path, manifest.get('channels') or {}, manifest,
            holdout_mask_path=str(capture.get('structure_holdout_mask_path') or ''),
            preset=str((local_call.get('extra') or {}).get('preset') or 'warm-contemporary'),
            expected_output_sha256=str(
                (local_call.get('extra') or {}).get('output_sha256') or ''))
        result = certify_geometry_locked_gate(result, replay)
    capture['gate'] = result
    capture['gated_at'] = time.time()
    capture['status'] = 'gated' if result.get('gate_pass') else 'gate_failed'
    project.setdefault('operations', []).append({
        'type': 'pano_gate', 'payload': {'pano_id': pano_id, 'gate_pass': result.get('gate_pass')},
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    _persist_project(project)
    history_record = _archive_pano_gate_record(project, capture, candidate_path, result)
    return {'gate': result, 'pano_id': pano_id, 'history_record': history_record}


@router.post('/api/whole-home/projects/{project_id}/panos/{pano_id}/review')
def review_whole_home_pano(project_id: str, pano_id: str, req: WholeHomePanoReviewRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    capture = _pano_capture_row(project, pano_id, req.source_hash)
    gate = capture.get('gate') or {}
    if not gate.get('gate_pass'):
        raise HTTPException(409, {
            'code': 'pano_gate_required',
            'message': '只有 P0 自动门禁通过后才能提交 Viewer 验收',
        })
    if str(gate.get('version') or '') != req.gate_version:
        raise HTTPException(409, {'code': 'pano_gate_version_mismatch'})
    candidate_path = capture.get('repaired_rgb_path') or capture.get('edited_rgb_path') or ''
    if not candidate_path or not os.path.isfile(candidate_path):
        raise HTTPException(409, {'code': 'pano_candidate_missing'})
    accepted = all(value == 'pass' for value in req.checklist.values())
    review = {
        'review_id': new_id('panoreview'), 'capture_id': capture.get('capture_id'),
        'pano_id': pano_id, 'source_hash': req.source_hash,
        'candidate_sha256': pano_file_sha256(candidate_path),
        'gate_version': req.gate_version, 'gate_level': gate.get('gate_level'),
        'checklist': copy.deepcopy(req.checklist), 'accepted': accepted,
        'annotator_id': req.annotator_id, 'reviewed_at': time.time(),
    }
    project.setdefault('pano_reviews', []).append(review)
    capture['human_review'] = copy.deepcopy(review)
    capture['status'] = 'accepted' if accepted else 'review_failed'
    _persist_project(project)
    return project_view(project)


@router.post('/api/whole-home/projects/{project_id}/camera-candidates')
def create_whole_home_camera_candidates(project_id: str, req: WholeHomeCameraCandidatesRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定整屋几何与语义布局，再生成机位候选')
    _assert_geometry_production_gate(project)
    if req.mode == 'reference':
        contract = project.get('reference_contract') if isinstance(project.get('reference_contract'), dict) else {}
        if not contract or str(contract.get('contract_id') or '') != req.contract_id:
            raise HTTPException(409, {'code': 'reference_contract_mismatch',
                                      'message': 'reference 候选必须绑定当前项目合同'})
        asset_errors = []
        viewpoint_errors = []
        for slot in contract.get('slots') or []:
            slot_id = str(slot.get('slot_id') or '')
            asset = slot.get('reference_asset') or {}
            if (asset.get('status') != 'verified' or not asset.get('sha256')
                    or not asset.get('local_path') or not os.path.isfile(str(asset.get('local_path') or ''))):
                asset_errors.append(slot_id)
            viewpoint = slot.get('reference_viewpoint') or {}
            mapping = viewpoint.get('point_mapping') or {}
            landing = viewpoint.get('landing_policy') or {}
            if (not viewpoint.get('scene_id') or mapping.get('status') != 'not_available'
                    or landing.get('mode') != 'cad_semantic_relative_region'
                    or landing.get('source') != 'inferred_from_reference_visual_and_cad_anchors'):
                viewpoint_errors.append(slot_id)
        if len(contract.get('slots') or []) != 9 or asset_errors or viewpoint_errors:
            raise HTTPException(409, {
                'code': 'reference_preflight_blocked',
                'message': 'reference 预检要求 9 个已校验本地资产与完整 scene/relative landing 合同',
                'missing_asset_slot_ids': asset_errors,
                'invalid_viewpoint_slot_ids': viewpoint_errors,
            })
        # Refresh non-geometric camera policy from the audited built-in
        # contract.  Existing projects created before the user's angle-flexible
        # instruction stored the old 1-degree limit.  CAD facts/revision remain
        # unchanged; the proposal hash records the newly applied policy.
        canonical = reference_contract_for_url(project.get('reference_url') or '')
        canonical_camera = canonical.get('camera') if isinstance(canonical, dict) else {}
        if canonical_camera:
            contract.setdefault('camera', {}).update(copy.deepcopy(canonical_camera))
            contract['camera_policy_version'] = 2
            project['reference_contract'] = contract
        proposal = generate_reference_camera_candidates(
            project.get('model') or {}, contract, aspect_ratio=req.aspect_ratio,
            max_per_slot=req.max_per_room, project_revision=int(project.get('revision') or 0),
        )
        records = []
        for row in [*(project.get('reference_camera_proposals') or []), proposal][-5:]:
            full = load_reference_camera_proposal(row)
            if not full:
                continue
            storage_key = save_reference_camera_proposal(project_id, full)
            records.append({
                'proposal_id': full.get('proposal_id') or '',
                'proposal_hash': full.get('proposal_hash') or '',
                'status': full.get('status') or '',
                'project_revision': full.get('project_revision'),
                'cad_facts_hash': full.get('cad_facts_hash') or '',
                'model_facts_hash': full.get('model_facts_hash') or '',
                'slot_pool_count': len(full.get('slot_pools') or []),
                'candidate_count': len(full.get('candidates') or []),
                'storage_key': storage_key,
            })
        project['reference_camera_proposals'] = records
        project.setdefault('operations', []).append({
            'type': 'reference_camera_candidates_local',
            'payload': {'proposal_id': proposal.get('proposal_id') or '',
                        'proposal_hash': proposal.get('proposal_hash') or '',
                        'status': proposal.get('status'), 'contract_id': req.contract_id},
            'at': time.time(), 'revision': project.get('revision', 0), 'actor': 'local-reference-camera',
        })
        _persist_project(project)
        return proposal
    return generate_semantic_camera_candidates(
        project.get('model') or {}, aspect_ratio=req.aspect_ratio,
        max_per_room=req.max_per_room,
    )


@router.post('/api/whole-home/projects/{project_id}/reference-captures')
def render_whole_home_reference_captures(
        project_id: str, req: WholeHomeReferenceCaptureBatchRequest):
    """Render and persist nine-slot evidence without a browser or paid call.

    Every slot is saved immediately after its first passing candidate.  A
    partial/crashed batch therefore remains resumable and auditable.  This
    endpoint deliberately has no API-key field and invokes no model provider.
    """
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定 CAD 几何与语义，再生成本地 reference 证据')
    _assert_geometry_production_gate(project)
    proposal_records = [
        row for row in project.get('reference_camera_proposals') or []
        if str(row.get('proposal_id') or '') == req.reference_proposal_id
        and str(row.get('proposal_hash') or '') == req.reference_proposal_hash
    ]
    if len(proposal_records) != 1:
        raise HTTPException(409, {'code': 'reference_proposal_not_found_or_tampered'})
    proposal = load_reference_camera_proposal(proposal_records[0])
    if not proposal:
        raise HTTPException(409, {'code': 'reference_proposal_storage_missing'})
    model = project.get('model') or {}
    if (int(proposal.get('project_revision') or -1) != int(project.get('revision') or 0)
            or str(proposal.get('cad_facts_hash') or '') != str(model.get('cad_facts_hash') or '')
            or str(proposal.get('model_facts_hash') or '') != reference_model_facts_hash(model)):
        raise HTTPException(409, {'code': 'reference_proposal_stale'})
    contract = split_reference_contract(project.get('reference_contract') or {})
    pool_rows = list(proposal.get('slot_pools') or [])
    expected_slot_ids = [str(row.get('slot_id') or '') for row in pool_rows]
    renderer_id = 'numpy_zbuffer_v1'
    requested_batch_id = new_id('reference_capture_batch')
    proposal_model_hash = _reference_batch_model_hash(model)
    proposal_model_facts_hash = reference_model_facts_hash(model)
    proposal_cad_facts_hash = str(model.get('cad_facts_hash') or '')
    proposal_project_revision = int(project.get('revision') or 0)

    def begin_batch(document: dict) -> dict:
        current_model = document.get('model') or {}
        if (int(document.get('revision') or 0) != proposal_project_revision
                or str(current_model.get('cad_facts_hash') or '') != proposal_cad_facts_hash
                or reference_model_facts_hash(current_model) != proposal_model_facts_hash
                or _reference_batch_model_hash(current_model) != proposal_model_hash):
            raise RuntimeError('reference_capture_project_stale_before_batch')
        batches = document.setdefault('reference_software_capture_batches', [])
        existing = next((
            row for row in reversed(batches)
            if int(row.get('batch_schema_version') or 0) == 2
            if str(row.get('proposal_id') or '') == req.reference_proposal_id
            and str(row.get('proposal_hash') or '') == req.reference_proposal_hash
            and str(row.get('renderer') or '') == renderer_id
            and int(row.get('width') or 0) == req.width
            and int(row.get('height') or 0) == req.height
            and int(row.get('project_revision') or -1) == proposal_project_revision
            and str(row.get('model_hash') or '') == proposal_model_hash
            and str(row.get('model_facts_hash') or '') == proposal_model_facts_hash
            and str(row.get('cad_facts_hash') or '') == proposal_cad_facts_hash
        ), None)
        if existing is None:
            now = time.time()
            existing = {
                'batch_schema_version': 2,
                'batch_id': requested_batch_id,
                'proposal_id': req.reference_proposal_id,
                'proposal_hash': req.reference_proposal_hash,
                'renderer': renderer_id, 'width': req.width, 'height': req.height,
                'expected_slot_ids': expected_slot_ids,
                'project_revision': proposal_project_revision,
                'model_hash': proposal_model_hash,
                'model_facts_hash': proposal_model_facts_hash,
                'cad_facts_hash': proposal_cad_facts_hash,
                'batch_version': 1,
                'slots': [{
                    'slot_id': slot_id, 'status': 'pending', 'attempts': [],
                    'candidate_id': '', 'capture_id': '', 'reason': '',
                    'updated_at': now,
                    'slot_version': 0, 'owner_token_hash': '',
                    'owner_claimed_at': None, 'owner_expires_at': None,
                } for slot_id in expected_slot_ids],
                'saved': [], 'skipped': [], 'blocked': [],
                'status': 'running', 'paid_calls': 0,
                'created_at': now, 'updated_at': now,
            }
            batches.append(existing)
            document.setdefault('operations', []).append({
                'type': 'reference_capture_batch_started_local_software',
                'payload': {
                    'batch_id': existing['batch_id'],
                    'proposal_id': req.reference_proposal_id,
                    'expected_slot_ids': expected_slot_ids, 'paid_calls': 0,
                },
                'at': now, 'revision': document.get('revision', 0),
                'actor': req.annotator_id,
            })
        elif existing.get('status') != 'ready':
            now = time.time()
            active_owner = any(
                row.get('status') == 'rendering'
                and float(row.get('owner_expires_at') or 0) > now
                for row in existing.get('slots') or [])
            if not active_owner:
                existing.update(
                    status='running', resumed_at=now, updated_at=now,
                    batch_version=int(existing.get('batch_version') or 0) + 1)
        return document

    try:
        project, _, _ = cas_update_project(project_id, begin_batch)
    except (FileNotFoundError, RuntimeError) as ex:
        raise HTTPException(409, {
            'code': 'reference_capture_batch_cas_conflict', 'message': str(ex),
        }) from ex
    batch = next(
        row for row in reversed(project.get('reference_software_capture_batches') or [])
        if str(row.get('proposal_id') or '') == req.reference_proposal_id
        and str(row.get('proposal_hash') or '') == req.reference_proposal_hash
        and str(row.get('renderer') or '') == renderer_id
        and int(row.get('width') or 0) == req.width
        and int(row.get('height') or 0) == req.height)
    batch_id = str(batch.get('batch_id') or '')
    if batch.get('status') == 'ready':
        return {'batch': batch, 'project': project_view(project)}

    def confirmed_capture(document: dict, slot_id: str) -> dict:
        return next((
            row for row in reversed(document.get('captures') or [])
            if row.get('status') == 'confirmed'
            and str(row.get('reference_slot_id') or
                    (row.get('camera') or {}).get('reference_slot_id') or '') == slot_id
            and str(row.get('reference_proposal_id') or '') == req.reference_proposal_id
            and str(row.get('reference_proposal_hash') or '') == req.reference_proposal_hash
            and str(((row.get('camera') or {}).get('render_gate') or {}).get('version') or '')
            == 'whole-home-reference-render-gate-v3-software'
            and str(((row.get('camera') or {}).get('reference_contract_validation') or {})
                    .get('pixel_gate_version') or '') == 'whole-home-subject-pixel-gate-v2'
            and not (
                str((row.get('camera') or {}).get('origin_scope') or '') == 'adjacent_portal'
                and not ((row.get('camera') or {}).get('origin_room_ids') or []))
        ), {})

    def claim_slot(slot_id: str) -> dict:
        owner_token = secrets.token_urlsafe(32)
        outcome: dict = {}

        def mutate(document: dict) -> dict:
            target_batch = next((
                row for row in document.get('reference_software_capture_batches') or []
                if str(row.get('batch_id') or '') == batch_id), None)
            if target_batch is None:
                raise RuntimeError('reference_capture_batch_missing_during_claim')
            current_model = document.get('model') or {}
            if (int(document.get('revision') or 0)
                    != int(target_batch.get('project_revision') or -1)
                    or str(target_batch.get('proposal_id') or '')
                    != req.reference_proposal_id
                    or str(target_batch.get('proposal_hash') or '')
                    != req.reference_proposal_hash
                    or str(target_batch.get('cad_facts_hash') or '')
                    != str(current_model.get('cad_facts_hash') or '')
                    or str(target_batch.get('model_facts_hash') or '')
                    != reference_model_facts_hash(current_model)
                    or str(target_batch.get('model_hash') or '')
                    != _reference_batch_model_hash(current_model)):
                raise RuntimeError('reference_capture_batch_stale_during_claim')
            slot = next((row for row in target_batch.get('slots') or []
                         if str(row.get('slot_id') or '') == slot_id), None)
            if slot is None:
                raise RuntimeError('reference_capture_slot_missing_during_claim')
            now = time.time()
            existing_capture = confirmed_capture(document, slot_id)
            if existing_capture:
                slot.update(
                    status='saved' if slot.get('status') == 'saved' else 'skipped',
                    candidate_id=str(existing_capture.get('candidate_id') or ''),
                    capture_id=str(existing_capture.get('capture_id') or ''),
                    reason='' if slot.get('status') == 'saved' else 'confirmed_capture_exists',
                    updated_at=now, owner_token_hash='',
                    owner_claimed_at=None, owner_expires_at=None,
                    slot_version=int(slot.get('slot_version') or 0) + 1)
                target_batch.update(
                    batch_version=int(target_batch.get('batch_version') or 0) + 1,
                    updated_at=now)
                outcome.update(state='terminal', status=slot['status'])
                return document
            active_owner = next((
                row for row in target_batch.get('slots') or []
                if row.get('status') == 'rendering'
                and float(row.get('owner_expires_at') or 0) > now
            ), None)
            if active_owner:
                outcome.update(
                    state='busy', owner_slot_id=active_owner.get('slot_id'))
                return document
            if slot.get('status') in {'saved', 'skipped'}:
                outcome.update(state='terminal', status=slot.get('status'))
                return document
            slot_version = int(slot.get('slot_version') or 0) + 1
            batch_version = int(target_batch.get('batch_version') or 0) + 1
            slot.update(
                status='rendering', owner_token_hash=_reference_owner_hash(owner_token),
                owner_claimed_at=now, owner_expires_at=now + 900,
                slot_version=slot_version, reason='', updated_at=now)
            target_batch.update(
                status='running', batch_version=batch_version, updated_at=now)
            outcome.update(
                state='claimed', batch_id=batch_id, slot_id=slot_id,
                owner_token=owner_token, slot_version=slot_version,
                batch_version=batch_version,
                proposal_id=req.reference_proposal_id,
                proposal_hash=req.reference_proposal_hash)
            return document
        try:
            cas_update_project(project_id, mutate)
        except (FileNotFoundError, RuntimeError) as ex:
            raise HTTPException(409, {
                'code': 'reference_capture_claim_cas_conflict',
                'slot_id': slot_id, 'message': str(ex),
            }) from ex
        return outcome

    def checkpoint_owned(ownership: dict, status: str, *, attempts: list[dict],
                         reason: str = '') -> None:
        def mutate(document: dict) -> dict:
            batch_row, slot = _assert_reference_ownership(document, ownership)
            now = time.time()
            slot.update(
                status=status, attempts=copy.deepcopy(attempts),
                reason=str(reason or ''), updated_at=now,
                owner_token_hash='', owner_claimed_at=None,
                owner_expires_at=None,
                slot_version=int(slot.get('slot_version') or 0) + 1)
            batch_row.update(
                batch_version=int(batch_row.get('batch_version') or 0) + 1,
                updated_at=now)
            return document
        try:
            cas_update_project(project_id, mutate)
        except (FileNotFoundError, RuntimeError) as ex:
            raise HTTPException(409, {
                'code': 'reference_capture_checkpoint_owner_fenced',
                'slot_id': ownership.get('slot_id'), 'message': str(ex),
            }) from ex

    semantic_legend = {
        role: '#%02x%02x%02x' % color for role, color in SEMANTIC_COLORS.items()
    }
    owner_contention = False
    for pool in pool_rows:
        slot_id = str(pool.get('slot_id') or '')
        ownership = claim_slot(slot_id)
        if ownership.get('state') == 'busy':
            owner_contention = True
            break
        if ownership.get('state') == 'terminal':
            continue
        attempts = []
        passed = False
        try:
            for candidate in pool.get('candidates') or []:
                rendered = render_reference_candidate(
                    model, candidate, contract, width=req.width, height=req.height)
                attempts.append({
                    'candidate_id': candidate.get('candidate_id') or '',
                    'pass': bool(rendered.get('pass')),
                    'render_gate': copy.deepcopy(rendered.get('render_gate') or {}),
                    'subject_evidence': copy.deepcopy(rendered.get('subject_evidence') or {}),
                })
                if not rendered.get('pass'):
                    continue
                camera = copy.deepcopy(candidate.get('camera') or {})
                validation = copy.deepcopy(camera.get('reference_contract_validation') or {})
                evidence = rendered['subject_evidence']
                validation.update({
                    'width': evidence['width'], 'height': evidence['height'],
                    'pixel_origin': 'top-left',
                    'must_show_bounds': copy.deepcopy(evidence['must_show_bounds']),
                    'safe_frame_status': 'pass', 'safe_frame_pass': True,
                    'buffer_renderer': rendered.get('renderer') or 'numpy_zbuffer_v1',
                    'pixel_gate_version': evidence.get('version') or '',
                })
                camera['reference_contract_validation'] = validation
                camera['render_gate'] = copy.deepcopy(rendered['render_gate'])
                capture_req = WholeHomeCaptureRequest(
                    camera=camera, aspect_ratio='4:3',
                    rgb_data_url=image_data_url(rendered['images']['rgb']),
                    depth_data_url=image_data_url(rendered['images']['depth']),
                    normal_data_url=image_data_url(rendered['images']['normal']),
                    edge_data_url=image_data_url(rendered['images']['edge']),
                    semantic_data_url=image_data_url(rendered['images']['semantic']),
                    semantic_legend=semantic_legend,
                    subject_id_data_url=image_data_url(rendered['images']['subject_id']),
                    subject_id_legend=rendered['legend'],
                    room_id=str(candidate.get('room_id') or ''), plan_id='',
                    candidate_id=str(candidate.get('candidate_id') or ''),
                    reference_slot_id=slot_id,
                    reference_proposal_id=req.reference_proposal_id,
                    reference_proposal_hash=req.reference_proposal_hash,
                    pool_rank=1, is_primary=True, annotator_id=req.annotator_id,
                )
                ownership['attempts'] = copy.deepcopy(attempts)
                # Route through the public save adapter so local integrations
                # can replace storage without bypassing the durable owner CAS.
                # The default adapter consumes this private, in-process proof;
                # it is never serialized into the request contract.
                object.__setattr__(
                    capture_req, '_reference_ownership', ownership)
                saved_project = save_whole_home_capture(project_id, capture_req)
                saved_slot = next((
                    row for batch_row in (
                        (saved_project or {}).get(
                            'reference_software_capture_batches') or [])
                    if str(batch_row.get('batch_id') or '') == batch_id
                    for row in batch_row.get('slots') or []
                    if str(row.get('slot_id') or '') == slot_id
                ), {})
                if saved_slot.get('status') != 'saved':
                    # A storage adapter may persist the capture but leave the
                    # owner checkpoint to the orchestrator.  Bind the exact
                    # server-visible capture under the original CAS proof.
                    def checkpoint_adapter_capture(document: dict) -> dict:
                        batch_row, slot = _assert_reference_ownership(
                            document, ownership)
                        capture = next((
                            row for row in reversed(document.get('captures') or [])
                            if row.get('status') == 'confirmed'
                            and str(row.get('reference_slot_id') or '') == slot_id
                            and str(row.get('candidate_id') or '')
                            == str(candidate.get('candidate_id') or '')
                        ), None)
                        if capture is None:
                            raise RuntimeError(
                                'reference_capture_adapter_missing_saved_capture')
                        now = time.time()
                        slot.update(
                            status='saved', attempts=copy.deepcopy(attempts),
                            candidate_id=str(candidate.get('candidate_id') or ''),
                            capture_id=str(capture.get('capture_id') or ''),
                            reason='', updated_at=now,
                            owner_token_hash='', owner_claimed_at=None,
                            owner_expires_at=None,
                            slot_version=int(slot.get('slot_version') or 0) + 1)
                        batch_row.update(
                            batch_version=int(
                                batch_row.get('batch_version') or 0) + 1,
                            updated_at=now)
                        return document
                    cas_update_project(project_id, checkpoint_adapter_capture)
                passed = True
                break
        except BaseException:
            # A process-local renderer/save exception must release the durable
            # owner claim immediately.  Otherwise a restart is needlessly
            # fenced for 15 minutes and the slot cannot resume from pending.
            try:
                checkpoint_owned(
                    ownership, 'pending', attempts=attempts,
                    reason='render_or_save_interrupted')
            except Exception as checkpoint_error:
                logger.warning(
                    '[reference_capture] 异常后 owner 释放失败，保留原异常: '
                    f'{checkpoint_error}')
            raise
        if not passed:
            checkpoint_owned(
                ownership, 'blocked', attempts=attempts,
                reason='no_candidate_passed_software_pixel_gate')

    if owner_contention:
        project = load_project(project_id) or project
        batch = next(row for row in project.get('reference_software_capture_batches') or []
                     if str(row.get('batch_id') or '') == batch_id)
        return {
            'batch': batch, 'project': project_view(project),
            'resume_status': 'owned_by_other_process',
        }

    def finish_batch(document: dict) -> dict:
        target = next((
            row for row in document.get('reference_software_capture_batches') or []
            if str(row.get('batch_id') or '') == batch_id), None)
        if target is None:
            raise RuntimeError('reference_capture_batch_missing_at_finish')
        slots = target.get('slots') or []
        target['saved'] = [{
            'slot_id': row.get('slot_id'), 'candidate_id': row.get('candidate_id') or '',
            'capture_id': row.get('capture_id') or '',
        } for row in slots if row.get('status') == 'saved']
        target['skipped'] = [{
            'slot_id': row.get('slot_id'), 'candidate_id': row.get('candidate_id') or '',
            'capture_id': row.get('capture_id') or '', 'reason': row.get('reason') or '',
        } for row in slots if row.get('status') == 'skipped']
        target['blocked'] = [{
            'slot_id': row.get('slot_id'), 'attempts': copy.deepcopy(row.get('attempts') or []),
            'reason': row.get('reason') or '',
        } for row in slots if row.get('status') == 'blocked']
        terminal_count = sum(row.get('status') in {'saved', 'skipped', 'blocked'} for row in slots)
        target['status'] = (
            'ready' if terminal_count == len(slots) and not target['blocked']
            else 'partial' if target['saved'] or target['skipped'] else 'blocked')
        target['batch_version'] = int(target.get('batch_version') or 0) + 1
        target['completed_at'] = time.time()
        target['updated_at'] = time.time()
        document.setdefault('operations', []).append({
            'type': 'reference_capture_batch_local_software',
            'payload': {
                'batch_id': batch_id, 'status': target['status'],
                'saved_slot_ids': [row['slot_id'] for row in target['saved']],
                'blocked_slot_ids': [row['slot_id'] for row in target['blocked']],
                'paid_calls': 0,
            },
            'at': time.time(), 'revision': document.get('revision', 0),
            'actor': req.annotator_id,
        })
        return document
    try:
        project, _, _ = cas_update_project(project_id, finish_batch)
    except (FileNotFoundError, RuntimeError) as ex:
        raise HTTPException(409, {
            'code': 'reference_capture_batch_finish_cas_conflict', 'message': str(ex),
        }) from ex
    batch = next(row for row in project.get('reference_software_capture_batches') or []
                 if str(row.get('batch_id') or '') == batch_id)
    return {'batch': batch, 'project': project_view(project)}


@router.get('/api/whole-home/projects/{project_id}/reference-assets/{slot_id}')
def get_whole_home_reference_asset(project_id: str, slot_id: str):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    contract = project.get('reference_contract') if isinstance(project.get('reference_contract'), dict) else {}
    slots = [row for row in contract.get('slots') or [] if str(row.get('slot_id') or '') == slot_id]
    if len(slots) != 1:
        raise HTTPException(404, '参考资产不存在')
    asset = slots[0].get('reference_asset') or {}
    path = str(asset.get('local_path') or '')
    if asset.get('status') != 'verified' or not path or not os.path.isfile(path):
        raise HTTPException(409, {'code': 'reference_asset_unavailable', 'slot_id': slot_id})
    return FileResponse(path, media_type=str(asset.get('mime') or 'image/jpeg'),
                        filename=str(asset.get('filename') or f'{slot_id}.jpg'))


@router.post('/api/whole-home/projects/{project_id}/camera-plans')
async def create_whole_home_camera_plan(project_id: str, req: WholeHomeAutoCameraRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(400, '请先锁定整屋几何，再自动选择机位')
    _assert_geometry_production_gate(project)
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    try:
        plan = await asyncio.to_thread(
            rank_auto_camera_plan, api_key, project, req.candidates,
            shots_per_room=req.shots_per_room, aspect_ratio=req.aspect_ratio,
            annotator_id=req.annotator_id, requested_room_pools=req.room_pools,
        )
    except ValueError as ex:
        raise HTTPException(400, str(ex)) from ex
    except Exception as ex:
        logger.exception('[自动机位] 候选复排异常')
        raise HTTPException(500, f'自动机位复排失败: {ex}') from ex
    project.setdefault('auto_camera_plans', []).append(plan)
    project.setdefault('operations', []).append({
        'type': 'auto_camera_plan',
        'payload': {
            'plan_id': plan['plan_id'], 'candidate_count': len(plan.get('candidates') or []),
            'selected_count': len(plan.get('selected_cameras') or []),
            'ai_model': plan.get('ai_model') or '', 'ai_error': plan.get('ai_error') or '',
        },
        'at': time.time(), 'revision': project.get('revision', 0), 'actor': req.annotator_id,
    })
    _persist_project(project)
    return project_view({'project_id': project_id, 'floorplan_path': project.get('floorplan_path'),
                         'captures': [], 'auto_camera_plans': [plan]})['auto_camera_plans'][0]


def _valid_capture(project: dict, capture: dict, aspect_ratio: str) -> bool:
    if capture.get('status') != 'confirmed' or capture.get('aspect_ratio') != aspect_ratio:
        return False
    if not all(os.path.isfile(capture.get(f'{key}_path') or '')
               for key in ('rgb', 'depth', 'normal', 'semantic', 'plan_overlay')):
        return False
    return capture.get('source_hash') == _capture_hash(project.get('model') or {}, capture.get('camera') or {}, aspect_ratio)


def _result_rows(req: WholeHomeRunRequest, capture_groups: list[dict], *, legacy: bool) -> list[dict]:
    rows = []
    for group in capture_groups:
        capture_ids = [group['primary_capture_id'], *(group.get('fallback_capture_ids') or [])]
        repeat = req.candidates_per_camera if legacy else 1
        for model_key in req.model_keys:
            for candidate_index in range(repeat):
                rows.append({
                    'result_id': new_id('result'), 'room_id': group.get('room_id') or '',
                    'slot_id': group.get('slot_id') or '',
                    'capture_ids': capture_ids, 'capture_id': capture_ids[0],
                    'camera_id': group.get('primary_camera_id') or '',
                    'camera_name': group.get('camera_name') or group.get('room_label') or group.get('room_id') or capture_ids[0],
                    'model_key': model_key, 'candidate_index': candidate_index + 1,
                    'status': 'queued', 'stage': '等待生成', 'error': '', 'path': '',
                    'outcome': 'queued', 'deliverable': False, 'selected_attempt_id': '',
                    'structure_path': '', 'api_original_path': '', 'material_path': '',
                    'corrected_path': '', 'final_path': '', 'evaluation': None,
                    'attempts': [], 'trace': [],
                })
    return rows


def _cancel_result(result: dict, message: str = '已取消；所有已生成中间图均已保留') -> None:
    result.update(
        status='failed', outcome='cancelled', deliverable=False, stage='', error=message,
        path='', final_path='', selected_attempt_id='',
    )


async def _call_generation(run: dict, result: dict, attempt_row: dict, api_key: str,
                           model_name: str, model_id: str, model_key: str,
                           pass_name: str, prompt: str, inputs: list[str],
                           resolution: str, aspect_ratio: str) -> tuple[object, Optional[str], str]:
    _assert_manual_call_cap(run, 'generation')
    sem = state.model_semaphores[model_key]
    started = time.time()
    prompt_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    input_rows = _input_manifest(inputs, run.get('reference_asset_snapshots') or [])
    call_id = new_id('call')
    trace = {
        'call_id': call_id, 'pass': pass_name, 'attempt_id': attempt_row.get('attempt_id'),
        'started_at': started, 'provider': '', 'model_id': model_id,
        'resolution': resolution, 'aspect_ratio': aspect_ratio,
        'prompt_version': f'whole-home-{pass_name}-v2-semantic-gate',
        'prompt_sha256': prompt_sha, 'prompt': prompt,
        'inputs': input_rows, 'seconds': 0, 'success': False, 'error': '',
    }
    attempt_row.setdefault('trace', []).append(trace)
    result.setdefault('trace', []).append(trace)

    def on_stage(value):
        prefix = '第一轮结构' if pass_name == 'structure' else '第二轮地板'
        result['stage'] = f'{prefix}：{value}'
        _persist_run(run)

    image = None
    error: Optional[str] = None
    provider = ''
    ledger_row = {
        'call_id': call_id, 'kind': 'generation', 'phase': pass_name,
        'result_id': result.get('result_id'), 'attempt_id': attempt_row.get('attempt_id'),
        'started_at': None, 'finished_at': None, 'seconds': 0,
        'status': 'pending', 'provider': '', 'model_id': model_id,
        'model_key': model_key, 'resolution': resolution,
        'prompt_sha256': prompt_sha, 'input_sha256': [row['sha256'] for row in input_rows],
        'error': '',
    }
    reservation_id = ''
    try:
        async with sem:
            # Reserve only after the per-model semaphore is acquired.  This is
            # still immediately before provider dispatch, but waiting results
            # no longer consume budget or force run writes for calls that have
            # not started.
            reservation_id = _reserve_development_call(run, ledger_row)
            _dispatch_development_call(run, ledger_row, reservation_id)
            started = time.time()
            if reservation_id:
                ledger_row['started_at'] = started
                _persist_run(run)
            image, error, provider = await asyncio.to_thread(
                call_image_generate, api_key, model_id, prompt, inputs[0],
                resolution, aspect_ratio, None, None, on_stage,
                lambda: run['run_id'] in _CANCELLED, None, inputs,
                pass_name == 'structure',
            )
    except DevelopmentAutopilotError:
        raise
    except Exception as ex:
        error = str(ex)
    finished = time.time()
    trace.update(
        finished_at=finished, seconds=round(finished - started, 1),
        provider=provider or '', success=image is not None, error=error or '',
    )
    if reservation_id:
        _finish_development_call(
            run, ledger_row, reservation_id,
            success=image is not None, error=error or '')
    else:
        run.setdefault('call_ledger', []).append(ledger_row)
    ledger_row.update(
        started_at=started, finished_at=finished, seconds=trace['seconds'],
        status='done' if image is not None else 'failed', provider=provider or '',
        error=error or '',
    )
    record_usage('整屋3D套图', model_name, provider or '', image is not None, 'generate')
    _persist_run(run)
    return image, error, provider


async def _generate_one(run: dict, project: dict, capture_map: dict[str, dict],
                        result: dict, api_key: str) -> None:
    started = time.time()
    if run['run_id'] in _CANCELLED:
        _cancel_result(result)
        _persist_run(run)
        return
    key = result['model_key']
    model_name = 'Nano Banana 2' if key == 'b2' else 'Nano Banana Pro'
    model_id = GEMINI_MODEL_MAP[model_name]
    result.update(status='running', outcome='structure_running', stage='第一轮：结构硬门禁', error='')
    _persist_run(run)
    captures = [capture_map[capture_id] for capture_id in result.get('capture_ids') or [] if capture_id in capture_map]
    if not captures:
        result.update(status='failed', outcome='failed', stage='', error='逻辑结果没有可用机位缓冲', seconds=0)
        _persist_run(run)
        return
    structure_specs = [(captures[0], 'primary'), (captures[0], 'primary_repair')]
    structure_specs.extend((capture, f'backup_{index}') for index, capture in enumerate(captures[1:3], 1))
    structure_specs = structure_specs[:4]
    accepted_attempt: Optional[dict] = None
    accepted_capture: Optional[dict] = None
    feedback = ''
    for index, (capture, trigger) in enumerate(structure_specs, 1):
        if run['run_id'] in _CANCELLED:
            _cancel_result(result)
            _persist_run(run)
            return
        attempt_row = {
            'attempt_id': new_id('attempt'), 'attempt_index': index, 'trigger': trigger,
            'capture_id': capture['capture_id'], 'camera_id': capture['camera_id'],
            'camera_name': (capture.get('camera') or {}).get('name') or capture['camera_id'],
            'status': 'running', 'structure_path': '', 'structure_local_gate': None,
            'structure_evaluation': None,
            'structure_qa_attempts': [], 'material_attempts': [], 'trace': [],
        }
        result.setdefault('attempts', []).append(attempt_row)
        result['stage'] = f'结构尝试 {index}/{len(structure_specs)} · {trigger}'
        _persist_run(run)
        try:
            prompt, inputs = build_generation_prompt(
                project, capture, run, pass_name='structure', feedback=feedback if index > 1 else '')
        except Exception as ex:
            attempt_row.update(status='failed', error=str(ex))
            feedback = str(ex)
            _persist_run(run)
            continue
        attempt_row.update(
            structure_prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            structure_input_manifest=_input_manifest(
                inputs, run.get('reference_asset_snapshots') or []),
        )
        image, error, provider = await _call_generation(
            run, result, attempt_row, api_key, model_name, model_id, key,
            'structure', prompt, inputs, '2K', run['aspect_ratio'])
        attempt_row['provider'] = provider or ''
        if image is None:
            attempt_row.update(status='failed', error=error or '结构写实化未返回图片')
            feedback = error or 'Structure generation returned no image'
            _persist_run(run)
            continue
        structure_path = await asyncio.to_thread(
            save_api_result_jpg, image,
            f'整屋_{attempt_row["camera_name"]}_{model_name}_结构_{index}', capture['rgb_path'])
        if not structure_path:
            attempt_row.update(status='failed', error='结构图保存失败')
            feedback = 'Structure image could not be saved'
            _persist_run(run)
            continue
        attempt_row['structure_path'] = structure_path
        if run['run_id'] in _CANCELLED:
            attempt_row['status'] = 'cancelled'
            _cancel_result(result)
            _persist_run(run)
            return
        result['stage'] = f'结构本地对齐 {index}/{len(structure_specs)} · fail-closed'
        _persist_run(run)
        try:
            local_gate = await asyncio.to_thread(
                evaluate_structure_local_gate, project, capture, structure_path,
                f"{attempt_row['attempt_id']}_{run['run_id']}_{result['result_id']}")
        except Exception as ex:
            local_gate = {
                'version': 'structure-local-alignment-v1', 'phase': 'structure',
                'status': 'unavailable', 'verdict': 'fail', 'gate_pass': False,
                'thresholds': {}, 'missing_buffers': [], 'invalid_buffers': [],
                'overlay_path': '', 'summary': f'本地图像对齐门禁异常，已阻断：{ex}',
            }
        attempt_row['structure_local_gate'] = local_gate
        _record_local_gate(run, result, attempt_row, local_gate, phase='structure')
        if not local_gate.get('gate_pass'):
            attempt_row.update(status='structure_rejected_local', error=local_gate.get('summary') or '')
            feedback = str(local_gate.get('summary') or 'Local structure alignment gate failed')[:3000]
            _persist_run(run)
            continue
        result['stage'] = f'结构 Gemini QA {index}/{len(structure_specs)} · fail-closed'
        _persist_run(run)
        evaluation, qa_error, qa_attempts = await _evaluate_with_retries(
            api_key, project, {
                **capture, 'material_mode': run.get('material_mode') or 'floor_sample',
                'scene_recipe_snapshot': copy.deepcopy(run.get('scene_recipe_snapshot') or {}),
            },
            structure_path, run['floor_path'], phase='structure',
            structure_path=structure_path, run=run, result=result, attempt_row=attempt_row)
        attempt_row.update(
            structure_evaluation=evaluation, structure_evaluation_error=qa_error or '',
            structure_qa_attempts=qa_attempts,
            status='structure_accepted' if evaluation.get('gate_pass') else 'structure_rejected',
        )
        _persist_run(run)
        if evaluation.get('gate_pass'):
            accepted_attempt, accepted_capture = attempt_row, capture
            break
        feedback = _qa_feedback(evaluation, qa_error or '')

    if not accepted_attempt or not accepted_capture:
        last = (result.get('attempts') or [{}])[-1]
        result.update(
            status='done', outcome='structure_rejected', deliverable=False, stage='', path='',
            structure_path='', final_path='', selected_attempt_id='',
            evaluation=last.get('structure_evaluation'),
            evaluation_error=last.get('structure_evaluation_error') or '',
            error='所有主/备用机位的结构尝试均未通过硬门禁，未调用地板阶段',
            seconds=round(time.time() - started, 1),
        )
        _persist_run(run)
        return

    result.update(
        capture_id=accepted_capture['capture_id'], camera_id=accepted_capture['camera_id'],
        camera_name=accepted_attempt['camera_name'], structure_path=accepted_attempt['structure_path'],
        outcome='material_running', selected_attempt_id=accepted_attempt['attempt_id'],
    )
    material_feedback = ''
    last_evaluation: Optional[dict] = None
    last_qa_error = ''
    for material_index in range(1, 3):
        if run['run_id'] in _CANCELLED:
            _cancel_result(result)
            _persist_run(run)
            return
        material_row = {
            'material_attempt_id': new_id('material_attempt'), 'attempt_index': material_index,
            'trigger': 'material_primary' if material_index == 1 else 'material_qa_retry',
            'status': 'running', 'api_original_path': '', 'material_path': '',
            'corrected_path': '', 'final_path': '', 'final_local_gate': None,
            'evaluation': None,
            'qa_attempts': [], 'trace': [],
        }
        accepted_attempt.setdefault('material_attempts', []).append(material_row)
        result['stage'] = f'地板尝试 {material_index}/2'
        material_capture = {**accepted_capture, 'structure_path': accepted_attempt['structure_path']}
        prompt, inputs = build_generation_prompt(
            project, material_capture, run, pass_name='material', feedback=material_feedback)
        material_row.update(
            prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            input_manifest=_input_manifest(
                inputs, run.get('reference_asset_snapshots') or []),
        )
        material, material_error, material_provider = await _call_generation(
            run, result, material_row, api_key, model_name, model_id, key,
            'material', prompt, inputs, run['resolution'], run['aspect_ratio'])
        material_row['provider'] = material_provider or ''
        if material is None:
            material_row.update(status='failed', error=material_error or '地板应用未返回图片')
            material_feedback = material_error or 'Material edit returned no image'
            _persist_run(run)
            continue
        api_original_path = await asyncio.to_thread(
            save_api_result_jpg, material,
            f'整屋_{accepted_attempt["camera_name"]}_{model_name}_地板原图_{material_index}',
            run['floor_path'])
        if not api_original_path:
            material_row.update(status='failed', error='地板 API 原始图保存失败')
            material_feedback = 'Raw material edit could not be saved'
            _persist_run(run)
            continue
        material_row['api_original_path'] = api_original_path
        material_row['material_path'] = api_original_path
        final_candidate_path = api_original_path
        try:
            if (run.get('material_mode') == 'floor_sample'
                    and bool(load_config().get('auto_color_match_enabled', False))):
                from .routes_jobs import _auto_color_match_generated
                corrected, metadata, color_error = await asyncio.to_thread(
                    _auto_color_match_generated, material, api_original_path, run['floor_path'])
                if corrected is not None:
                    corrected_path = await asyncio.to_thread(
                        save_api_result_png, corrected,
                        f'整屋_{accepted_attempt["camera_name"]}_自动校色_{material_index}',
                        api_original_path, metadata)
                    if corrected_path:
                        material_row.update(
                            corrected_path=corrected_path, auto_color_status='done',
                            auto_color_metadata=metadata,
                        )
                        final_candidate_path = corrected_path
                    else:
                        material_row.update(auto_color_status='failed', auto_color_error='自动校色结果保存失败')
                elif color_error:
                    material_row.update(auto_color_status='failed', auto_color_error=color_error)
            else:
                material_row['auto_color_status'] = 'disabled'
        except Exception as ex:
            material_row.update(auto_color_status='failed', auto_color_error=str(ex))
        material_row['final_path'] = final_candidate_path
        if run['run_id'] in _CANCELLED:
            material_row['status'] = 'cancelled'
            _cancel_result(result)
            _persist_run(run)
            return
        result['stage'] = f'最终本地几何 {material_index}/2 · fail-closed'
        _persist_run(run)
        try:
            final_local_gate = await asyncio.to_thread(
                evaluate_final_local_gate, project, accepted_capture,
                accepted_attempt['structure_path'], final_candidate_path,
                f"{material_row['material_attempt_id']}_{accepted_attempt['attempt_id']}_{run['run_id']}_{result['result_id']}")
        except Exception as ex:
            final_local_gate = {
                'version': 'material-local-geometry-v1', 'phase': 'final',
                'status': 'unavailable', 'verdict': 'fail', 'gate_pass': False,
                'thresholds': {}, 'missing_buffers': [], 'invalid_buffers': [],
                'overlay_path': '', 'summary': f'本地材质几何门禁异常，已阻断：{ex}',
            }
        material_row['final_local_gate'] = final_local_gate
        _record_local_gate(
            run, result, accepted_attempt, final_local_gate, phase='final',
            material_row=material_row)
        if not final_local_gate.get('gate_pass'):
            material_row.update(status='rejected_local', error=final_local_gate.get('summary') or '')
            material_feedback = str(final_local_gate.get('summary') or 'Local final geometry gate failed')[:3000]
            last_evaluation = None
            last_qa_error = material_feedback
            _persist_run(run)
            continue
        result['stage'] = f'最终 Gemini QA {material_index}/2 · fail-closed'
        evaluation, qa_error, qa_attempts = await _evaluate_with_retries(
            api_key, project, {
                **accepted_capture, 'material_mode': run.get('material_mode') or 'floor_sample',
                'scene_recipe_snapshot': copy.deepcopy(run.get('scene_recipe_snapshot') or {}),
            },
            final_candidate_path, run['floor_path'],
            phase='final', structure_path=accepted_attempt['structure_path'],
            material_path=api_original_path, run=run, result=result, attempt_row=accepted_attempt)
        last_evaluation, last_qa_error = evaluation, qa_error or ''
        material_row.update(
            evaluation=evaluation, evaluation_error=qa_error or '', qa_attempts=qa_attempts,
            status='accepted' if evaluation.get('gate_pass') else 'rejected',
        )
        _persist_run(run)
        if evaluation.get('gate_pass'):
            accepted_attempt['status'] = 'accepted'
            result.update(
                status='done', outcome='accepted', deliverable=True, stage='', error='',
                path=final_candidate_path, api_original_path=api_original_path,
                material_path=api_original_path,
                corrected_path=material_row.get('corrected_path') or '',
                final_path=final_candidate_path, evaluation=evaluation,
                evaluation_error='', provider=material_provider or accepted_attempt.get('provider') or '',
                seconds=round(time.time() - started, 1),
            )
            _persist_run(run)
            return
        material_feedback = _qa_feedback(evaluation, qa_error or '')

    accepted_attempt['status'] = 'material_rejected'
    result.update(
        status='done', outcome='material_rejected', deliverable=False, stage='', path='',
        final_path='', api_original_path='', material_path='', corrected_path='',
        evaluation=last_evaluation, evaluation_error=last_qa_error,
        error='结构已通过，但两次地板结果均未通过最终硬门禁；原始图均保留在 attempts 中',
        seconds=round(time.time() - started, 1),
    )
    _persist_run(run)


async def _run_generation(run: dict, project: dict, captures: list[dict], api_key: str) -> None:
    try:
        run.update(status='running', stage='按照整屋 3D 机位并行生成', error='')
        _persist_run(run)
        capture_map = {capture['capture_id']: capture for capture in captures}
        tasks = [_generate_one(run, project, capture_map, result, api_key) for result in run['results']]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for result, outcome in zip(run['results'], outcomes):
            if isinstance(outcome, BaseException):
                logger.error(f'[整屋生成] 单结果状态机异常: {outcome}')
                result.update(
                    status='failed', outcome='failed', deliverable=False, stage='', path='',
                    final_path='', selected_attempt_id='', error=f'状态机异常: {outcome}',
                )
            elif result.get('status') in ('queued', 'running'):
                result.update(
                    status='failed', outcome='failed', deliverable=False, stage='', path='',
                    final_path='', selected_attempt_id='', error='状态机异常结束，未产生明确终态',
                )
        done = sum(result.get('status') == 'done' for result in run['results'])
        failed = sum(result.get('status') == 'failed' for result in run['results'])
        deliverable = sum(bool(result.get('deliverable')) for result in run['results'])
        if run['run_id'] in _CANCELLED:
            status = 'partial' if done else 'failed'
            error = '任务已取消'
        elif failed and done:
            status, error = 'partial', f'{failed} 个候选失败'
        elif failed:
            status, error = 'failed', '全部候选生成失败'
        else:
            status, error = 'done', ''
        run.update(
            status=status, stage='', error=error,
            summary_counts={'processed': done + failed, 'deliverable': deliverable,
                            'rejected': done - deliverable, 'failed': failed},
        )
    except Exception as ex:
        logger.exception('[整屋生成] 主任务异常')
        has_result = run_has_viewable_artifact(run)
        run.update(status='partial' if has_result else 'failed', stage='', error=str(ex))
    finally:
        _persist_run(run)
        if _is_development_run(run):
            try:
                proof = _development_claim_proof(run)
                mark_development_run_terminal(
                    str(run.get('development_session_id') or ''),
                    int(run.get('development_batch_index') or 0),
                    str(run.get('run_id') or ''),
                    str(run.get('status') or 'failed'),
                    str(run.get('error') or ''),
                    **proof,
                )
            except DevelopmentAutopilotError as ex:
                logger.warning(
                    f'[development_autopilot] 终态同步失败，run 证据仍保留: {ex}')
        try:
            ensure_run_recipes(run)
        except Exception as ex:
            logger.warning(f'[整屋学习] 生成终态 recipe 失败，原始证据仍保留: {ex}')
        _ACTIVE_RUNS.pop(run['run_id'], None)
        _RUN_KEYS.pop(run['run_id'], None)
        _DEVELOPMENT_CLAIM_PROOFS.pop(run['run_id'], None)
        _CANCELLED.discard(run['run_id'])


def _existing_idempotent_run(project_id: str, idempotency_key: str, *,
                             parent_run_id: str = '', request_fingerprint: str = '',
                             completion_event_id: str = '') -> Optional[dict]:
    if not idempotency_key:
        return None
    rows = list(_ACTIVE_RUNS.values()) + list_learning_runs(project_id)
    for row in rows:
        if str(row.get('project_id') or '') != project_id:
            continue
        if parent_run_id:
            if (str(row.get('parent_run_id') or '') == parent_run_id
                    and row.get('continuation_idempotency_key') == idempotency_key):
                if (completion_event_id and str(row.get('continuation_completion_event_id') or '')
                        != completion_event_id):
                    raise HTTPException(409, '幂等键已用于不同的人工放行事件')
                if (request_fingerprint and row.get('creation_request_fingerprint')
                        and row.get('creation_request_fingerprint') != request_fingerprint):
                    raise HTTPException(409, '幂等键已用于不同的补跑请求')
                return row
        elif row.get('creation_idempotency_key') == idempotency_key:
            if (request_fingerprint and row.get('creation_request_fingerprint')
                    and row.get('creation_request_fingerprint') != request_fingerprint):
                raise HTTPException(409, '幂等键已用于不同的生成请求')
            return row
    return None


def _creation_request_fingerprint(req: WholeHomeRunRequest, metadata: dict) -> str:
    payload = req.model_dump(exclude={'api_key'})
    payload['workflow'] = {
        key: metadata.get(key)
        for key in ('workflow_id', 'parent_run_id', 'round_index',
                    'generation_spec_hash', 'continuation_completion_event_id',
                    'execution_policy', 'development_session_id',
                    'development_batch_index', 'development_limits_snapshot',
                    'manual_preview_id', 'manual_preview_sha256',
                    'manual_call_caps')
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')).hexdigest()


def _generation_project_snapshot(project: dict) -> dict:
    """Keep immutable generation facts without duplicating CAD debug history.

    The canonical project permanently retains parse failures, raw diagnostics,
    and prior attempts.  Those multi-megabyte fields are not read by generation
    or QA retry; copying them into every frequently-updated run caused each
    status update to rewrite tens of megabytes and starved cancellation.
    """
    keys = (
        'project_id', 'source_type', 'status', 'stage', 'summary',
        'created_at', 'updated_at', 'floorplan_path', 'source_analysis_id',
        'cad_path', 'reference_url', 'reference_contract', 'model',
        'revision', 'verified', 'verified_revision', 'ai_model',
        'semantic_ai_model', 'prompt_version', 'auto_camera_plans',
        'construction_profile', 'scene_recipes', 'active_scene_recipe_id',
        'professional_revision',
    )
    snapshot = {
        key: copy.deepcopy(project.get(key))
        for key in keys if key in project
    }
    snapshot['snapshot_format'] = 'whole_home_generation_minimal_v1'
    return snapshot


async def _create_whole_home_run(req: WholeHomeRunRequest,
                                 workflow_metadata: Optional[dict] = None):
    metadata = copy.deepcopy(workflow_metadata or {})
    request_fingerprint = _creation_request_fingerprint(req, metadata)
    is_development = metadata.get('execution_policy') == _DEVELOPMENT_POLICY
    is_manual = metadata.get('execution_policy') == MANUAL_POLICY
    existing = _existing_idempotent_run(
        req.project_id, req.idempotency_key,
        parent_run_id=(
            '' if is_development else str(metadata.get('parent_run_id') or '')),
        request_fingerprint=request_fingerprint,
        completion_event_id=str(metadata.get('continuation_completion_event_id') or ''),
    )
    if existing:
        return run_view(existing)
    project = _project_entry(req.project_id)
    if not project or not project.get('verified'):
        raise HTTPException(400, '整屋模型尚未锁定')
    _assert_cad_project_gate(project)
    _assert_geometry_production_gate(project)
    scene_recipe: dict = {}
    if req.material_mode == 'style_pack':
        scene_recipe = next((
            copy.deepcopy(row) for row in project.get('scene_recipes') or []
            if str(row.get('recipe_id') or '') == req.scene_recipe_id
        ), {})
        if (not scene_recipe or scene_recipe.get('status') != 'locked'
                or str(project.get('active_scene_recipe_id') or '') != req.scene_recipe_id):
            raise HTTPException(409, {
                'code': 'locked_scene_recipe_required',
                'message': '固定风格生成必须绑定当前已锁定的 SceneRecipe',
            })
        supplied_recipe_hash = str(scene_recipe.pop('recipe_hash', ''))
        actual_recipe_hash = professional_canonical_hash(scene_recipe)
        scene_recipe['recipe_hash'] = supplied_recipe_hash
        if not supplied_recipe_hash or not hmac.compare_digest(supplied_recipe_hash, actual_recipe_hash):
            raise HTTPException(409, {'code': 'scene_recipe_hash_mismatch'})
        graph = build_floorplan_graph(project)
        profile_hash = str((project.get('construction_profile') or {}).get('profile_hash') or '')
        if (scene_recipe.get('floorplan_graph_hash') != graph.get('graph_hash')
                or scene_recipe.get('construction_profile_hash') != profile_hash):
            raise HTTPException(409, {'code': 'scene_recipe_stale'})
    if req.material_mode == 'reference' and req.style_ref_path:
        raise HTTPException(409, {
            'code': 'reference_generic_style_ref_forbidden',
            'message': 'reference 模式只能使用当前 slot 的审计资产，禁止 generic style_ref_path',
        })
    if req.material_mode == 'style_pack' and req.style_ref_path:
        raise HTTPException(409, {
            'code': 'style_pack_generic_style_ref_forbidden',
            'message': '固定风格模式只能使用锁定的版本化 StylePack，禁止临时参考图改变风格事实',
        })
    floor_path = require_upload_image_path(
        req.floor_path, '地板小样', required=req.material_mode == 'floor_sample') or ''
    project_reference = project.get('reference_contract') if isinstance(project.get('reference_contract'), dict) else {}
    if req.material_mode == 'reference':
        if not project_reference or req.reference_contract_id != project_reference.get('contract_id'):
            raise HTTPException(409, 'reference 模式必须绑定当前 CAD 项目的已审计 reference_contract')
        output_contract = project_reference.get('output') or {}
        if (req.aspect_ratio != output_contract.get('aspect_ratio')
                or req.resolution != output_contract.get('resolution')):
            raise HTTPException(409, 'reference 模式必须遵守已审计输出合同：4:3 / 4K')
        if set(req.model_keys) != {'b2', 'pro'}:
            raise HTTPException(409, 'reference benchmark 必须同时运行 B2 与 Pro，禁止静默缩减模型覆盖')
        try:
            project_reference = resolve_reference_assets(project_reference, require_all=True)
        except CadError as ex:
            raise HTTPException(ex.status_code, ex.to_dict()) from ex
        project['reference_contract'] = project_reference
    style_ref_path = require_ref_image_path(req.style_ref_path) if req.style_ref_path else ''
    capture_map = {capture.get('capture_id'): capture for capture in project.get('captures') or []}
    capture_groups: list[dict] = []
    legacy = not bool(req.capture_groups)
    if req.material_mode == 'reference' and legacy:
        raise HTTPException(409, 'reference 模式必须使用带 slot_id 的 capture_groups')
    slot_map = {
        str(row.get('slot_id') or ''): row
        for row in (project_reference.get('slots') or []) if isinstance(row, dict)
    }
    if req.capture_groups:
        for requested in req.capture_groups:
            slot = slot_map.get(requested.slot_id) if req.material_mode == 'reference' else None
            if req.material_mode == 'reference' and not slot:
                raise HTTPException(409, f'reference slot {requested.slot_id} 不在已审计合同中')
            ids = [requested.primary_capture_id, *requested.fallback_capture_ids]
            rows = []
            for capture_id in ids:
                capture = capture_map.get(capture_id)
                if not capture or not _valid_capture(project, capture, req.aspect_ratio):
                    raise HTTPException(409, f'机位缓冲 {capture_id} 已过期、缺失或画幅不一致，请重新保存')
                if req.material_mode == 'style_pack' and (
                        str(capture.get('scene_recipe_id') or '') != req.scene_recipe_id
                        or str(capture.get('scene_hash') or '') != str(scene_recipe.get('scene_hash') or '')):
                    raise HTTPException(409, {
                        'code': 'capture_scene_recipe_mismatch',
                        'message': f'机位缓冲 {capture_id} 不是从当前锁定方案生成，请重新保存机位',
                    })
                if (req.material_mode == 'reference'
                        and not os.path.isfile(str(capture.get('edge_path') or ''))):
                    raise HTTPException(409, {
                        'code': 'reference_slot_camera_missing',
                        'message': f'{requested.slot_id}: reference 模式缺少当前机位 edge buffer',
                    })
                capture_room_id = str(capture.get('room_id') or (capture.get('camera') or {}).get('room_id') or '')
                if capture_room_id != requested.room_id:
                    raise HTTPException(409, f'机位缓冲 {capture_id} 不属于房间 {requested.room_id}')
                if req.material_mode == 'reference':
                    capture_slot_id = str(capture.get('reference_slot_id') or
                                          (capture.get('camera') or {}).get('reference_slot_id') or '')
                    if capture_slot_id != requested.slot_id:
                        raise HTTPException(409, f'机位缓冲 {capture_id} 未绑定 reference slot {requested.slot_id}')
                rows.append(capture)
            primary = rows[0]
            binding_mode = ''
            actual_profile = ''
            if slot:
                room = next((row for row in (project.get('model') or {}).get('rooms') or []
                             if row.get('id') == requested.room_id), {})
                actual_profile = str(room.get('reference_room_profile') or '')
                binding_mode = _reference_room_profile_binding(
                    actual_profile, str(slot.get('room_profile') or ''))
                if not binding_mode:
                    raise HTTPException(
                        409, f'reference slot {requested.slot_id} 不能绑定房型 {actual_profile or "未解析"}')
                for capture in rows:
                    _assert_reference_slot_camera(
                        project_reference, slot, capture, requested.slot_id)
            room = next((row for row in (project.get('model') or {}).get('rooms') or [] if row.get('id') == requested.room_id), {})
            capture_groups.append({
                'room_id': requested.room_id, 'room_label': room.get('label') or requested.room_id,
                'slot_id': requested.slot_id,
                'reference_asset': copy.deepcopy(
                    (public_reference_contract({'slots': [slot]}).get('slots') or [{}])[0].get('reference_asset') or {}
                ) if slot else {},
                'reference_viewpoint': copy.deepcopy((slot or {}).get('reference_viewpoint') or {}),
                'cad_room_profile': actual_profile,
                'reference_composition_profile': str((slot or {}).get('room_profile') or ''),
                'room_profile_binding_mode': binding_mode,
                'primary_capture_id': requested.primary_capture_id,
                'fallback_capture_ids': list(requested.fallback_capture_ids),
                'primary_camera_id': primary.get('camera_id') or '',
                'camera_name': (primary.get('camera') or {}).get('name') or room.get('label') or requested.room_id,
            })
    else:
        for capture_id in req.capture_ids:
            capture = capture_map.get(capture_id)
            if not capture or not _valid_capture(project, capture, req.aspect_ratio):
                raise HTTPException(409, f'机位缓冲 {capture_id} 已过期、缺失或画幅不一致，请重新保存')
            if req.material_mode == 'style_pack' and (
                    str(capture.get('scene_recipe_id') or '') != req.scene_recipe_id
                    or str(capture.get('scene_hash') or '') != str(scene_recipe.get('scene_hash') or '')):
                raise HTTPException(409, {
                    'code': 'capture_scene_recipe_mismatch',
                    'message': f'机位缓冲 {capture_id} 不是从当前锁定方案生成，请重新保存机位',
                })
            room_id = str(capture.get('room_id') or (capture.get('camera') or {}).get('room_id') or '')
            capture_groups.append({
                'room_id': room_id, 'room_label': room_id,
                'slot_id': '',
                'primary_capture_id': capture_id, 'fallback_capture_ids': [],
                'primary_camera_id': capture.get('camera_id') or '',
                'camera_name': (capture.get('camera') or {}).get('name') or capture_id,
            })
    capture_ids = list(dict.fromkeys(
        capture_id
        for group in capture_groups
        for capture_id in [group['primary_capture_id'], *(group.get('fallback_capture_ids') or [])]
    ))
    captures = [copy.deepcopy(capture_map[capture_id]) for capture_id in capture_ids]
    run_id = new_id('run')
    results = _result_rows(req, capture_groups, legacy=legacy)
    contract_snapshots = []
    for group in capture_groups:
        primary = capture_map[group['primary_capture_id']]
        try:
            contract = build_room_generation_contract(
                project, {**primary, 'material_mode': req.material_mode})
            if project_reference:
                room = next((row for row in (project.get('model') or {}).get('rooms') or []
                             if str(row.get('id') or '') == str(group.get('room_id') or '')), {})
                slot = (copy.deepcopy(slot_map.get(str(group.get('slot_id') or '')) or {})
                        if group.get('slot_id') else
                        reference_slot_for_room(
                            project_reference, room, primary.get('camera') or {},
                            reference_slot_id=str(primary.get('reference_slot_id') or ''),
                            require_explicit=req.material_mode == 'reference'))
                if req.material_mode == 'reference' and not slot:
                    raise HTTPException(409, f"房间 {group.get('room_id')} 未绑定 9-slot reference 合同")
                if req.material_mode == 'reference':
                    slot['cad_room_binding'] = {
                        'room_id': str(group.get('room_id') or ''),
                        'cad_room_profile': str(group.get('cad_room_profile') or ''),
                        'reference_composition_profile': str(
                            group.get('reference_composition_profile') or ''),
                        'binding_mode': str(group.get('room_profile_binding_mode') or ''),
                        'geometry_authority': 'cad',
                        'instruction': (
                            'This reference slot is only an alternate composition of the same '
                            'CAD wet/dry suite; do not invent a separate room.'
                            if group.get('room_profile_binding_mode') == 'shared_cad_wet_dry_suite'
                            else 'Use this exact CAD room profile.'
                        ),
                    }
                contract['reference_contract_id'] = project_reference.get('contract_id') or ''
                contract['reference_slot'] = slot
            contract_snapshots.append(contract)
        except ValueError as ex:
            raise HTTPException(409, str(ex)) from ex
    plan_ids = {
        str(capture.get('plan_id') or '') for capture in captures if capture.get('plan_id')
    }
    camera_plan = next((
        copy.deepcopy(plan) for plan in reversed(project.get('auto_camera_plans') or [])
        if str(plan.get('plan_id') or '') in plan_ids
    ), {})
    if req.material_mode == 'reference' and not camera_plan:
        proposal_ids = {str(capture.get('reference_proposal_id') or '') for capture in captures
                        if capture.get('reference_proposal_id')}
        if len(proposal_ids) == 1:
            proposal_id = next(iter(proposal_ids))
            proposal = next((copy.deepcopy(row) for row in reversed(
                project.get('reference_camera_proposals') or [])
                if str(row.get('proposal_id') or '') == proposal_id), {})
            if proposal:
                camera_plan = {
                    'plan_id': proposal_id, 'kind': 'reference_slot_local',
                    'pool_scope': 'reference_slot', 'contract_id': proposal.get('contract_id') or '',
                    'proposal_hash': proposal.get('proposal_hash') or '',
                    'project_revision': proposal.get('project_revision'),
                    'cad_facts_hash': proposal.get('cad_facts_hash') or '',
                    'model_facts_hash': proposal.get('model_facts_hash') or '',
                    'slot_pools': copy.deepcopy(proposal.get('slot_pools') or []),
                }
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    request_input_paths = [floor_path] if floor_path else []
    request_input_paths.extend([
        str((project.get('cad_source') or {}).get('path') or ''),
        str((project.get('cad_source') or {}).get('converted_dxf_path') or ''),
        str((project.get('parse_report') or {}).get('report_path') or ''),
    ])
    if style_ref_path:
        request_input_paths.append(style_ref_path)
    for capture in captures:
        request_input_paths.extend(
            str(capture.get(f'{key}_path') or '')
            for key in ('rgb', 'depth', 'normal', 'edge', 'semantic', 'plan_overlay')
        )
    request_prompt_sha = hashlib.sha256(req.prompt.encode('utf-8')).hexdigest()
    reference_slot_ids = [str(group.get('slot_id') or '') for group in capture_groups]
    reference_asset_manifest = _reference_asset_manifest(
        project_reference, reference_slot_ids, include_path=True)
    run = {
        'run_id': run_id, 'project_id': req.project_id, 'status': 'queued', 'stage': '等待生成',
        'error': '', 'created_at': time.time(), 'updated_at': time.time(),
        'floorplan_path': project['floorplan_path'], 'floor_path': floor_path,
        'material_mode': req.material_mode,
        'scene_recipe_id': req.scene_recipe_id,
        'scene_hash': str(scene_recipe.get('scene_hash') or ''),
        'scene_recipe_snapshot': copy.deepcopy(scene_recipe),
        'reference_contract_id': req.reference_contract_id,
        'reference_contract_snapshot': copy.deepcopy(project_reference),
        'reference_asset_snapshots': _reference_asset_manifest(
            project_reference, reference_slot_ids, include_path=False),
        'benchmark_batch_id': req.benchmark_batch_id,
        'cad_source_snapshot': copy.deepcopy(project.get('cad_source') or {}),
        'cad_import_snapshot': copy.deepcopy(project.get('cad_import') or {}),
        'cad_parse_report_snapshot': copy.deepcopy(project.get('parse_report') or {}),
        'style_ref_path': style_ref_path, 'prompt': req.prompt, 'style': req.style,
        'lighting': req.lighting, 'model_keys': req.model_keys,
        'candidates_per_camera': req.candidates_per_camera, 'aspect_ratio': req.aspect_ratio,
        'resolution': req.resolution, 'capture_ids': capture_ids,
        'capture_groups': copy.deepcopy(capture_groups), 'legacy_flat_capture_mode': legacy,
        'model_revision': project.get('verified_revision'), 'model_hash': model_hash(project.get('model') or {}),
        'request_prompt_sha256': request_prompt_sha,
        'input_manifest': _input_manifest(request_input_paths) + reference_asset_manifest,
        'project_snapshot': _generation_project_snapshot(project),
        'model_snapshot': copy.deepcopy(project.get('model') or {}),
        'capture_snapshots': copy.deepcopy(captures),
        'room_contract_snapshots': contract_snapshots,
        'camera_plan_snapshot': camera_plan,
        'call_ledger': [],
        # One accepted result needs one structure and one material call. The
        # fail-closed state machine can consume four structure + two material.
        'estimated_minimum_model_calls': len(results) * 2,
        'estimated_model_calls': len(results) * 6,
        'estimated_qa_calls': len(results) * 12,
        'results': results,
    }
    computed_spec_hash = generation_spec_hash(run)
    expected_spec_hash = str(metadata.get('generation_spec_hash') or '')
    if expected_spec_hash and expected_spec_hash != computed_spec_hash:
        raise HTTPException(409, '项目模型或生成设置已变化，不能沿用上一轮人工放行')
    run.update(
        workflow_id=str(metadata.get('workflow_id') or new_id('workflow')),
        parent_run_id=str(metadata.get('parent_run_id') or ''),
        round_index=int(metadata.get('round_index') or 1),
        generation_spec_hash=computed_spec_hash,
        continuation_completion_event_id=str(
            metadata.get('continuation_completion_event_id') or ''),
        continuation_idempotency_key=(
            req.idempotency_key
            if metadata.get('parent_run_id') and not is_development else ''),
        creation_idempotency_key=(
            req.idempotency_key
            if not metadata.get('parent_run_id') or is_development else ''),
        creation_request_fingerprint=request_fingerprint,
    )
    if is_development:
        claim_proof = copy.deepcopy(metadata.get('development_claim_proof') or {})
        run.update(
            execution_policy=_DEVELOPMENT_POLICY,
            development_session_id=str(
                metadata.get('development_session_id') or ''),
            development_batch_index=int(
                metadata.get('development_batch_index') or 0),
            development_limits_snapshot=copy.deepcopy(
                metadata.get('development_limits_snapshot') or {}),
            budget_accounting_scope=BUDGET_ACCOUNTING_SCOPE,
            development_run_claim_id=str(
                claim_proof.get('run_claim_id') or ''),
            development_claim_generation=int(
                claim_proof.get('claim_generation') or 0),
            development_request_fingerprint=str(
                claim_proof.get('request_fingerprint') or ''),
        )
    elif is_manual:
        run.update(
            execution_policy=MANUAL_POLICY,
            manual_preview_id=str(metadata.get('manual_preview_id') or ''),
            manual_preview_sha256=str(
                metadata.get('manual_preview_sha256') or ''),
            manual_call_caps=copy.deepcopy(
                metadata.get('manual_call_caps') or {
                    'image_calls': MANUAL_IMAGE_CALL_CAP,
                    'qa_calls': MANUAL_QA_CALL_CAP,
                }),
        )
    if metadata.get('variant_of_run_id') or metadata.get('variant_batch_id'):
        run.update(
            variant_group_id=str(metadata.get('variant_group_id') or ''),
            variant_of_run_id=str(metadata.get('variant_of_run_id') or ''),
            variant_batch_id=str(metadata.get('variant_batch_id') or ''),
            variant_label=str(metadata.get('variant_label') or req.style)[:200],
            variant_index=int(metadata.get('variant_index') or 1),
        )
    try:
        _, replay_reference = ensure_replay_snapshot(project, run)
        run['replay_snapshot_ref'] = replay_reference
    except WholeHomeHistoryError as ex:
        raise HTTPException(ex.status_code, ex.to_dict()) from ex
    _ACTIVE_RUNS[run_id] = run
    _RUN_KEYS[run_id] = api_key
    try:
        _persist_run(run)
    except Exception:
        _ACTIVE_RUNS.pop(run_id, None)
        _RUN_KEYS.pop(run_id, None)
        raise
    if is_development:
        try:
            bind_development_run(
                str(run.get('development_session_id') or ''),
                int(run.get('development_batch_index') or 0),
                run_id, 'queued', **claim_proof)
            _DEVELOPMENT_CLAIM_PROOFS[run_id] = claim_proof
        except DevelopmentAutopilotError:
            run.update(
                status='failed', stage='',
                error='development run claim bind failed before background spawn')
            _persist_run(run)
            _ACTIVE_RUNS.pop(run_id, None)
            _RUN_KEYS.pop(run_id, None)
            raise
    generation_coro = _run_generation(run, copy.deepcopy(project), captures, api_key)
    try:
        state.spawn(generation_coro)
    except Exception as ex:
        generation_coro.close()
        run.update(status='failed', stage='', error=f'后台生成任务创建失败: {ex}')
        _persist_run(run)
        _ACTIVE_RUNS.pop(run_id, None)
        _RUN_KEYS.pop(run_id, None)
        _DEVELOPMENT_CLAIM_PROOFS.pop(run_id, None)
        if is_development:
            try:
                mark_development_run_terminal(
                    str(run.get('development_session_id') or ''),
                    int(run.get('development_batch_index') or 0),
                    run_id, 'failed', str(run.get('error') or ''),
                    **claim_proof)
            except DevelopmentAutopilotError:
                pass
        raise HTTPException(500, '后台生成任务创建失败，失败记录已保留，可使用新的幂等键重试') from ex
    return run_view(run)


@router.post('/api/whole-home/runs')
async def create_whole_home_run(req: WholeHomeRunRequest):
    return await _create_whole_home_run(req)


@router.get('/api/whole-home/manual/capabilities')
def get_whole_home_manual_capabilities():
    return manual_capabilities()


@router.post('/api/whole-home/manual/runs/preview')
def preview_whole_home_manual_run(req: WholeHomeManualRunPreviewRequest):
    project = _project_entry(req.project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    request = req.model_dump()
    request['floor_path'] = require_upload_image_path(
        req.floor_path, '地板小样', required=req.material_mode == 'floor_sample') or ''
    request['style_ref_path'] = (
        require_ref_image_path(req.style_ref_path)
        if req.style_ref_path else None)
    try:
        return create_manual_run_preview(project=project, request=request)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


@router.post('/api/whole-home/manual/runs/commit')
async def commit_whole_home_manual_run(req: WholeHomeManualRunCommitRequest):
    # The project id is read from the immutable server-side preview.  An
    # untrusted caller cannot swap project/capture/model between preview and
    # paid commit.
    try:
        project_id = get_manual_preview_project_id(req.preview_id)
        project = _project_entry(project_id)
        if not project:
            raise DevelopmentAutopilotError(
                'manual_project_not_found', '预览绑定的整屋项目不存在', 404)
        claim = claim_manual_run_commit(
            preview_id=req.preview_id,
            preview_sha256=req.preview_sha256,
            confirmation_phrase=req.confirmation_phrase,
            project=project)
        request = WholeHomeRunRequest.model_validate({
            **claim['request'], 'api_key': req.api_key,
        })
        response = await _create_whole_home_run(request, {
            'execution_policy': MANUAL_POLICY,
            'workflow_id': f'manual_{req.preview_id}',
            'manual_preview_id': req.preview_id,
            'manual_preview_sha256': req.preview_sha256,
            'manual_call_caps': claim['caps'],
        })
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex
    except BaseException:
        finish_manual_run_commit(req.preview_id, success=False)
        raise
    finish_manual_run_commit(
        req.preview_id, success=True,
        run_id=str((response or {}).get('run_id') or ''))
    return response


def _development_run_request(
        req: WholeHomeDevelopmentAutopilotRunRequest) -> WholeHomeRunRequest:
    fields = set(WholeHomeRunRequest.model_fields)
    return WholeHomeRunRequest.model_validate({
        key: value for key, value in req.model_dump().items() if key in fields
    })


async def _execute_variant_batch(batch_id: str, api_key: str = '') -> None:
    """Run exact one-camera manual-safe children serially."""
    if batch_id in _ACTIVE_VARIANT_BATCHES:
        return
    _ACTIVE_VARIANT_BATCHES.add(batch_id)
    try:
        batch = load_variant_batch(batch_id)
        if not batch or batch.get('status') in TERMINAL_BATCH_STATUSES:
            return
        batch['status'] = 'running'
        save_variant_batch(batch)
        for index in range(len(batch.get('items') or [])):
            batch = load_variant_batch(batch_id) or batch
            item = batch['items'][index]
            if batch.get('cancel_requested_at') or item.get('status') == 'cancelled':
                if item.get('status') == 'pending':
                    item['status'] = 'cancelled'
                    save_variant_batch(batch)
                continue
            if item.get('status') == 'done':
                continue
            existing_run_id = str(item.get('child_run_id') or '')
            if existing_run_id:
                existing_run = _run_entry(existing_run_id)
                if existing_run and existing_run.get('status') in ('done', 'partial'):
                    item.update(status='done', completed_at=time.time(), error='')
                    save_variant_batch(batch)
                    continue
                if existing_run and existing_run.get('status') == 'failed':
                    item.update(status='failed', completed_at=time.time(),
                                error=str(existing_run.get('error') or '子任务失败'))
                    save_variant_batch(batch)
                    continue
            style_spec = batch.get('style_spec') or {}
            request = WholeHomeRunRequest(
                project_id=str(batch.get('project_id') or ''),
                capture_ids=[str(item.get('capture_id') or '')], capture_groups=[],
                floor_path=str(style_spec.get('floor_path') or ''),
                material_mode='floor_sample', reference_contract_id='', benchmark_batch_id='',
                style_ref_path=str(style_spec.get('style_ref_path') or '') or None,
                prompt=str(style_spec.get('prompt') or ''),
                style=str(style_spec.get('style') or '现代自然'),
                lighting=str(style_spec.get('lighting') or '自然日光'),
                model_keys=[str(item.get('model_key') or 'b2')],
                candidates_per_camera=1,
                aspect_ratio=str(style_spec.get('aspect_ratio') or '4:3'),
                resolution='2K',
                idempotency_key=f'variant:{batch_id}:{item.get("item_id")}',
                api_key=api_key,
            )
            item.update(status='claimed', claimed_at=time.time(), error='')
            save_variant_batch(batch)
            try:
                response = await _create_whole_home_run(request, {
                    'execution_policy': MANUAL_POLICY,
                    'workflow_id': f'variant_{batch_id}',
                    'manual_preview_id': f'batch_{batch_id}',
                    'manual_preview_sha256': str(batch.get('preview_hash') or ''),
                    'manual_call_caps': {
                        'image_calls': MANUAL_IMAGE_CALL_CAP,
                        'qa_calls': MANUAL_QA_CALL_CAP,
                    },
                    'variant_group_id': f'variant_{batch.get("source_run_id")}',
                    'variant_of_run_id': str(batch.get('source_run_id') or ''),
                    'variant_batch_id': batch_id,
                    'variant_label': str(style_spec.get('style') or '新风格'),
                    'variant_index': index + 1,
                })
                child_run_id = str(response.get('run_id') or '')
                batch = load_variant_batch(batch_id) or batch
                item = batch['items'][index]
                item.update(status='running', child_run_id=child_run_id)
                child_ids = batch.setdefault('child_run_ids', [])
                if child_run_id and child_run_id not in child_ids:
                    child_ids.append(child_run_id)
                save_variant_batch(batch)
                while child_run_id:
                    await asyncio.sleep(1)
                    child = _run_entry(child_run_id)
                    if child and child.get('status') in ('done', 'partial', 'failed'):
                        batch = load_variant_batch(batch_id) or batch
                        item = batch['items'][index]
                        if child.get('status') in ('done', 'partial'):
                            item.update(status='done', completed_at=time.time(), error='')
                        else:
                            item.update(status='failed', completed_at=time.time(),
                                        error=str(child.get('error') or '子任务失败'))
                        save_variant_batch(batch)
                        break
            except Exception as ex:
                logger.exception('[整屋历史] 风格批次子任务失败')
                batch = load_variant_batch(batch_id) or batch
                batch['items'][index].update(
                    status='failed', completed_at=time.time(), error=str(ex)[:1000])
                save_variant_batch(batch)
        batch = load_variant_batch(batch_id) or batch
        statuses = [str(item.get('status') or '') for item in batch.get('items') or []]
        if statuses and all(status == 'done' for status in statuses):
            batch['status'] = 'done'
        elif any(status == 'done' for status in statuses):
            batch['status'] = 'partial'
        elif statuses and all(status == 'cancelled' for status in statuses):
            batch['status'] = 'cancelled'
        else:
            batch['status'] = 'failed'
        save_variant_batch(batch)
    finally:
        _ACTIVE_VARIANT_BATCHES.discard(batch_id)


@router.post('/api/whole-home/variant-batches/preview')
def preview_whole_home_variant_batch(req: WholeHomeVariantBatchPreviewRequest):
    project = _project_entry(req.project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    if not project.get('verified'):
        raise HTTPException(409, {'code': 'variant_project_not_verified',
                                  'message': '历史分支尚未通过几何生产锁'})
    _assert_cad_project_gate(project)
    _assert_geometry_production_gate(project)
    source_run = _run_entry(req.source_run_id)
    if not source_run:
        raise HTTPException(404, '来源整屋生成任务不存在')
    existing = next((row for row in list_variant_batches(req.project_id)
                     if str(row.get('creation_idempotency_key') or '') == req.idempotency_key), None)
    if existing:
        # Confirmation phrases are deliberately not recoverable.  A refreshed
        # page requests a new preview rather than weakening the proof.
        raise HTTPException(409, {'code': 'variant_preview_exists',
                                  'message': '该幂等键已经创建过预览，请使用新的预览操作'})
    try:
        batch, phrase = create_variant_preview(
            batch_id=new_id('variant_batch'), project=project, source_run=source_run,
            style_spec=req.model_dump(), excluded_artifact_ids=req.excluded_artifact_ids,
            project_state_hash=state_hash(project),
            image_call_cap=MANUAL_IMAGE_CALL_CAP, qa_call_cap=MANUAL_QA_CALL_CAP)
        batch['creation_idempotency_key'] = req.idempotency_key
        save_variant_batch(batch)
    except WholeHomeHistoryError as ex:
        _raise_history_error(ex)
    response = public_variant_batch(batch)
    response['confirmation_phrase'] = phrase
    return response


@router.get('/api/whole-home/variant-batches/{batch_id}')
def get_whole_home_variant_batch(batch_id: str):
    batch = load_variant_batch(batch_id)
    if not batch:
        raise HTTPException(404, '整套风格批次不存在')
    return public_variant_batch(batch)


@router.post('/api/whole-home/variant-batches/{batch_id}/commit')
def commit_whole_home_variant_batch(batch_id: str,
                                    req: WholeHomeVariantBatchCommitRequest):
    batch = load_variant_batch(batch_id)
    if not batch:
        raise HTTPException(404, '整套风格批次不存在')
    if not manual_paid_enabled():
        raise HTTPException(409, {'code': 'manual_paid_not_enabled',
                                  'message': '服务未使用 -AllowPaid 启动，整批付费提交保持关闭'})
    project = _project_entry(str(batch.get('project_id') or ''))
    if not project:
        raise HTTPException(404, '整屋分支项目不存在')
    was_previewed = batch.get('status') == 'previewed'
    try:
        batch = claim_variant_batch(
            batch, preview_hash=req.preview_hash,
            confirmation_phrase=req.confirmation_phrase,
            current_project_state_hash=state_hash(project))
    except WholeHomeHistoryError as ex:
        _raise_history_error(ex)
    if was_previewed or (batch_id not in _ACTIVE_VARIANT_BATCHES
                         and batch.get('status') in {'queued', 'running'}):
        state.spawn(_execute_variant_batch(batch_id, req.api_key))
    return public_variant_batch(batch)


@router.post('/api/whole-home/variant-batches/{batch_id}/cancel')
def cancel_whole_home_variant_batch(batch_id: str):
    batch = load_variant_batch(batch_id)
    if not batch:
        raise HTTPException(404, '整套风格批次不存在')
    return public_variant_batch(request_variant_cancel(batch))


def _development_request_fingerprint(
        req: WholeHomeDevelopmentAutopilotRunRequest) -> str:
    payload = req.model_dump(exclude={'api_key'})
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')).hexdigest()


def _development_budget_envelope(
        req: WholeHomeDevelopmentAutopilotRunRequest) -> dict:
    target_count = len(req.capture_groups) if req.capture_groups else len(req.capture_ids)
    result_count = target_count * len(req.model_keys) * int(req.candidates_per_camera)
    return {
        'paid_batches': 1,
        'result_count': result_count,
        'model_keys': list(req.model_keys),
        'candidates_per_camera': int(req.candidates_per_camera),
        'image_calls_min': result_count * 2,
        'image_calls_max': result_count * 6,
        'qa_calls_min': result_count * 2,
        'qa_calls_max': result_count * 12,
    }


@router.post('/api/whole-home/development-autopilot/runs')
async def create_development_autopilot_run(
        req: WholeHomeDevelopmentAutopilotRunRequest):
    """Start one explicitly enabled dev batch without mutating human review state."""
    limits = req.limits.model_dump()
    fingerprint = _development_request_fingerprint(req)
    try:
        prepare_development_batch(
            session_id=req.development_session_id,
            project_id=req.project_id,
            batch_index=req.batch_index,
            parent_run_id=req.parent_run_id,
            limits=limits,
            idempotency_key=req.idempotency_key,
            request_fingerprint=fingerprint,
        )
        claim = claim_development_run(
            session_id=req.development_session_id,
            batch_index=req.batch_index,
            request_fingerprint=fingerprint,
            budget_envelope=_development_budget_envelope(req),
        )
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex
    request = _development_run_request(req)
    try:
        response = await _create_whole_home_run(request, {
            'execution_policy': _DEVELOPMENT_POLICY,
            'workflow_id': f'development_{req.development_session_id}',
            'parent_run_id': req.parent_run_id,
            'round_index': req.batch_index,
            'development_session_id': req.development_session_id,
            'development_batch_index': req.batch_index,
            'development_limits_snapshot': limits,
            'development_claim_proof': {
                'run_claim_id': str(claim.get('run_claim_id') or ''),
                'claim_generation': int(claim.get('claim_generation') or 0),
                'claim_token': str(claim.get('claim_token') or ''),
                'request_fingerprint': fingerprint,
            },
        })
        return response
    except HTTPException as ex:
        try:
            if claim.get('claim_token'):
                mark_development_preflight_failed(
                    req.development_session_id, req.batch_index, str(ex.detail),
                    run_claim_id=str(claim.get('run_claim_id') or ''),
                    claim_generation=int(claim.get('claim_generation') or 0),
                    claim_token=str(claim.get('claim_token') or ''),
                    request_fingerprint=fingerprint)
        except DevelopmentAutopilotError:
            pass
        raise
    except DevelopmentAutopilotError as ex:
        try:
            if claim.get('claim_token'):
                mark_development_preflight_failed(
                    req.development_session_id, req.batch_index,
                    f'{ex.code}: {ex.message}',
                    run_claim_id=str(claim.get('run_claim_id') or ''),
                    claim_generation=int(claim.get('claim_generation') or 0),
                    claim_token=str(claim.get('claim_token') or ''),
                    request_fingerprint=fingerprint)
        except DevelopmentAutopilotError:
            pass
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


@router.get('/api/whole-home/development-autopilot/sessions/{session_id}')
def get_development_autopilot_session(session_id: str):
    try:
        return get_development_session(session_id)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


@router.post('/api/whole-home/development-autopilot/sessions/{session_id}/cancel')
def cancel_development_autopilot_session(session_id: str):
    try:
        session = cancel_development_session(session_id)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex
    for run_id in session.get('runs') or []:
        run = _ACTIVE_RUNS.get(str(run_id or ''))
        if not run or run.get('status') in ('done', 'partial', 'failed'):
            continue
        _CANCELLED.add(str(run_id))
        run['stage'] = 'development_autopilot 已取消；正在停止后续调用'
        _persist_run(run)
    return session


@router.post('/api/whole-home/development-autopilot/sessions/{session_id}/reconcile')
def reconcile_development_autopilot_session(
        session_id: str, req: WholeHomeDevelopmentReconcileRequest):
    try:
        session = get_development_session(session_id)
        runs = {
            str(run_id): _run_entry(str(run_id))
            for run_id in session.get('runs') or []
        }
        return reconcile_development_session(
            session_id, runs, apply=bool(req.apply),
            expected_state_version=req.expected_state_version,
            idempotency_key=req.idempotency_key)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


def _agent_workflow_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


@router.post('/api/whole-home/development-workflows')
def create_whole_home_agent_workflow(req: WholeHomeAgentWorkflowCreateRequest):
    return _agent_workflow_call(
        create_agent_workflow, **req.model_dump())


@router.get('/api/whole-home/development-workflows/{workflow_id}')
def get_whole_home_agent_workflow(workflow_id: str):
    return _agent_workflow_call(get_agent_workflow, workflow_id)


@router.post('/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/claim')
def claim_whole_home_agent_task(
        workflow_id: str, task_id: str, req: WholeHomeAgentTaskClaimRequest):
    return _agent_workflow_call(
        claim_agent_task, workflow_id=workflow_id, task_id=task_id,
        **req.model_dump())


@router.post('/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/heartbeat')
def heartbeat_whole_home_agent_task(
        workflow_id: str, task_id: str, req: WholeHomeAgentTaskHeartbeatRequest):
    return _agent_workflow_call(
        heartbeat_agent_task, workflow_id=workflow_id, task_id=task_id,
        **req.model_dump())


@router.post('/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/complete')
def complete_whole_home_agent_task(
        workflow_id: str, task_id: str, req: WholeHomeAgentTaskCompleteRequest):
    return _agent_workflow_call(
        complete_agent_task, workflow_id=workflow_id, task_id=task_id,
        **req.model_dump())


def _transition_agent_workflow_route(
        workflow_id: str, action: str,
        req: WholeHomeAgentWorkflowTransitionRequest):
    return _agent_workflow_call(
        transition_agent_workflow, workflow_id=workflow_id, action=action,
        **req.model_dump())


@router.post('/api/whole-home/development-workflows/{workflow_id}/pause')
def pause_whole_home_agent_workflow(
        workflow_id: str, req: WholeHomeAgentWorkflowTransitionRequest):
    return _transition_agent_workflow_route(workflow_id, 'pause', req)


@router.post('/api/whole-home/development-workflows/{workflow_id}/resume')
def resume_whole_home_agent_workflow(
        workflow_id: str, req: WholeHomeAgentWorkflowTransitionRequest):
    return _transition_agent_workflow_route(workflow_id, 'resume', req)


@router.post('/api/whole-home/development-workflows/{workflow_id}/cancel')
def cancel_whole_home_agent_workflow(
        workflow_id: str, req: WholeHomeAgentWorkflowTransitionRequest):
    return _transition_agent_workflow_route(workflow_id, 'cancel', req)


@router.get('/api/whole-home/development-reviews/runs/{run_id}')
def get_whole_home_external_reviews(run_id: str):
    run = _run_entry(run_id)
    if not run or not _is_development_run(run):
        raise HTTPException(404, '开发 run 不存在')
    return _agent_workflow_call(get_external_reviews, run_id)


@router.post('/api/whole-home/development-reviews/runs/{run_id}')
def review_whole_home_external_result(
        run_id: str, req: WholeHomeExternalReviewRequest):
    run = _run_entry(run_id)
    if not run or not _is_development_run(run):
        raise HTTPException(404, '开发 run 不存在')
    try:
        reviewer_context = authorize_review_lease(
            workflow_id=req.workflow_id, task_id=req.task_id,
            lease_token=req.lease_token)
        artifact_evidence = resolve_review_artifact(
            run=run, result_id=req.result_id, artifact_id=req.artifact_id)
        body = req.model_dump(exclude={'workflow_id', 'task_id', 'lease_token'})
        return record_external_review(
            run_id=run_id, reviewer_context=reviewer_context,
            artifact_evidence=artifact_evidence, **body)
    except DevelopmentAutopilotError as ex:
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex


@router.get('/api/whole-home/runs')
def get_whole_home_runs(limit: int = 30):
    rows = {entry['run_id']: entry for entry in list_runs(limit)}
    rows.update(_ACTIVE_RUNS)
    ordered = sorted(rows.values(), key=lambda item: item.get('updated_at', 0), reverse=True)
    return [_whole_home_run_list_view(item)
            for item in ordered[:max(1, min(limit, 100))]]


@router.get('/api/whole-home/runs/{run_id}')
def get_whole_home_run(run_id: str):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    return run_view(run)


@router.get('/api/whole-home/runs/{run_id}/replay')
def get_whole_home_run_replay(run_id: str):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    current = _project_entry(str(run.get('project_id') or ''))
    fallback = current or {
        **copy.deepcopy(run.get('project_snapshot') or {}),
        'project_id': str(run.get('project_id') or ''),
        'model': copy.deepcopy(run.get('model_snapshot') or {}),
        'captures': copy.deepcopy(run.get('capture_snapshots') or []),
    }
    try:
        snapshot = transient_replay_snapshot(fallback, run)
        capability = replay_capability(
            snapshot, current,
            current_model_hash=(model_hash(current.get('model') or {}) if current else ''))
    except WholeHomeHistoryError as ex:
        _raise_history_error(ex)
    history_project = _whole_home_project_view(snapshot_project(snapshot))
    history_project['history_read_only'] = True
    history_project['history_snapshot_id'] = snapshot.get('snapshot_id')
    return {
        'run': run_view(run, include_learning=False), 'snapshot': snapshot,
        'history_project': history_project, 'replay_capability': capability,
    }


@router.post('/api/whole-home/runs/{run_id}/fork')
def fork_whole_home_run(run_id: str, req: WholeHomeHistoryForkRequest):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    for candidate in list_projects(10_000):
        lineage = candidate.get('lineage') if isinstance(candidate.get('lineage'), dict) else {}
        if (str(lineage.get('source_run_id') or '') == run_id
                and str(lineage.get('idempotency_key') or '') == req.idempotency_key):
            return _whole_home_project_view(candidate)
    current = _project_entry(str(run.get('project_id') or ''))
    fallback = current or {
        **copy.deepcopy(run.get('project_snapshot') or {}),
        'project_id': str(run.get('project_id') or ''),
        'model': copy.deepcopy(run.get('model_snapshot') or {}),
        'captures': copy.deepcopy(run.get('capture_snapshots') or []),
    }
    try:
        presented_snapshot = transient_replay_snapshot(fallback, run)
        if req.source_snapshot_hash and not hmac.compare_digest(
                req.source_snapshot_hash,
                str(presented_snapshot.get('snapshot_hash') or '')):
            raise WholeHomeHistoryError(
                'history_snapshot_changed', '历史快照与页面预览不一致，请刷新后重试')
        snapshot, reference = ensure_replay_snapshot(fallback, run)
        capability = replay_capability(
            snapshot, current,
            current_model_hash=(model_hash(current.get('model') or {}) if current else ''))
        if not capability.get('can_fork'):
            raise WholeHomeHistoryError(
                'history_fork_blocked', '历史资产不完整，只能只读回看',
                details={'blockers': capability.get('blockers') or []})
        if not run.get('replay_snapshot_ref'):
            run['replay_snapshot_ref'] = reference
            save_run(run)
        branch = prepare_branch_project(
            snapshot, project_id=new_id('home'), branch_name=req.branch_name,
            idempotency_key=req.idempotency_key)
    except WholeHomeHistoryError as ex:
        _raise_history_error(ex)

    old_state = snapshot.get('project_state') if isinstance(
        snapshot.get('project_state'), dict) else {}
    old_report = old_state.get('geometry_acceptance') if isinstance(
        old_state.get('geometry_acceptance'), dict) else {}
    registration = branch.get('source_registration') if isinstance(
        branch.get('source_registration'), dict) else {}
    if registration:
        old_review = old_report.get('human_review') if isinstance(
            old_report.get('human_review'), dict) else {}
        try:
            manifest, report, _ = build_project_geometry_acceptance(
                branch, reviewer=str(old_review.get('reviewer') or 'history-fork'),
                review_note=(f"继承自 {old_report.get('report_hash') or run_id}；"
                             f"{str(old_review.get('note') or '')}")[:2000],
                assumptions_confirmed=bool(old_review.get('assumptions_confirmed')),
            )
            if report.get('status') == 'passed':
                branch['geometry_acceptance'] = report
                branch['model']['geometry_manifest'] = manifest
                branch['model']['model_facts_hash'] = manifest['model_facts_hash']
                branch.update(
                    verified=True, verified_revision=1, status='verified',
                    stage='历史模型与机位已恢复，可调整风格并创建新批次')
            else:
                branch.update(
                    verified=False, verified_revision=0,
                    status='history_revalidation_required',
                    stage='历史模型已恢复；请确认几何工程假设后再生成')
        except (GeometryContractError, ValueError) as ex:
            branch.update(
                verified=False, verified_revision=0,
                status='history_revalidation_required',
                stage='历史模型已恢复；新版几何生产锁需要重新验收',
                history_rebind_error=str(ex),
            )
    else:
        branch.update(
            verified=False, verified_revision=0,
            status='history_revalidation_required',
            stage='历史模型已恢复；旧记录缺少新版图纸配准，请重新验收')
    _persist_project(branch)
    return _whole_home_project_view(branch)


def _learning_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except WholeHomeLearningError as ex:
        raise HTTPException(ex.status_code, ex.detail) from ex


@router.post('/api/whole-home/runs/{run_id}/results/{result_id}/review')
def review_whole_home_result(run_id: str, result_id: str, req: WholeHomeResultReviewRequest):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    return _learning_call(
        review_result, run, result_id,
        artifact_id=req.artifact_id, review_status=req.review_status,
        review_tags=req.review_tags, review_note=req.review_note,
        reviewer_id=req.reviewer_id,
        expected_review_version=req.expected_review_version,
        idempotency_key=req.idempotency_key,
    )


@router.get('/api/whole-home/runs/{run_id}/review-state')
def get_whole_home_review_state(run_id: str):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    return get_run_review_state(run)


@router.post('/api/whole-home/runs/{run_id}/review-complete')
def complete_whole_home_review(run_id: str, req: WholeHomeReviewCompleteRequest):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    return _learning_call(
        complete_run_review, run, req.reviewer_id,
        expected_review_version=req.expected_review_version,
        idempotency_key=req.idempotency_key,
    )


@router.post('/api/whole-home/runs/{run_id}/continue')
async def continue_whole_home_run(run_id: str, req: WholeHomeContinueRequest):
    parent = _run_entry(run_id)
    if not parent:
        raise HTTPException(404, '整屋生成任务不存在')
    existing = _existing_idempotent_run(
        str(parent.get('project_id') or ''), req.idempotency_key,
        parent_run_id=run_id,
        completion_event_id=req.continuation_completion_event_id,
    )
    if existing:
        return run_view(existing)
    review_state = get_run_review_state(parent)
    if review_state.get('round_status') != 'review_complete':
        raise HTTPException(409, '本轮尚未获得有效的人工评审放行')
    if int(req.expected_review_version) != int(review_state.get('review_version') or 0):
        raise HTTPException(409, '人工评审版本已更新，请刷新后再继续')
    if req.continuation_completion_event_id != review_state.get('completion_event_id'):
        raise HTTPException(409, '人工放行事件已失效，请重新完成本轮评审')
    covered = set(workflow_covered_room_ids(parent))
    groups = [
        copy.deepcopy(group) for group in parent.get('capture_groups') or []
        if str(group.get('slot_id') if parent.get('material_mode') == 'reference'
               else group.get('room_id') or '') not in covered
    ]
    if not groups:
        raise HTTPException(409, '当前 workflow 的全部房间已有人工通过图片，无需补跑')
    request = WholeHomeRunRequest(
        project_id=str(parent.get('project_id') or ''),
        capture_groups=[{
            'room_id': str(group.get('room_id') or ''),
            'slot_id': str(group.get('slot_id') or ''),
            'primary_capture_id': str(group.get('primary_capture_id') or ''),
            'fallback_capture_ids': list(group.get('fallback_capture_ids') or []),
        } for group in groups],
        floor_path=str(parent.get('floor_path') or ''),
        material_mode=str(parent.get('material_mode') or 'floor_sample'),
        reference_contract_id=str(parent.get('reference_contract_id') or ''),
        benchmark_batch_id=str(parent.get('benchmark_batch_id') or ''),
        style_ref_path=str(parent.get('style_ref_path') or '') or None,
        prompt=str(parent.get('prompt') or ''),
        style=str(parent.get('style') or '现代自然'),
        lighting=str(parent.get('lighting') or '自然日光'),
        model_keys=list(parent.get('model_keys') or ['b2', 'pro']),
        candidates_per_camera=int(parent.get('candidates_per_camera') or 1),
        aspect_ratio=str(parent.get('aspect_ratio') or '4:3'),
        resolution=str(parent.get('resolution') or '4K'),
        idempotency_key=req.idempotency_key,
        api_key=req.api_key,
    )
    return await _create_whole_home_run(request, {
        'workflow_id': str(parent.get('workflow_id') or parent.get('run_id') or ''),
        'parent_run_id': run_id,
        'round_index': int(parent.get('round_index') or 1) + 1,
        'generation_spec_hash': str(
            parent.get('generation_spec_hash') or generation_spec_hash(parent)),
        'continuation_completion_event_id': req.continuation_completion_event_id,
    })


@router.post('/api/whole-home/projects/{project_id}/training-consent')
def update_whole_home_training_consent(project_id: str, req: WholeHomeTrainingConsentRequest):
    project = _project_entry(project_id)
    if not project:
        raise HTTPException(404, '整屋项目不存在')
    return set_training_consent(project, req.allowed, req.reviewer_id)


@router.get('/api/whole-home-learning/summary')
def get_whole_home_learning_summary(project_id: str = ''):
    if project_id and not _project_entry(project_id):
        raise HTTPException(404, '整屋项目不存在')
    return learning_summary(project_id)


@router.get('/api/whole-home-learning/export')
def export_whole_home_learning(project_id: str = ''):
    path = _learning_call(build_learning_export, project_id)
    return FileResponse(
        path, media_type='application/zip', filename=os.path.basename(path),
        headers={'X-Whole-Home-Learning-Export': os.path.basename(path)},
    )


@router.post('/api/whole-home/runs/{run_id}/qa/retry')
async def retry_whole_home_qa(run_id: str, req: WholeHomeQaRetryRequest):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    if run.get('status') not in ('done', 'partial', 'failed'):
        raise HTTPException(409, '任务仍在生成，请等待完成后再补评')
    project = runtime_project_copy(run.get('project_snapshot') or {}) or _project_entry(str(run.get('project_id') or ''))
    if not project:
        raise HTTPException(409, '整屋项目记录不存在，无法补评')
    api_key = (req.api_key or '').strip() or (load_config().get('gemini_api_key') or '').strip()
    if not api_key:
        raise HTTPException(400, '未配置 Gemini API Key')
    requested = set(req.result_ids or [])
    capture_map = {
        row.get('capture_id'): row
        for row in (run.get('capture_snapshots') or project.get('captures') or [])
    }
    targets = []
    for result in run.get('results') or []:
        evaluation = result.get('evaluation') if isinstance(result.get('evaluation'), dict) else {}
        if requested and result.get('result_id') not in requested:
            continue
        if not requested and evaluation.get('status') != 'unavailable':
            continue
        selected_attempt = next((
            row for row in result.get('attempts') or []
            if row.get('attempt_id') == result.get('selected_attempt_id')
        ), None)
        if not selected_attempt:
            selected_attempt = next((
                row for row in reversed(result.get('attempts') or [])
                if row.get('structure_path')
            ), None)
        material_row = (selected_attempt.get('material_attempts') or [])[-1] if selected_attempt and selected_attempt.get('material_attempts') else None
        capture_id = (selected_attempt or {}).get('capture_id') or result.get('capture_id')
        capture = capture_map.get(capture_id)
        result_path = (material_row or {}).get('final_path') or result.get('final_path') or result.get('path')
        structure_path = (selected_attempt or {}).get('structure_path') or result.get('structure_path') or ''
        material_path = (material_row or {}).get('api_original_path') or result.get('api_original_path') or ''
        if capture and result_path and os.path.isfile(result_path):
            targets.append((result, capture, result_path, structure_path, material_path, selected_attempt, material_row))
    if not targets:
        raise HTTPException(400, '没有可补评的终图；默认只补评 QA unavailable 的结果')

    batch_id = new_id('qa_retry')
    run['stage'] = f'正在补评 {len(targets)} 个 QA 结果'
    _persist_run(run)
    try:
        for index, (result, capture, result_path, structure_path, material_path, selected_attempt, material_row) in enumerate(targets, 1):
            run['stage'] = f'正在补评 QA {index}/{len(targets)} · {result.get("camera_name") or result.get("result_id")}'
            _persist_run(run)
            previous = copy.deepcopy(result.get('evaluation'))
            previous_error = str(result.get('evaluation_error') or '')
            evaluation, qa_error, attempts = await _evaluate_with_retries(
                api_key, project, {**capture, 'material_mode': run.get('material_mode') or 'floor_sample'},
                result_path, run['floor_path'], phase='final',
                structure_path=structure_path, material_path=material_path,
                run=run, result=result, attempt_row=selected_attempt)
            result.setdefault('qa_history', []).append({
                'batch_id': batch_id, 'at': time.time(), 'previous_evaluation': previous,
                'previous_error': previous_error, 'attempts': attempts,
            })
            result.update(
                evaluation=evaluation, evaluation_error=qa_error or '',
                qa_attempts=(result.get('qa_attempts') or []) + attempts,
            )
            if material_row is not None:
                material_row.update(
                    evaluation=evaluation, evaluation_error=qa_error or '',
                    qa_attempts=(material_row.get('qa_attempts') or []) + attempts,
                    status='accepted' if evaluation.get('gate_pass') else 'rejected',
                )
            if evaluation.get('gate_pass') and selected_attempt and material_row:
                selected_attempt['status'] = 'accepted'
                result.update(
                    status='done', outcome='accepted', deliverable=True, error='',
                    selected_attempt_id=selected_attempt.get('attempt_id') or '',
                    capture_id=selected_attempt.get('capture_id') or result.get('capture_id'),
                    structure_path=structure_path, api_original_path=material_path,
                    material_path=material_path,
                    corrected_path=material_row.get('corrected_path') or '',
                    final_path=result_path, path=result_path,
                )
            _persist_run(run)
    except DevelopmentAutopilotError as ex:
        run.update(
            error=f'{ex.code}: {ex.message}',
            development_stop_reason=f'{ex.code}: {ex.message}',
        )
        _persist_run(run)
        raise HTTPException(ex.status_code, _development_error_detail(ex)) from ex
    finally:
        run['stage'] = ''
        _persist_run(run)
    return run_view(run)


@router.post('/api/whole-home/runs/{run_id}/cancel')
def cancel_whole_home_run(run_id: str):
    run = _run_entry(run_id)
    if not run:
        raise HTTPException(404, '整屋生成任务不存在')
    if run.get('status') in ('done', 'partial', 'failed'):
        return {'cancelled': False, 'status': run.get('status')}
    _CANCELLED.add(run_id)
    run['stage'] = '正在停止后续调用'
    _persist_run(run)
    return {'cancelled': True, 'status': run.get('status')}
