"""Product-facing orchestration for deterministic Blender/IFC research runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

from .contract import ResearchModelError, canonical_json, stable_token, validate_bundle


ALLOWED_STATUSES = {
    "mechanical_verified",
    "blocked_dependency_missing",
    "failed_product",
}
REQUIRED_BLENDER_FILES = (
    "scene.blend",
    "scene.glb",
    "top.png",
    "north-east.png",
    "north-west.png",
    "model-report.json",
    "unresolved-issues.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": os.fspath(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _artifact_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        path.name: _artifact(path)
        for path in sorted(run_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.stat().st_size > 0
    }


def _project_token(project: Any) -> str:
    if isinstance(project, str):
        candidate = project
    elif isinstance(project, Mapping):
        candidate = str(project.get("id") or project.get("name") or "project")
    else:  # validate_bundle already rejects this; defensive for type checkers.
        candidate = "project"
    return stable_token(candidate)[:48]


def _discover_blender(explicit: Path | None) -> Path | None:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    environment = os.environ.get("BLENDER_EXECUTABLE")
    if environment:
        candidates.append(Path(environment))
    found = shutil.which("blender")
    if found:
        candidates.append(Path(found))
    candidates.extend(
        [
            Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
            Path(r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"),
            Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"),
            Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        key = os.path.normcase(os.fspath(resolved))
        if key not in seen and resolved.is_file():
            return resolved
        seen.add(key)
    return None


def _python_has_ifcopenshell(executable: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                os.fspath(executable),
                "-I",
                "-c",
                "import ifcopenshell,sys;sys.stdout.write(ifcopenshell.version)",
            ],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _running_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _run_owned(
    arguments: Sequence[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run an argument list and terminate only the child created here."""

    started = datetime.now(timezone.utc)
    process: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""
    timed_out = False
    try:
        process = subprocess.Popen(
            list(arguments),
            cwd=os.fspath(cwd),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
    except OSError as exc:
        stderr = f"{type(exc).__name__}: {exc}"
    stdout_path.write_text(stdout, encoding="utf-8", newline="\n")
    stderr_path.write_text(stderr, encoding="utf-8", newline="\n")
    return {
        "arguments": list(arguments),
        "pid": process.pid if process is not None else None,
        "returncode": process.returncode if process is not None else None,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "stdout": os.fspath(stdout_path.resolve()),
        "stderr": os.fspath(stderr_path.resolve()),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchModelError(f"cannot read generated {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchModelError(f"generated {path.name} is not a JSON object")
    return value


def _blocked_result(
    *,
    run_dir: Path | None,
    dependency: str,
    message: str,
    structure_hash: str,
) -> dict[str, Any]:
    result = {
        "status": "blocked_dependency_missing",
        "structure_hash": structure_hash,
        "dependency": dependency,
        "message": message,
        "output_dir": os.fspath(run_dir.resolve()) if run_dir is not None else None,
        "artifacts": _artifact_map(run_dir) if run_dir is not None and run_dir.is_dir() else {},
    }
    assert result["status"] in ALLOWED_STATUSES
    return result


def run_research_model(
    bundle: dict,
    output_root: Path,
    *,
    blender_executable: Path | None = None,
    ifc_python: Path | None = None,
) -> dict:
    """Validate, build, cold-verify, export, and report one research model.

    The run directory is deterministic for ``project + structure_hash`` and is
    created once.  Repeating the same call is rejected instead of overwriting a
    previous model.  Invalid input is rejected before creating any output.
    """

    validated = validate_bundle(bundle)
    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and not output_root.is_dir():
        raise ResearchModelError(f"output_root exists and is not a directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    run_name = f"{_project_token(validated['project'])}-{validated['structure_hash'][:16]}"
    run_dir = output_root / run_name
    try:
        run_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise ResearchModelError(f"refusing to overwrite existing research run: {run_dir}") from exc

    validated_path = run_dir / "validated-bundle.json"
    validated_path.write_bytes(canonical_json(validated) + b"\n")
    unresolved_path = run_dir / "unresolved-issues.json"
    _write_json(
        unresolved_path,
        {
            "schema": "research-unresolved-issues-v1",
            "structure_hash": validated["structure_hash"],
            "issues": validated["unresolved_issues"],
        },
    )

    blender = _discover_blender(blender_executable)
    if blender is None:
        mechanical = {
            "schema": "research-mechanical-report-v1",
            "status": "blocked_dependency_missing",
            "dependency": "blender",
            "structure_hash": validated["structure_hash"],
            "checks": [],
        }
        _write_json(run_dir / "mechanical-report.json", mechanical)
        return _blocked_result(
            run_dir=run_dir,
            dependency="blender",
            message="Blender executable was not found",
            structure_hash=validated["structure_hash"],
        )

    package_dir = Path(__file__).resolve().parent
    repository_root = package_dir.parents[1]
    build_command = [
        os.fspath(blender),
        "--factory-startup",
        "--background",
        "--python-exit-code",
        "1",
        "--python",
        os.fspath(package_dir / "blender_builder.py"),
        "--",
        "--bundle",
        os.fspath(validated_path),
        "--output",
        os.fspath(run_dir),
    ]
    build_process = _run_owned(
        build_command,
        cwd=repository_root,
        stdout_path=run_dir / "blender-build.stdout.log",
        stderr_path=run_dir / "blender-build.stderr.log",
        timeout=300,
    )
    _write_json(run_dir / "blender-build-process.json", build_process)
    if build_process["returncode"] != 0 or build_process["timed_out"]:
        mechanical = {
            "schema": "research-mechanical-report-v1",
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "failed_stage": "blender_build",
            "process": build_process,
            "checks": [],
        }
        _write_json(run_dir / "mechanical-report.json", mechanical)
        return {
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "output_dir": os.fspath(run_dir),
            "message": "Blender builder failed; inspect process logs",
            "artifacts": _artifact_map(run_dir),
        }
    missing = [name for name in REQUIRED_BLENDER_FILES if not (run_dir / name).is_file() or (run_dir / name).stat().st_size <= 0]
    if missing:
        mechanical = {
            "schema": "research-mechanical-report-v1",
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "failed_stage": "blender_artifacts",
            "missing": missing,
            "checks": [],
        }
        _write_json(run_dir / "mechanical-report.json", mechanical)
        return {
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "output_dir": os.fspath(run_dir),
            "message": f"Blender did not produce required artifacts: {missing}",
            "artifacts": _artifact_map(run_dir),
        }

    verify_reports: dict[str, Any] = {}
    verify_processes: dict[str, Any] = {}
    for mode, model_name in (("blend", "scene.blend"), ("glb", "scene.glb")):
        output_report = run_dir / f"{mode}-verify.json"
        command = [os.fspath(blender), "--factory-startup", "--background"]
        if mode == "blend":
            command.append(os.fspath(run_dir / model_name))
        command.extend(
            [
                "--python-exit-code",
                "1",
                "--python",
                os.fspath(package_dir / "verify_blender.py"),
                "--",
                "--mode",
                mode,
                "--input",
                os.fspath(run_dir / model_name),
                "--bundle",
                os.fspath(validated_path),
                "--output",
                os.fspath(output_report),
            ]
        )
        process_report = _run_owned(
            command,
            cwd=repository_root,
            stdout_path=run_dir / f"{mode}-verify.stdout.log",
            stderr_path=run_dir / f"{mode}-verify.stderr.log",
            timeout=180,
        )
        verify_processes[mode] = process_report
        if process_report["returncode"] != 0 or process_report["timed_out"] or not output_report.is_file():
            mechanical = {
                "schema": "research-mechanical-report-v1",
                "status": "failed_product",
                "structure_hash": validated["structure_hash"],
                "failed_stage": f"{mode}_cold_verify",
                "build_process": build_process,
                "verify_processes": verify_processes,
                "checks": [],
            }
            _write_json(run_dir / "mechanical-report.json", mechanical)
            return {
                "status": "failed_product",
                "structure_hash": validated["structure_hash"],
                "output_dir": os.fspath(run_dir),
                "message": f"{mode} cold verification failed",
                "artifacts": _artifact_map(run_dir),
            }
        verify_reports[mode] = _read_json(output_report)
        if verify_reports[mode].get("status") != "pass":
            raise ResearchModelError(f"{mode} verifier wrote a non-pass report")

    in_process_ifc = ifc_python is None and _running_frozen()
    if ifc_python is not None:
        selected_ifc_python = Path(ifc_python).expanduser().resolve()
    else:
        selected_ifc_python = Path(sys.executable).resolve()
    if in_process_ifc:
        try:
            import ifcopenshell as _ifcopenshell  # noqa: F401
        except ImportError:
            ifc_available = False
        else:
            ifc_available = True
    else:
        ifc_available = selected_ifc_python.is_file() and _python_has_ifcopenshell(selected_ifc_python)
    if not ifc_available:
        mechanical = {
            "schema": "research-mechanical-report-v1",
            "status": "blocked_dependency_missing",
            "dependency": "ifcopenshell",
            "structure_hash": validated["structure_hash"],
            "build_process": build_process,
            "verify_processes": verify_processes,
            "blender": verify_reports,
            "checks": ["blender_build", "blend_cold_open", "glb_cold_import"],
        }
        _write_json(run_dir / "mechanical-report.json", mechanical)
        return _blocked_result(
            run_dir=run_dir,
            dependency="ifcopenshell",
            message="Blender artifacts passed, but IfcOpenShell is unavailable",
            structure_hash=validated["structure_hash"],
        )

    ifc_report_path = run_dir / "ifc-report.json"
    if in_process_ifc:
        started = datetime.now(timezone.utc)
        try:
            from .ifc_builder import build as build_ifc_in_process
            build_ifc_in_process(validated_path, run_dir / "research.ifc", ifc_report_path)
            returncode, stderr = 0, ""
        except Exception as exc:  # pragma: no cover - exercised by packaged runtime smoke
            returncode, stderr = 1, f"{type(exc).__name__}: {exc}"
        (run_dir / "ifc-build.stdout.log").write_text("in-process packaged IFC build\n", encoding="utf-8")
        (run_dir / "ifc-build.stderr.log").write_text(stderr, encoding="utf-8")
        ifc_process = {
            "arguments": ["in-process", "ifc_builder.build"], "pid": os.getpid(),
            "returncode": returncode, "timed_out": False, "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stdout": os.fspath((run_dir / "ifc-build.stdout.log").resolve()),
            "stderr": os.fspath((run_dir / "ifc-build.stderr.log").resolve()),
        }
    else:
        ifc_command = [
            os.fspath(selected_ifc_python),
            "-I",
            os.fspath(package_dir / "ifc_builder.py"),
            "--bundle",
            os.fspath(validated_path),
            "--output",
            os.fspath(run_dir / "research.ifc"),
            "--report",
            os.fspath(ifc_report_path),
        ]
        ifc_process = _run_owned(
            ifc_command,
            cwd=repository_root,
            stdout_path=run_dir / "ifc-build.stdout.log",
            stderr_path=run_dir / "ifc-build.stderr.log",
            timeout=180,
        )
    _write_json(run_dir / "ifc-build-process.json", ifc_process)
    if (
        ifc_process["returncode"] != 0
        or ifc_process["timed_out"]
        or not (run_dir / "research.ifc").is_file()
        or not ifc_report_path.is_file()
    ):
        mechanical = {
            "schema": "research-mechanical-report-v1",
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "failed_stage": "ifc_build_or_reopen",
            "build_process": build_process,
            "verify_processes": verify_processes,
            "ifc_process": ifc_process,
            "checks": ["blender_build", "blend_cold_open", "glb_cold_import"],
        }
        _write_json(run_dir / "mechanical-report.json", mechanical)
        return {
            "status": "failed_product",
            "structure_hash": validated["structure_hash"],
            "output_dir": os.fspath(run_dir),
            "message": "IFC build or reopen validation failed",
            "artifacts": _artifact_map(run_dir),
        }
    ifc_report = _read_json(ifc_report_path)
    if ifc_report.get("status") != "pass":
        raise ResearchModelError("IFC report did not pass")

    mechanical = {
        "schema": "research-mechanical-report-v1",
        "status": "mechanical_verified",
        "structure_hash": validated["structure_hash"],
        "build_process": build_process,
        "verify_processes": verify_processes,
        "ifc_process": ifc_process,
        "blender": verify_reports,
        "ifc": ifc_report,
        "checks": [
            "strict_contract",
            "blender_factory_startup_build",
            "wall_branch_grid_cut_no_boolean",
            "closed_manifold_wall_meshes",
            "blend_cold_open",
            "glb_cold_import",
            "ifc4_write_and_reopen",
        ],
    }
    _write_json(run_dir / "mechanical-report.json", mechanical)
    result = {
        "status": "mechanical_verified",
        "structure_hash": validated["structure_hash"],
        "source_hash": validated["source_hash"],
        "output_dir": os.fspath(run_dir.resolve()),
        "ifc_status": "pass",
        "artifacts": _artifact_map(run_dir),
    }
    assert result["status"] in ALLOWED_STATUSES
    return result
