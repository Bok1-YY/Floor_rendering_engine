import zipfile
from pathlib import Path
import pytest
from tools.fastloop_research import engine

def test_blender_source_resource_materializes_only_in_frozen_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, '_running_frozen', lambda: False)
    assert engine._blender_python_sources(tmp_path) == tmp_path
    with zipfile.ZipFile(tmp_path / 'blender-runtime.zip', 'w') as archive:
        archive.writestr('tools/__init__.py', '')
        archive.writestr('tools/fastloop_research/contract.py', 'value = 1\n')
    monkeypatch.setattr(engine, '_running_frozen', lambda: True)
    result = engine._blender_python_sources(tmp_path)
    assert result == tmp_path / '_blender_sources/tools/fastloop_research'
    assert (result / 'contract.py').read_text() == 'value = 1\n'
    assert engine._blender_python_sources(tmp_path) == result

@pytest.mark.parametrize('name', ['../escape.py', 'tools/unexpected.txt'])
def test_blender_source_archive_rejects_unsafe_entries(tmp_path, monkeypatch, name):
    with zipfile.ZipFile(tmp_path / 'blender-runtime.zip', 'w') as archive:
        archive.writestr(name, 'bad')
    monkeypatch.setattr(engine, '_running_frozen', lambda: True)
    with pytest.raises(ValueError, match='Invalid bundled'):
        engine._blender_python_sources(tmp_path)
