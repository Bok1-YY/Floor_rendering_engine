"""HTTP 路由契约快照 —— 重构安全网。

Next.js 前端(web/)依赖这 67 个端点的 (path, method) 契约。
本测试固化端点全集:任何拆分/搬移导致端点丢失、路径改动、方法改动都会在此失败。

新增端点属正常演进 —— 把新条目加进 EXPECTED_ROUTES 即可;
但**修改或删除**既有条目前必须确认前端已同步。
"""

from fastapi.routing import APIRoute

from Floor_engine_server.server_api import app

EXPECTED_ROUTES = [
    ("/api/color-match/preview", "POST"),
    ("/api/config", "GET"),
    ("/api/config", "PUT"),
    ("/api/connection/test", "GET"),
    ("/api/failure/classify", "POST"),
    ("/api/failure/rules", "GET"),
    ("/api/floor-visualize/apply", "POST"),
    ("/api/floor-visualize/preview", "POST"),
    ("/api/floor/analyze", "GET"),
    ("/api/healthz", "GET"),
    ("/api/inpaint", "POST"),
    ("/api/inpaint/comfyui/ping", "GET"),
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
    ("/api/records/load", "GET"),
    ("/api/records/result/delete", "POST"),
    ("/api/records/result/favorite", "POST"),
    ("/api/records/result/review", "POST"),
    ("/api/records/reveal", "POST"),
    ("/api/review/gallery", "GET"),
    ("/api/review/summary", "GET"),
    ("/api/swatches/recent", "GET"),
    ("/api/uploads/floor", "POST"),
    ("/api/uploads/logo", "POST"),
    ("/api/uploads/logo/clear", "POST"),
    ("/api/uploads/ref", "POST"),
    ("/api/uploads/room", "POST"),
    ("/api/usage", "GET"),
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
    assert len(_actual_routes()) == 67
