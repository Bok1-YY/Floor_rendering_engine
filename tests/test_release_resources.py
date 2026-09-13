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

def test_ifc_parser_resource_is_materialized_and_not_overwritten(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    package = tmp_path / 'package'
    package.mkdir()
    monkeypatch.setattr(engine, '__file__', str(package / 'tools/fastloop_research/engine.py'))
    library = tmp_path / 'ifcopenshell'
    library.mkdir()
    monkeypatch.setitem(sys.modules, 'ifcopenshell', SimpleNamespace(__file__=str(library/'__init__.py')))
    with zipfile.ZipFile(package / 'ifc-parser.zip', 'w') as archive:
        archive.writestr('express_parser.py', 'parser_version = 1\n')
    engine._prepare_ifc_parser_resource()
    parser = library/'express/express_parser.py'
    assert parser.read_text() == 'parser_version = 1\n'
    parser.write_text('existing parser\n')
    engine._prepare_ifc_parser_resource()
    assert parser.read_text() == 'existing parser\n'

def test_ifc_parser_resource_rejects_unexpected_entries(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    package=tmp_path/'package';package.mkdir()
    monkeypatch.setattr(engine, '__file__', str(package/'tools/fastloop_research/engine.py'))
    monkeypatch.setitem(sys.modules, 'ifcopenshell', SimpleNamespace(__file__=str(tmp_path/'library/__init__.py')))
    with zipfile.ZipFile(package/'ifc-parser.zip','w') as archive:
        archive.writestr('../bad.py','bad')
    with pytest.raises(ValueError, match='Invalid IFC'):
        engine._prepare_ifc_parser_resource()
