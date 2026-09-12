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
    app = FastAPI()
    app.get("/api/options")(options)
    app.mount('/', NextStaticExportFiles(directory=root / 'web' / 'out', html=True))
    uvicorn.run(app, host='127.0.0.1', port=8799)
