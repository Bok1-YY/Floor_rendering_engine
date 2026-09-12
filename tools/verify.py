"""Repeatable validation: python tools/verify.py [--integration] [--backend-only]."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--integration', action='store_true')
    parser.add_argument('--backend-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    command = [str(python if python.exists() else sys.executable), '-m', 'pytest', 'tests', '-q', '-rs']
    if not args.integration:
        command.extend(['-m', 'not integration'])
    subprocess.run(command, cwd=root, check=True)
    if not args.backend_only:
        npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
        if not npm:
            raise SystemExit('Node/npm is required for frontend validation')
        subprocess.run([npm, 'run', 'check'], cwd=root / 'web', check=True)


if __name__ == '__main__':
    main()
