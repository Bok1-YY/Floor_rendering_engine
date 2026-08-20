"""HTTP 路由契约快照 —— 重构安全网。

Next.js 前端(web/)依赖这里登记的全部端点 (path, method) 契约。
本测试固化端点全集:任何拆分/搬移导致端点丢失、路径改动、方法改动都会在此失败。

新增端点属正常演进 —— 把新条目加进 EXPECTED_ROUTES 即可;
但**修改或删除**既有条目前必须确认前端已同步。
"""

from fastapi.routing import APIRoute

from Floor_engine_server.server_api import app

EXPECTED_ROUTES = [
    ("/api/film/analyze", "POST"),
    ("/api/color-match/segment", "POST"),
    ("/api/color-match/preview", "POST"),
    ("/api/config", "GET"),
    ("/api/config", "PUT"),
    ("/api/connection/test", "GET"),
    ("/api/failure/classify", "POST"),
    ("/api/failure/rules", "GET"),
    ("/api/floor-visualize/apply", "POST"),
    ("/api/floor-visualize/preview", "POST"),
    ("/api/floorplan-dataset/export", "GET"),
    ("/api/floorplan-dataset/summary", "GET"),
    ("/api/floorplan-suites", "GET"),
    ("/api/floorplan-suites", "POST"),
    ("/api/floorplan-suites/{suite_id}", "GET"),
    ("/api/floorplan-suites/{suite_id}/anchor", "POST"),
    ("/api/floorplan-suites/{suite_id}/cancel", "POST"),
    ("/api/floorplan-suites/{suite_id}/color-match", "POST"),
    ("/api/floorplan-suites/{suite_id}/rooms/{room_id}/retry", "POST"),
    ("/api/floorplan-suites/{suite_id}/results/{result_id}/review", "POST"),
    ("/api/floorplan-suites/{suite_id}/stream", "GET"),
    ("/api/floorplans/analyze", "POST"),
    ("/api/floorplans", "GET"),
    ("/api/floorplans/manual", "POST"),
    ("/api/floorplans/{analysis_id}", "GET"),
    ("/api/floorplans/{analysis_id}", "PUT"),
    ("/api/floorplans/{analysis_id}/draft", "PUT"),
    ("/api/floorplans/{analysis_id}/history", "GET"),
    ("/api/floorplans/{analysis_id}/spatial-plans/generate", "POST"),
    ("/api/floorplans/{analysis_id}/spatial-plans/{camera_id}", "PUT"),
    ("/api/floorplans/{analysis_id}/training-consent", "POST"),
    ("/api/floorplans/{analysis_id}/view-proxies/{camera_id}/confirm", "POST"),
    ("/api/floorplans/{analysis_id}/verify", "POST"),
    ("/api/floor/analyze", "GET"),
    ("/api/healthz", "GET"),
    ("/api/inpaint", "POST"),
    ("/api/inpaint/comfyui/ping", "GET"),
    ("/api/inpaint/segment", "POST"),
    ("/api/inpaint/{iid}", "GET"),
    ("/api/inpaint/{iid}/apply", "POST"),
    ("/api/inpaint/{iid}/cancel", "POST"),
    ("/api/jobs", "GET"),
    ("/api/jobs", "POST"),
    ("/api/jobs/free", "POST"),
    ("/api/jobs/cancel-all", "POST"),
    ("/api/jobs/clear-completed", "POST"),
    ("/api/jobs/{jid}", "GET"),
    ("/api/jobs/{jid}/cancel", "POST"),
    ("/api/jobs/{jid}/color-match", "POST"),
    ("/api/jobs/{jid}/delete", "POST"),
    ("/api/jobs/{jid}/edit", "POST"),
    ("/api/jobs/{jid}/polish", "POST"),
    ("/api/jobs/{jid}/panorama/commit", "POST"),
    ("/api/jobs/{jid}/panorama/floor/apply", "POST"),
    ("/api/jobs/{jid}/panorama/floor/prepare", "POST"),
    ("/api/jobs/{jid}/panorama/floor/preview", "POST"),
    ("/api/jobs/{jid}/panorama/preview", "POST"),
    ("/api/jobs/{jid}/panorama/review", "POST"),
    ("/api/jobs/panorama-direct/commit", "POST"),
    ("/api/jobs/panorama-direct/preview", "POST"),
    ("/api/records/panorama/floor/apply", "POST"),
    ("/api/records/panorama/floor/prepare", "POST"),
    ("/api/records/panorama/floor/preview", "POST"),
    ("/api/jobs/{jid}/regen", "POST"),
    ("/api/jobs/{jid}/result", "GET"),
    ("/api/jobs/{jid}/retry", "POST"),
    ("/api/jobs/{jid}/sd-upscale", "POST"),
    ("/api/jobs/{jid}/stream", "GET"),
    ("/api/models", "GET"),
    ("/api/omakase/scenes", "POST"),
    ("/api/options", "GET"),
    ("/api/preview", "POST"),
    ("/api/preview/{pid}", "GET"),
    ("/api/preview/{pid}/cancel", "POST"),
    ("/api/recipes", "GET"),
    ("/api/recipes/custom", "GET"),
    ("/api/recipes/custom", "POST"),
    ("/api/recipes/custom/{rid}/delete", "POST"),
    ("/api/recipes/custom/{rid}/update", "POST"),
    ("/api/records", "GET"),
    ("/api/records/color-match", "POST"),
    ("/api/records/delete", "POST"),
    ("/api/records/edit", "POST"),
    ("/api/records/export/favorites-pptx", "GET"),
    ("/api/records/export/html", "GET"),
    ("/api/records/export/pptx", "GET"),
    ("/api/records/geometry-audits", "GET"),
    ("/api/records/geometry-audit/artifact", "GET"),
    ("/api/records/geometry-audit/review", "POST"),
    ("/api/records/load", "GET"),
    ("/api/records/result/delete", "POST"),
    ("/api/records/result/favorite", "POST"),
    ("/api/records/result/review", "POST"),
    ("/api/records/reveal", "POST"),
    ("/api/review/gallery", "GET"),
    ("/api/review/summary", "GET"),
    ("/api/swatches/recent", "GET"),
    ("/api/uploads/floor", "POST"),
    ("/api/uploads/film", "POST"),
    ("/api/uploads/cad", "POST"),
    ("/api/uploads/floorplan", "POST"),
    ("/api/uploads/logo", "POST"),
    ("/api/uploads/logo/clear", "POST"),
    ("/api/uploads/ref", "POST"),
    ("/api/uploads/room", "POST"),
    ("/api/usage", "GET"),
    ("/api/whole-home/projects", "GET"),
    ("/api/whole-home/projects", "POST"),
    ("/api/whole-home/cad/status", "GET"),
    ("/api/whole-home/projects/{project_id}", "GET"),
    ("/api/whole-home/professional/capabilities", "GET"),
    ("/api/whole-home/projects/{project_id}/floorplan-graph", "GET"),
    ("/api/whole-home/projects/{project_id}/construction-profile", "GET"),
    ("/api/whole-home/projects/{project_id}/construction-profile", "PUT"),
    ("/api/whole-home/projects/{project_id}/scene-recipes", "GET"),
    ("/api/whole-home/projects/{project_id}/scene-recipes", "POST"),
    ("/api/whole-home/projects/{project_id}/scene-recipes/preview", "POST"),
    ("/api/whole-home/projects/{project_id}/scene-recipes/{recipe_id}/review", "POST"),
    ("/api/whole-home/projects/{project_id}/marketing-proposal", "GET"),
    ("/api/whole-home/projects/{project_id}/generation-draft", "GET"),
    ("/api/whole-home/projects/{project_id}/generation-draft", "PUT"),
    ("/api/whole-home/projects/{project_id}/history", "GET"),
    ("/api/whole-home/projects/{project_id}/cad/ai-assist", "POST"),
    ("/api/whole-home/projects/{project_id}/cad/reparse", "POST"),
    ("/api/whole-home/projects/{project_id}/cad/reparse/{operation_id}", "GET"),
    ("/api/whole-home/projects/{project_id}/cad/report", "GET"),
    ("/api/whole-home/projects/{project_id}/cad/space-draft", "GET"),
    ("/api/whole-home/projects/{project_id}/cad/space-draft", "PUT"),
    ("/api/whole-home/projects/{project_id}/cad/semantic-reconstruct", "POST"),
    ("/api/whole-home/projects/{project_id}/cad/opening-annotations", "PUT"),
    ("/api/whole-home/projects/{project_id}/cad/wall-assemblies/{assembly_id}/confirm", "POST"),
    ("/api/whole-home/projects/{project_id}/captures", "POST"),
    ("/api/whole-home/projects/{project_id}/camera-candidates", "POST"),
    ("/api/whole-home/projects/{project_id}/reference-captures", "POST"),
    ("/api/whole-home/projects/{project_id}/camera-plans", "POST"),
    ("/api/whole-home/projects/{project_id}/pano-captures", "POST"),
    ("/api/whole-home/projects/{project_id}/pano-hotspots", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/edit", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/gate", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/paid-preview", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/materialize", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/repair", "POST"),
    ("/api/whole-home/projects/{project_id}/panos/{pano_id}/review", "POST"),
    ("/api/whole-home/projects/{project_id}/reference-assets/{slot_id}", "GET"),
    ("/api/whole-home/projects/{project_id}/cad/candidates/{candidate_id}/preview", "GET"),
    ("/api/whole-home/projects/{project_id}/model", "PUT"),
    ("/api/whole-home/projects/{project_id}/source-registration", "PUT"),
    ("/api/whole-home/projects/{project_id}/source-registration/raster", "POST"),
    ("/api/whole-home/projects/{project_id}/geometry-acceptance", "GET"),
    ("/api/whole-home/projects/{project_id}/geometry-acceptance", "POST"),
    ("/api/whole-home/projects/{project_id}/geometry-manifest", "GET"),
    ("/api/whole-home/projects/{project_id}/semantic-layout", "POST"),
    ("/api/whole-home/projects/{project_id}/training-consent", "POST"),
    ("/api/whole-home/projects/{project_id}/verify", "POST"),
    ("/api/whole-home/development-autopilot/runs", "POST"),
    ("/api/whole-home/development-autopilot/sessions/{session_id}", "GET"),
    ("/api/whole-home/development-autopilot/sessions/{session_id}/cancel", "POST"),
    ("/api/whole-home/development-autopilot/sessions/{session_id}/reconcile", "POST"),
    ("/api/whole-home/development-reviews/runs/{run_id}", "GET"),
    ("/api/whole-home/development-reviews/runs/{run_id}", "POST"),
    ("/api/whole-home/development-workflows", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}", "GET"),
    ("/api/whole-home/development-workflows/{workflow_id}/cancel", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}/pause", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}/resume", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/claim", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/complete", "POST"),
    ("/api/whole-home/development-workflows/{workflow_id}/tasks/{task_id}/heartbeat", "POST"),
    ("/api/whole-home-learning/export", "GET"),
    ("/api/whole-home-learning/summary", "GET"),
    ("/api/whole-home/manual/capabilities", "GET"),
    ("/api/whole-home/manual/runs/commit", "POST"),
    ("/api/whole-home/manual/runs/preview", "POST"),
    ("/api/whole-home/runs", "GET"),
    ("/api/whole-home/runs", "POST"),
    ("/api/whole-home/runs/{run_id}", "GET"),
    ("/api/whole-home/runs/{run_id}/cancel", "POST"),
    ("/api/whole-home/runs/{run_id}/continue", "POST"),
    ("/api/whole-home/runs/{run_id}/fork", "POST"),
    ("/api/whole-home/runs/{run_id}/qa/retry", "POST"),
    ("/api/whole-home/runs/{run_id}/replay", "GET"),
    ("/api/whole-home/runs/{run_id}/results/{result_id}/review", "POST"),
    ("/api/whole-home/runs/{run_id}/review-complete", "POST"),
    ("/api/whole-home/runs/{run_id}/review-state", "GET"),
    ("/api/whole-home/variant-batches/preview", "POST"),
    ("/api/whole-home/variant-batches/{batch_id}", "GET"),
    ("/api/whole-home/variant-batches/{batch_id}/cancel", "POST"),
    ("/api/whole-home/variant-batches/{batch_id}/commit", "POST"),
    ("/outputs/{relpath:path}", "GET"),
    ("/thumb/outputs/{relpath:path}", "GET"),
    ("/thumb/uploads/{name}", "GET"),
    ("/uploads/{name}", "GET"),
]


def _iter_api_routes(routes):
    """递归展开:FastAPI 0.139 的 include_router 把子路由包成 _IncludedRouter
    (经 original_router 暴露),不平铺进 app.routes。"""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, 'original_router'):
            yield from _iter_api_routes(r.original_router.routes)
        else:
            yield from _iter_api_routes(getattr(r, 'routes', []) or [])


def _actual_routes():
    return sorted(
        (r.path, m)
        for r in _iter_api_routes(app.routes)
        for m in r.methods
    )


def test_route_contract_unchanged():
    actual = _actual_routes()
    expected = sorted(EXPECTED_ROUTES)
    missing = set(expected) - set(actual)
    added = set(actual) - set(expected)
    assert not missing, f"端点丢失(前端会 404):{sorted(missing)}"
    assert not added, f"出现未入册的新端点,请加入 EXPECTED_ROUTES:{sorted(added)}"


def test_route_count():
    assert len(_actual_routes()) == 192
