"""Build the Windows portable executable from a clean, identified Git commit.

All disposable paths live below a marked work directory. This command never
removes an existing build or user data; successful release cleanup is explicit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'floor-engine-release-v1'

def run(args, *, cwd=ROOT, env=None):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)

def output(args, cwd=ROOT):
    return subprocess.check_output([str(a) for a in args], cwd=cwd, text=True).strip()

def prepare_compiler(work: Path, python: Path, env: dict) -> dict:
    """Fail early on compiler setup; work around the pinned MinGW header lookup."""
    overlay = work / 'compiler-headers'
    overlay.mkdir(exist_ok=True)
    probe = work / 'compiler_probe.py'
    probe.write_text("print('compiler probe ok')\n", encoding='utf-8')
    copied = {}
    for attempt in range(2):
        headers = list((work / 'compiler-cache' / 'downloads' / 'gcc').glob('**/include/structuredquerycondition.h'))
        if headers:
            header = headers[0]
            shutil.copy2(header, overlay / header.name)
            env['CPATH'] = str(overlay)
            copied = {'name': header.name, 'sha256': hashlib.sha256(header.read_bytes()).hexdigest()}
        command = [python, '-m', 'nuitka', '--mode=onefile', '--mingw64', '--jobs=2', '--low-memory',
                   '--assume-yes-for-downloads', f'--output-dir={work / "compiler-probe"}', probe]
        with (work / f'compiler-probe-{attempt}.log').open('wb') as stream:
            result = subprocess.run([str(x) for x in command], cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode == 0:
            return copied
    raise SystemExit(f'Compiler preflight failed; inspect logs under {work}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--non-interactive', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Build with Windows x64 Python 3.12.')
    if output(['git', 'status', '--porcelain', '--untracked-files=normal']):
        raise SystemExit('Commit source/documentation changes before release compilation.')
    work = (args.work_dir or ROOT / '.tools' / f'release-{time.strftime("%Y%m%d-%H%M%S")}').resolve()
    marker = work / 'ownership.json'
    if work.exists() and (not marker.exists() or json.loads(marker.read_text(encoding='utf-8-sig')).get('owner') != MARKER):
        raise SystemExit('Refusing to reuse an unmarked work directory.')
    work.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'owner': MARKER, 'root': str(work), 'purpose': 'build and isolated verification'}, indent=2), encoding='utf-8')
    sha = output(['git', 'rev-parse', 'HEAD'])
    attempt = work / f'build-{sha[:12]}-{time.strftime("%H%M%S")}'
    attempt.mkdir(exist_ok=False)
    source = attempt / 'source'; source.mkdir()
    archive = attempt / 'source.zip'
    run(['git', 'archive', '--format=zip', f'--output={archive}', sha])
    with zipfile.ZipFile(archive) as z: z.extractall(source)
    version = (source / 'VERSION').read_text(encoding='ascii').strip()
    if len(version.split('.')) != 3 or not all(part.isdigit() for part in version.split('.')):
        raise SystemExit('VERSION must be a three-part numeric release version.')
    buildenv = work / 'buildenv'
    python = buildenv / 'Scripts' / 'python.exe'
    if not python.exists(): run([sys.executable, '-m', 'venv', buildenv])
    install_env = os.environ.copy()
    install_env['PIP_CACHE_DIR'] = str(work / 'pip-cache')
    install_env['npm_config_cache'] = str(work / 'npm-cache')
    run([python, '-c', 'import sys; assert sys.version_info[:2] == (3, 12)'])
    run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', source / 'requirements-build.txt', '-r', source / 'requirements.txt'], env=install_env)
    run([python, '-m', 'pip', 'check'])
    dependency_modules = json.loads(output([python, '-c', 'import importlib.metadata,json;print(json.dumps(sorted(importlib.metadata.packages_distributions())))']))
    dependency_modules = [name for name in dependency_modules if name not in ('nuitka', 'pytest', 'Floor_engine_server', 'anyio')]
    npm = shutil.which('npm.cmd')
    if not npm: raise SystemExit('Node.js 20.9+ and npm are required.')
    run([npm, 'ci'], cwd=source / 'web', env=install_env)
    run([npm, 'run', 'build'], cwd=source / 'web', env=install_env)
    package = attempt / 'stage' / 'Floor_engine_server'; package.mkdir(parents=True)
    for file in source.glob('*.py'): shutil.copy2(file, package / file.name)
    shutil.copytree(source / 'providers', package / 'providers')
    calibrator = package / 'standalone_color_calibrator'; calibrator.mkdir()
    for name in ['__init__.py', 'advanced.py', 'engine.py']: shutil.copy2(source / 'standalone_color_calibrator' / name, calibrator / name)
    (package / 'tools').mkdir(); (package / 'tools' / '__init__.py').write_text('', encoding='utf-8')
    research = package / 'tools' / 'fastloop_research'; research.mkdir()
    for file in (source / 'tools' / 'fastloop_research').glob('*.py'): shutil.copy2(file, research / file.name)
    # Blender runs a different Python interpreter and cannot import Nuitka modules.
    with zipfile.ZipFile(research / 'blender-runtime.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('tools/__init__.py', '')
        for file in (source / 'tools' / 'fastloop_research').glob('*.py'):
            z.write(file, 'tools/fastloop_research/' + file.name)
    ifc_parser = buildenv / 'Lib' / 'site-packages' / 'ifcopenshell' / 'express' / 'express_parser.py'
    with zipfile.ZipFile(package / 'ifc-parser.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(ifc_parser, 'express_parser.py')
    env = os.environ.copy(); env['PYTHONPATH'] = str(package.parent); env['NUITKA_CACHE_DIR'] = str(work / 'compiler-cache')
    header_workaround = prepare_compiler(work, python, env)
    dist = attempt / 'dist'
    command = [python, '-m', 'nuitka', '--mode=onefile', '--jobs=2', '--low-memory', '--mingw64', '--assume-yes-for-downloads',
        '--output-filename=FloorEngine.exe', f'--output-dir={dist}', '--product-name=Floor Engine',
        f'--file-version={version}.0', f'--product-version={version}.0', '--python-flag=isolated',
        '--include-package=Floor_engine_server', '--include-package=uvicorn', '--include-package=anyio',
        *['--noinclude-custom-mode=' + name + ':bytecode' for name in dependency_modules], '--include-package=PIL', '--include-package=cv2', '--include-package=onnxruntime', '--include-package=multipart',
        '--include-package=keyring', '--include-package=keyring.backends', '--include-package=pymupdf', '--include-package=ifcopenshell',
        '--include-distribution-metadata=keyring', '--include-package-data=certifi', '--include-package-data=pptx', '--include-package-data=ifcopenshell',
        '--nofollow-import-to=pytest,tkinter,IPython,onnxruntime.backend,onnxruntime.transformers,onnxruntime.tools,onnxruntime.quantization',
        f'--include-data-dir={source / "web" / "out"}=Floor_engine_server/web/out',
        f'--include-data-dir={source / "assets"}=Floor_engine_server/assets',
        f'--include-data-files={research / "blender-runtime.zip"}=Floor_engine_server/tools/fastloop_research/blender-runtime.zip',
        f'--include-data-files={package / "ifc-parser.zip"}=Floor_engine_server/ifc-parser.zip',
        f'--report={attempt / "nuitka-report.xml"}', package / 'serve.py']
    manifest = {'version': version, 'source_sha': sha, 'work_dir': str(work), 'attempt': str(attempt),
        'compiler_header_workaround': header_workaround, 'pymupdf_binding_mode': 'embedded-bytecode', 'dependency_bytecode_modules': dependency_modules,
        'python': output([python, '--version']), 'node': output(['node', '--version']),
        'dependencies': output([python, '-m', 'pip', 'freeze']).splitlines(), 'command': [str(x) for x in command], 'status': 'building'}
    report = attempt / 'build-report.json'
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    run(command, cwd=attempt, env=env)
    exe = dist / 'FloorEngine.exe'
    manifest.update(status='built', executable=str(exe), executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), executable_bytes=exe.stat().st_size)
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (work / 'latest-build.json').write_text(json.dumps({'report': str(report)}, indent=2), encoding='utf-8')
    print(f'Built {exe}\nReport: {report}')

if __name__ == '__main__': main()
