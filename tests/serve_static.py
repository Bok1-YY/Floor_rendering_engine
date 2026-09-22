"""Isolated Python-hosted static export for browser regression tests."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

import uvicorn
from fastapi import FastAPI

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='floor-browser-') as data_dir:
    os.environ['FLOOR_DATA_DIR'] = data_dir
    spec = importlib.util.spec_from_file_location('Floor_engine_server', root / '__init__.py', submodule_search_locations=[str(root)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    from Floor_engine_server.server_api import NextStaticExportFiles
    from Floor_engine_server.routes_config import options
    from Floor_engine_server import routes_jobs, server_state, records, submission_store, server_helpers
    from Floor_engine_server.models import update_job
    from PIL import Image
    server_state.init_runtime(1)
    calls = []
    routes_jobs.load_config = lambda: {'gemini_api_key': 'offline-browser-fixture'}
    async def offline_worker(job, _request):
        calls.append(job.job_id)
        update_job(job, status='done')
        server_state.JOBS.persist()
    routes_jobs._run_job_bg = offline_worker
    routes_jobs._run_free_job_bg = offline_worker
    app = FastAPI()
    app.get("/api/options")(options)
    app.include_router(routes_jobs.router)
    @app.get('/api/healthz')
    def health():
        return {'ok': True, 'submissions': submission_store.health()}
    @app.post('/__test/reset')
    def reset():
        server_state.JOBS.replace([])
        calls.clear()
        source = Path(server_helpers.UPLOAD_DIR) / 'browser-floor.png'
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (32, 32), '#987755').save(source)
        return {'image_path': str(source), 'store_id': submission_store.identity()}
    @app.get('/__test/submissions')
    def evidence():
        return {'calls': calls, 'receipts': [__import__('json').loads(p.read_text(encoding='utf-8')) for p in submission_store.folder().glob('*/*.json')]}
    @app.get('/api/{unused:path}')
    def empty_api(unused: str):
        return {} if unused == 'config' else []
    app.mount('/', NextStaticExportFiles(directory=root / 'web' / 'out', html=True))
    uvicorn.run(app, host='127.0.0.1', port=8799)
