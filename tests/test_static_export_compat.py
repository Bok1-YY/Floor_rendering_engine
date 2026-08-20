import asyncio
import os

from Floor_engine_server.server_api import NextStaticExportFiles


def test_next_static_export_flat_page_payload_maps_to_nested_file(tmp_path):
    payload_dir = tmp_path / 'settings' / '__next.settings'
    payload_dir.mkdir(parents=True)
    (payload_dir / '__PAGE__.txt').write_text('next-rsc-payload', encoding='utf-8')
    (tmp_path / 'index.html').write_text('<html>home</html>', encoding='utf-8')
    (tmp_path / '404.html').write_text('<html>missing</html>', encoding='utf-8')
    files = NextStaticExportFiles(directory=str(tmp_path), html=True)
    scope = {'type': 'http', 'method': 'GET', 'headers': [], 'path': '/'}
    response = asyncio.run(files.get_response(
        os.path.join('settings', '__next.settings.__PAGE__.txt'), scope))
    unrelated = asyncio.run(files.get_response(
        os.path.join('settings', '__next.other.__PAGE__.txt'), scope))

    assert response.status_code == 200
    assert os.path.samefile(response.path, payload_dir / '__PAGE__.txt')
    assert unrelated.status_code == 404
