"""Auditable public-dataset framework for whole-home geometry tests.

The catalog committed under ``tests/datasets/whole_home_geometry`` contains
metadata and checksums only.  Original assets are downloaded into the ignored
``data/external_datasets`` tree.  The production whole-home pipeline must never
import this module or read the truth/prepared directories.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parent
CATALOG_ROOT = REPO_ROOT / "tests" / "datasets" / "whole_home_geometry"
DEFAULT_DATA_ROOT = REPO_ROOT / "data" / "external_datasets" / "whole_home_geometry"

MANIFEST_PATH = CATALOG_ROOT / "dataset_manifest.json"
LOCK_PATH = CATALOG_ROOT / "dataset_lock.json"
LICENSE_INDEX_PATH = CATALOG_ROOT / "LICENSE_INDEX.json"
DIFFICULTY_RULES_PATH = CATALOG_ROOT / "difficulty_rules.json"
SPLITS_PATH = CATALOG_ROOT / "splits.json"

LEVELS = ("L1", "L2", "L3", "L4", "L5")
SPLIT_NAMES = ("development", "validation", "sealed_holdout")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
IFC_ENTITY = re.compile(rb"=\s*(IFC[A-Z0-9_]+)\s*\(", re.IGNORECASE)
CAD_FIXTURE_ID = re.compile(r"^(contract_example|cad_real_[0-9]{3,6}|cad_real_audit_[0-9]{3,6})$")
CAD_ENTITY_ID = re.compile(r"^(wall|assembly|opening|face|space)_[0-9]{4,6}$")
CAD_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:+-]{1,96}$")
CAD_FORBIDDEN_KEY_PARTS = (
    "path", "url", "filename", "address", "text", "label", "layer", "block",
    "handle", "preview", "screenshot", "project_id", "source_sha256", "dwg_sha256",
    "dxf_sha256", "report_sha256", "actor", "reviewer_name", "user_name",
)
CAD_FORBIDDEN_STRING_PARTS = (
    "http://", "https://", ".dwg", ".dxf", ".png", ".jpg", ".svg", "\\users\\",
    "/users/", "output_files", "_ng_uploads", "file://",
)
CAD_REGRESSION_SCHEMA_VERSION = 1

# This code-level allowlist cannot be weakened by editing the JSON catalog.
# Prefixes intentionally name official repositories, not generic host roots.
TRUSTED_SOURCE_PREFIXES = (
    "https://huggingface.co/datasets/sylvainHellin/ifc-bench/",
    "https://github.com/sylvainHellin/ifc-bench",
    "https://github.com/buildingsmart-community/Community-Sample-Test-Files",
    "https://github.com/openBIMstandards/Archive-DataSetSchependomlaan",
    "https://github.com/MLSTRUCT/MLStructFP",
    "https://github.com/CubiCasa/CubiCasa5k",
    "https://github.com/bertjiazheng/Structured3D",
    "https://github.com/zillow/zind",
    "https://floorplancad.github.io/",
)

# Hugging Face serves pinned files through its own Xet/CDN hosts.  Redirects
# are checked independently from source URLs and only these suffixes are valid.
TRUSTED_REDIRECT_HOST_SUFFIXES = (
    "huggingface.co",
    ".huggingface.co",
    ".hf.co",
    ".xethub.hf.co",
)


class DatasetError(RuntimeError):
    """Raised for a catalog, licensing, download, or integrity violation."""


@dataclass(frozen=True)
class Catalog:
    manifest: Mapping[str, Any]
    lock: Mapping[str, Any]
    licenses: Mapping[str, Any]
    difficulty_rules: Mapping[str, Any]
    splits: Mapping[str, Any]


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"catalog file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"catalog file must contain an object: {path}")
    return value


def load_catalog(catalog_root: Path | str = CATALOG_ROOT) -> Catalog:
    root = Path(catalog_root)
    return Catalog(
        manifest=_read_json(root / "dataset_manifest.json"),
        lock=_read_json(root / "dataset_lock.json"),
        licenses=_read_json(root / "LICENSE_INDEX.json"),
        difficulty_rules=_read_json(root / "difficulty_rules.json"),
        splits=_read_json(root / "splits.json"),
    )


def sha256_file(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def validate_official_url(url: str, *, redirect: bool = False) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise DatasetError(f"malformed source URL: {url!r}") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DatasetError(f"source URL must be credential-free HTTPS: {url!r}")
    if parsed.port not in (None, 443):
        raise DatasetError(f"non-standard source URL port is forbidden: {url!r}")
    if redirect:
        host = parsed.hostname.lower()
        if not any(host == suffix or host.endswith(suffix) for suffix in TRUSTED_REDIRECT_HOST_SUFFIXES):
            raise DatasetError(f"download redirected to an untrusted host: {host}")
        return
    if not any(url == prefix.rstrip("/") or url.startswith(prefix) for prefix in TRUSTED_SOURCE_PREFIXES):
        raise DatasetError(f"source URL is not on the official allowlist: {url!r}")


def _points_for_metric(value: Any, specification: Mapping[str, Any]) -> int:
    if "points" in specification:
        return int(specification["points"]) if bool(value) else 0
    if "bands" in specification:
        numeric = float(value or 0)
        matched = 0
        for band in specification["bands"]:
            minimum = float(band.get("min", float("-inf")))
            maximum = float(band.get("max", float("inf")))
            if minimum <= numeric <= maximum:
                matched = max(matched, int(band["points"]))
        return matched
    if "mapping" in specification:
        return int(specification["mapping"].get(str(value), 0))
    raise DatasetError(f"unsupported difficulty metric specification: {specification!r}")


def score_difficulty(
    features: Mapping[str, Any], rules: Mapping[str, Any]
) -> tuple[int, str, Mapping[str, int]]:
    group_scores: dict[str, int] = {}
    for group_name, group in rules.get("groups", {}).items():
        subtotal = 0
        for feature_name, specification in group.get("metrics", {}).items():
            subtotal += _points_for_metric(features.get(feature_name), specification)
        group_scores[group_name] = min(int(group["cap"]), subtotal)
    score = min(100, sum(group_scores.values()))
    for item in rules.get("levels", []):
        if int(item["min"]) <= score <= int(item["max"]):
            return score, str(item["level"]), group_scores
    raise DatasetError(f"difficulty score {score} is outside configured levels")


def _case_index(catalog: Catalog) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for case in catalog.manifest.get("cases", []):
        case_id = str(case.get("case_id", ""))
        if case_id in result:
            raise DatasetError(f"duplicate case_id: {case_id}")
        result[case_id] = case
    return result


def _source_index(catalog: Catalog) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for source in catalog.manifest.get("sources", []):
        source_id = str(source.get("source_id", ""))
        if source_id in result:
            raise DatasetError(f"duplicate source_id: {source_id}")
        result[source_id] = source
    return result


def _license_index(catalog: Catalog) -> Mapping[str, Mapping[str, Any]]:
    return catalog.licenses.get("licenses", {})


def audit_catalog(catalog: Catalog) -> Mapping[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        cases = _case_index(catalog)
        sources = _source_index(catalog)
    except DatasetError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "case_count": 0}
    licenses = _license_index(catalog)
    lock_artifacts = catalog.lock.get("artifacts", {})

    if catalog.manifest.get("schema_version") != 1:
        errors.append("dataset_manifest schema_version must be 1")
    if catalog.lock.get("schema_version") != 1:
        errors.append("dataset_lock schema_version must be 1")

    for source_id, source in sources.items():
        for field in ("homepage", "license_url"):
            url = source.get(field)
            if not url:
                errors.append(f"{source_id}: missing {field}")
                continue
            try:
                validate_official_url(str(url))
            except DatasetError as exc:
                errors.append(f"{source_id}: {exc}")
        license_id = source.get("license_id")
        if license_id not in licenses:
            errors.append(f"{source_id}: unknown license_id {license_id!r}")
        if source.get("access") == "agreement_required" and source.get("auto_download", True):
            errors.append(f"{source_id}: agreement-required source cannot auto-download")

    building_splits: dict[str, set[str]] = {}
    for case_id, case in cases.items():
        source = sources.get(case.get("source_id"))
        if source is None:
            errors.append(f"{case_id}: unknown source_id {case.get('source_id')!r}")
            continue
        license_id = case.get("license_id") or source.get("license_id")
        license_info = licenses.get(license_id)
        if license_info is None:
            errors.append(f"{case_id}: unknown license_id {license_id!r}")
            continue
        if license_id != source.get("license_id"):
            errors.append(f"{case_id}: license differs from its source without an explicit source split")
        split = case.get("split")
        if split not in SPLIT_NAMES:
            errors.append(f"{case_id}: invalid split {split!r}")
        building_group = str(case.get("building_group", ""))
        if not building_group:
            errors.append(f"{case_id}: building_group is required")
        building_splits.setdefault(building_group, set()).add(str(split))

        try:
            score, level, _ = score_difficulty(case.get("difficulty_features", {}), catalog.difficulty_rules)
        except (DatasetError, TypeError, ValueError) as exc:
            errors.append(f"{case_id}: invalid difficulty features: {exc}")
        else:
            if int(case.get("difficulty_score", -1)) != score:
                errors.append(f"{case_id}: difficulty_score is {case.get('difficulty_score')}, calculated {score}")
            if case.get("difficulty_level") != level:
                errors.append(f"{case_id}: difficulty_level is {case.get('difficulty_level')}, calculated {level}")
            if level == "L1" and case.get("blocking_candidate"):
                features = case.get("difficulty_features", {})
                complex_enough = any(
                    bool(features.get(key))
                    for key in (
                        "non_rectangular_outline", "non_orthogonal_walls", "has_stairs",
                        "has_open_plan", "multiple_wall_thicknesses",
                    )
                )
                if int(features.get("room_count", 0)) < 4 or int(features.get("opening_count", 0)) < 5 or not complex_enough:
                    errors.append(f"{case_id}: blocking L1 candidate is a forbidden toy layout")

        automatic = bool(source.get("auto_download")) and bool(license_info.get("auto_download"))
        if source.get("access") != "public" and automatic:
            errors.append(f"{case_id}: restricted case cannot be automatically downloaded")
        for artifact in case.get("artifacts", []):
            lock_id = artifact.get("lock_id")
            entry = lock_artifacts.get(lock_id)
            if entry is None:
                errors.append(f"{case_id}: artifact {lock_id!r} is missing from dataset_lock")
                continue
            if entry.get("case_id") != case_id:
                errors.append(f"{case_id}: lock entry {lock_id!r} points at a different case")
            try:
                validate_official_url(str(entry.get("url", "")))
            except DatasetError as exc:
                errors.append(f"{case_id}/{lock_id}: {exc}")
            checksum = str(entry.get("sha256", "")).lower()
            if not HEX_SHA256.fullmatch(checksum):
                errors.append(f"{case_id}/{lock_id}: a pinned SHA-256 is required")
            if int(entry.get("size_bytes", 0)) <= 0:
                errors.append(f"{case_id}/{lock_id}: positive size_bytes is required")

    for building_group, assigned in building_splits.items():
        assigned.discard("None")
        if len(assigned) > 1:
            errors.append(f"building leakage: {building_group} appears in {sorted(assigned)}")

    split_membership: dict[str, str] = {}
    for level, configured in catalog.splits.get("levels", {}).items():
        if level not in LEVELS:
            errors.append(f"splits.json contains unknown level {level}")
        for split_name, case_ids in configured.items():
            if split_name not in SPLIT_NAMES:
                errors.append(f"splits.json contains unknown split {split_name}")
                continue
            if not case_ids:
                warnings.append(f"{level}/{split_name}: no candidate assigned yet")
            for case_id in case_ids:
                case = cases.get(case_id)
                if case is None:
                    errors.append(f"splits.json references unknown case {case_id}")
                    continue
                if case.get("difficulty_level") != level or case.get("split") != split_name:
                    errors.append(f"splits.json assignment disagrees with case {case_id}")
                if case_id in split_membership:
                    errors.append(f"case {case_id} appears more than once in splits.json")
                split_membership[case_id] = f"{level}/{split_name}"
    missing_from_splits = sorted(set(cases) - set(split_membership))
    if missing_from_splits:
        errors.append(f"cases missing from splits.json: {', '.join(missing_from_splits)}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": len(cases),
        "source_count": len(sources),
        "automatic_case_count": sum(
            1 for case in cases.values()
            if sources[case["source_id"]].get("auto_download")
            and licenses[sources[case["source_id"]]["license_id"]].get("auto_download")
        ),
    }


def _normalize_filters(values: Sequence[str] | None, allowed: Iterable[str] | None = None) -> set[str]:
    result = {item.strip() for value in values or [] for item in value.split(",") if item.strip()}
    if allowed is not None:
        unknown = result - set(allowed)
        if unknown:
            raise DatasetError(f"unknown filter value(s): {', '.join(sorted(unknown))}")
    return result


def select_cases(
    catalog: Catalog,
    *,
    levels: Sequence[str] | None = None,
    splits: Sequence[str] | None = None,
    case_ids: Sequence[str] | None = None,
) -> list[Mapping[str, Any]]:
    level_filter = _normalize_filters(levels, LEVELS)
    split_filter = _normalize_filters(splits, SPLIT_NAMES)
    case_filter = _normalize_filters(case_ids)
    result = []
    for case in catalog.manifest.get("cases", []):
        if level_filter and case.get("difficulty_level") not in level_filter:
            continue
        if split_filter and case.get("split") not in split_filter:
            continue
        if case_filter and case.get("case_id") not in case_filter:
            continue
        result.append(case)
    unknown_cases = case_filter - {str(case["case_id"]) for case in result}
    if unknown_cases:
        raise DatasetError(f"unknown or filtered-out case(s): {', '.join(sorted(unknown_cases))}")
    return result


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        validate_official_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_one(url: str, destination: Path, *, expected_sha256: str, expected_size: int, retries: int = 4) -> None:
    validate_official_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    opener = urllib.request.build_opener(_ValidatedRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "FloorEngine-GeometryDataset/1.0"})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        temporary.unlink(missing_ok=True)
        try:
            context = ssl.create_default_context()
            # HTTPSHandler is intentionally rebuilt so every retry gets a fresh TLS connection.
            opener = urllib.request.build_opener(
                _ValidatedRedirectHandler(), urllib.request.HTTPSHandler(context=context)
            )
            with opener.open(request, timeout=90) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            actual_size = temporary.stat().st_size
            if actual_size != expected_size:
                raise DatasetError(
                    f"size mismatch for {destination.name}: expected {expected_size}, got {actual_size}"
                )
            actual_hash = sha256_file(temporary)
            if actual_hash != expected_sha256:
                raise DatasetError(
                    f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual_hash}"
                )
            os.replace(temporary, destination)
            return
        except (OSError, DatasetError, urllib.error.URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if isinstance(exc, DatasetError) or attempt == retries:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise DatasetError(f"download failed after {retries} attempt(s): {url}: {last_error}")


def _data_root(value: Path | str | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    configured = os.getenv("WHOLE_HOME_DATASET_ROOT")
    return Path(configured).resolve() if configured else DEFAULT_DATA_ROOT.resolve()


def download_cases(
    catalog: Catalog,
    cases: Sequence[Mapping[str, Any]],
    *,
    data_root: Path | str | None = None,
    force: bool = False,
) -> Mapping[str, Any]:
    audit = audit_catalog(catalog)
    if not audit["ok"]:
        raise DatasetError("catalog audit failed; refusing download: " + "; ".join(audit["errors"]))
    root = _data_root(data_root)
    sources = _source_index(catalog)
    licenses = _license_index(catalog)
    lock_artifacts = catalog.lock["artifacts"]
    downloaded: list[str] = []
    cached: list[str] = []
    skipped: list[Mapping[str, str]] = []

    for case in cases:
        source = sources[case["source_id"]]
        license_info = licenses[source["license_id"]]
        if not source.get("auto_download") or not license_info.get("auto_download"):
            skipped.append({
                "case_id": case["case_id"],
                "reason": source.get("access_note") or license_info.get("restriction") or "manual access required",
            })
            continue
        for artifact in case.get("artifacts", []):
            entry = lock_artifacts[artifact["lock_id"]]
            relative_path = Path(entry["relative_path"])
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise DatasetError(f"unsafe artifact path in lock: {relative_path}")
            destination = root / "raw" / relative_path
            expected_hash = entry["sha256"].lower()
            expected_size = int(entry["size_bytes"])
            if destination.exists() and not force:
                if destination.stat().st_size == expected_size and sha256_file(destination) == expected_hash:
                    cached.append(str(relative_path).replace("\\", "/"))
                    continue
                raise DatasetError(f"cached file failed integrity checks; use --force: {destination}")
            _download_one(
                entry["url"], destination,
                expected_sha256=expected_hash, expected_size=expected_size,
            )
            downloaded.append(str(relative_path).replace("\\", "/"))
    return {"downloaded": downloaded, "cached": cached, "skipped": skipped, "data_root": str(root)}


def _ifc_inventory(path: Path) -> Mapping[str, Any]:
    counts: dict[str, int] = {}
    with path.open("rb") as handle:
        for match in IFC_ENTITY.finditer(handle.read()):
            entity = match.group(1).decode("ascii").upper()
            counts[entity] = counts.get(entity, 0) + 1
    selected = {
        name: counts.get(name, 0)
        for name in (
            "IFCBUILDINGSTOREY", "IFCSPACE", "IFCWALL", "IFCWALLSTANDARDCASE",
            "IFCDOOR", "IFCWINDOW", "IFCOPENINGELEMENT", "IFCSTAIR", "IFCCOLUMN",
        )
    }
    selected["IFCWALL_TOTAL"] = selected["IFCWALL"] + selected["IFCWALLSTANDARDCASE"]
    return {"ifc_entity_counts": selected, "ifc_total_entities": sum(counts.values())}


def prepare_cases(
    catalog: Catalog,
    cases: Sequence[Mapping[str, Any]],
    *,
    data_root: Path | str | None = None,
) -> Mapping[str, Any]:
    root = _data_root(data_root)
    lock_artifacts = catalog.lock.get("artifacts", {})
    prepared: list[str] = []
    missing: list[str] = []
    for case in cases:
        inventory: dict[str, Any] = {
            "schema_version": 1,
            "case_id": case["case_id"],
            "building_group": case["building_group"],
            "truth_kind": case["truth_kind"],
            "difficulty_score": case["difficulty_score"],
            "difficulty_level": case["difficulty_level"],
            "sources": [],
        }
        case_missing = False
        for artifact in case.get("artifacts", []):
            entry = lock_artifacts[artifact["lock_id"]]
            path = root / "raw" / entry["relative_path"]
            if not path.exists():
                case_missing = True
                missing.append(f"{case['case_id']}:{entry['relative_path']}")
                continue
            actual = sha256_file(path)
            if actual != entry["sha256"]:
                raise DatasetError(f"cannot prepare corrupt source: {path}")
            item: dict[str, Any] = {
                "relative_path": entry["relative_path"],
                "sha256": actual,
                "size_bytes": path.stat().st_size,
                "media_type": artifact.get("media_type"),
            }
            if path.suffix.lower() == ".ifc":
                item.update(_ifc_inventory(path))
            inventory["sources"].append(item)
        if case_missing or not inventory["sources"]:
            continue
        destination = root / "prepared" / case["case_id"] / "inventory.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        prepared.append(case["case_id"])
    return {"prepared": prepared, "missing": missing, "data_root": str(root)}


def verify_checksums(
    catalog: Catalog,
    cases: Sequence[Mapping[str, Any]],
    *,
    data_root: Path | str | None = None,
    require_installed: bool = False,
) -> Mapping[str, Any]:
    root = _data_root(data_root)
    lock_artifacts = catalog.lock.get("artifacts", {})
    verified: list[str] = []
    missing: list[str] = []
    corrupt: list[Mapping[str, Any]] = []
    for case in cases:
        for artifact in case.get("artifacts", []):
            entry = lock_artifacts[artifact["lock_id"]]
            path = root / "raw" / entry["relative_path"]
            label = f"{case['case_id']}:{entry['relative_path']}"
            if not path.exists():
                missing.append(label)
                continue
            actual_hash = sha256_file(path)
            actual_size = path.stat().st_size
            if actual_hash != entry["sha256"] or actual_size != int(entry["size_bytes"]):
                corrupt.append({
                    "artifact": label,
                    "expected_sha256": entry["sha256"], "actual_sha256": actual_hash,
                    "expected_size": entry["size_bytes"], "actual_size": actual_size,
                })
            else:
                verified.append(label)
    return {
        "ok": not corrupt and (not require_installed or not missing),
        "verified": verified,
        "missing": missing,
        "corrupt": corrupt,
        "data_root": str(root),
    }


def inspect_cases(
    catalog: Catalog,
    cases: Sequence[Mapping[str, Any]],
    *,
    data_root: Path | str | None = None,
) -> Mapping[str, Any]:
    root = _data_root(data_root)
    sources = _source_index(catalog)
    licenses = _license_index(catalog)
    lock_artifacts = catalog.lock.get("artifacts", {})
    rows = []
    for case in cases:
        source = sources[case["source_id"]]
        license_info = licenses[source["license_id"]]
        artifact_states = []
        for artifact in case.get("artifacts", []):
            entry = lock_artifacts[artifact["lock_id"]]
            path = root / "raw" / entry["relative_path"]
            artifact_states.append({
                "path": entry["relative_path"],
                "installed": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
            })
        inventory_path = root / "prepared" / case["case_id"] / "inventory.json"
        rows.append({
            "case_id": case["case_id"],
            "building_group": case["building_group"],
            "level": case["difficulty_level"],
            "score": case["difficulty_score"],
            "split": case["split"],
            "license": source["license_id"],
            "distribution_class": license_info["classification"],
            "auto_download": bool(source.get("auto_download") and license_info.get("auto_download")),
            "artifacts": artifact_states,
            "prepared": inventory_path.exists(),
        })
    return {"cases": rows, "data_root": str(root)}


def canonical_json_sha256(value: Any) -> str:
    """Hash a JSON value with stable ordering and no presentation whitespace."""
    payload = json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _round_number(value: Any, decimals: int = 6) -> float:
    result = round(float(value), decimals)
    if not math.isfinite(result):
        raise DatasetError("CAD regression geometry contains a non-finite number")
    return 0.0 if result == 0 else result


def _point_xz(value: Any) -> tuple[float, float]:
    if isinstance(value, Mapping):
        return float(value.get("x")), float(value.get("z"))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    raise DatasetError(f"invalid CAD regression point: {value!r}")


def _normalized_point(value: Any, origin_x: float, origin_z: float) -> list[float]:
    x, z = _point_xz(value)
    return [_round_number(x - origin_x), _round_number(z - origin_z)]


def _polygon_points(value: Any, origin_x: float, origin_z: float) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    points = [_normalized_point(point, origin_x, origin_z) for point in value]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _safe_enum(value: Any, allowed: set[str], default: str = "other") -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return token if token in allowed else default


def _source_kind(provenance: Any) -> str:
    raw = str((provenance or {}).get("source_kind") or "").upper()
    if raw in {"LINE", "XLINE", "RAY"}:
        return "line"
    if raw in {"LWPOLYLINE", "POLYLINE"}:
        return "polyline"
    if raw in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}:
        return "curve"
    if raw in {"INSERT", "MINSERT"}:
        return "insert"
    return "other"


def _entity_metadata(provenance: Any) -> Mapping[str, Any]:
    value = provenance if isinstance(provenance, Mapping) else {}
    return {
        "source_kind": _source_kind(value),
        "nested_insert_depth": min(32, len(value.get("insert_chain") or [])),
    }


def _read_pointer(value: Any, *, expected_hash_key: str, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if value.get("storage") != "external_json_v1":
        return value
    path = Path(str(value.get("path") or value.get("report_path") or "")).resolve()
    if not path.is_file():
        raise DatasetError(f"{description} file is missing")
    expected = str(value.get(expected_hash_key) or "").lower()
    if not HEX_SHA256.fullmatch(expected) or sha256_file(path) != expected:
        raise DatasetError(f"{description} integrity check failed")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{description} is not readable JSON") from exc
    if not isinstance(result, dict):
        raise DatasetError(f"{description} must contain a JSON object")
    return result


def _source_commitment(raw_hash: str, key: bytes, scope: str) -> Mapping[str, str]:
    if not HEX_SHA256.fullmatch(raw_hash.lower()):
        raise DatasetError(f"{scope} is missing a valid private SHA-256")
    return {
        "scope": scope,
        "algorithm": "hmac-sha256",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "value": hmac.new(key, bytes.fromhex(raw_hash), hashlib.sha256).hexdigest(),
    }


def _collect_geometry_points(model: Mapping[str, Any], report: Mapping[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for wall in model.get("walls") or []:
        for key in ("start", "end"):
            try:
                points.append(_point_xz(wall.get(key)))
            except (DatasetError, TypeError, ValueError):
                pass
    for assembly in model.get("wall_assemblies") or []:
        for point in assembly.get("footprint_polygon") or []:
            try:
                points.append(_point_xz(point))
            except (DatasetError, TypeError, ValueError):
                pass
        for point in assembly.get("source_centerline") or []:
            try:
                points.append(_point_xz(point))
            except (DatasetError, TypeError, ValueError):
                pass
    for face in report.get("raw_faces") or []:
        for point in face.get("polygon") or []:
            try:
                points.append(_point_xz(point))
            except (DatasetError, TypeError, ValueError):
                pass
    if not points:
        raise DatasetError("CAD project has no model-space geometry to export")
    return points


def _normalized_walls(model: Mapping[str, Any], origin_x: float, origin_z: float) -> tuple[list[Mapping[str, Any]], Mapping[str, str]]:
    rows = []
    for wall in model.get("walls") or []:
        start = _normalized_point(wall.get("start"), origin_x, origin_z)
        end = _normalized_point(wall.get("end"), origin_x, origin_z)
        if start == end:
            continue
        rows.append((start, end, str(wall.get("id") or ""), wall))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    result: list[Mapping[str, Any]] = []
    id_map: dict[str, str] = {}
    for index, (start, end, source_id, wall) in enumerate(rows, 1):
        entity_id = f"wall_{index:06d}"
        if source_id:
            id_map[source_id] = entity_id
        item = {
            "id": entity_id,
            "start": start,
            "end": end,
            "thickness_m": _round_number(wall.get("thickness_m") or 0),
            "height_m": _round_number(wall.get("height_m") or model.get("wall_height_m") or 0),
            "boundary_kind": _safe_enum(
                wall.get("boundary_kind"), {"centerline", "paired_faces", "closed_footprint"}
            ),
            **_entity_metadata(wall.get("cad_provenance")),
        }
        item["entity_hash"] = canonical_json_sha256(item)
        result.append(item)
    return result, id_map


def _normalized_assemblies(model: Mapping[str, Any], origin_x: float, origin_z: float) -> list[Mapping[str, Any]]:
    rows = []
    for assembly in model.get("wall_assemblies") or []:
        polygon = _polygon_points(assembly.get("footprint_polygon"), origin_x, origin_z)
        centerline = _polygon_points(assembly.get("source_centerline"), origin_x, origin_z)
        if len(polygon) < 3 and len(centerline) < 2:
            continue
        sort_geometry = polygon if len(polygon) >= 3 else centerline
        rows.append((sort_geometry, polygon, centerline, str(assembly.get("id") or ""), assembly))
    rows.sort(key=lambda row: (row[0], row[3]))
    result = []
    for index, (_sort_geometry, polygon, centerline, _source_id, assembly) in enumerate(rows, 1):
        item = {
            "id": f"assembly_{index:06d}",
            "representation": _safe_enum(
                assembly.get("source_representation"),
                {"centerline", "paired_faces", "closed_footprint", "ambiguous",
                 "redundant_evidence"},
                "ambiguous",
            ),
            "review_state": _safe_enum(
                assembly.get("review_status"), {"accepted", "review_required", "rejected"},
                "review_required",
            ),
            "height_m": _round_number(assembly.get("height_m") or model.get("wall_height_m") or 0),
            **_entity_metadata(assembly.get("cad_provenance")),
        }
        if len(polygon) >= 3:
            item["footprint"] = polygon
        else:
            item["source_centerline"] = centerline
        thickness = assembly.get("thickness_m")
        if thickness is not None:
            item["thickness_m"] = _round_number(thickness)
        item["entity_hash"] = canonical_json_sha256(item)
        result.append(item)
    return result


def _normalized_faces(report: Mapping[str, Any], origin_x: float, origin_z: float) -> tuple[list[Mapping[str, Any]], Mapping[str, str]]:
    rows = []
    for face in report.get("raw_faces") or []:
        polygon = _polygon_points(face.get("polygon"), origin_x, origin_z)
        if len(polygon) < 3:
            continue
        holes = [
            points for points in (
                _polygon_points(ring, origin_x, origin_z)
                for ring in (face.get("interior_rings") or [])
            ) if len(points) >= 3
        ]
        rows.append((polygon, str(face.get("face_id") or ""), holes, face))
    rows.sort(key=lambda row: (row[0], row[1]))
    result: list[Mapping[str, Any]] = []
    id_map: dict[str, str] = {}
    for index, (polygon, source_id, holes, face) in enumerate(rows, 1):
        entity_id = f"face_{index:06d}"
        if source_id:
            id_map[source_id] = entity_id
        item = {
            "id": entity_id,
            "polygon": polygon,
            "holes": holes,
            "area_m2": _round_number(face.get("area_m2") or 0),
            "manual_eligible": face.get("manual_eligible") is True,
        }
        item["entity_hash"] = canonical_json_sha256(item)
        result.append(item)
    return result, id_map


def _normalized_openings(
    model: Mapping[str, Any], wall_ids: Mapping[str, str]
) -> list[Mapping[str, Any]]:
    rows = []
    for opening in model.get("openings") or []:
        wall_id = wall_ids.get(str(opening.get("wall_id") or ""))
        if not wall_id:
            continue
        rows.append((wall_id, float(opening.get("offset_m") or 0), str(opening.get("id") or ""), opening))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    result = []
    for index, (wall_id, _offset, _source_id, opening) in enumerate(rows, 1):
        item = {
            "id": f"opening_{index:06d}",
            "wall_id": wall_id,
            "kind": _safe_enum(opening.get("kind"), {"door", "window", "open_connection"}),
            "offset_m": _round_number(opening.get("offset_m") or 0),
            "width_m": _round_number(opening.get("width_m") or 0),
            "height_m": _round_number(opening.get("height_m") or 0),
            "sill_height_m": _round_number(opening.get("sill_height_m") or 0),
            **_entity_metadata(opening.get("cad_provenance")),
        }
        item["entity_hash"] = canonical_json_sha256(item)
        result.append(item)
    return result


def build_sanitized_cad_regression_fixture(
    project: Mapping[str, Any], *, fixture_id: str, commitment_key: bytes
) -> Mapping[str, Any]:
    """Build a commit-safe candidate stream without serializing private CAD data.

    The returned ground truth is deliberately ``annotation_required``.  Space
    drafts are model candidates, not independent human truth, and are therefore
    used only for normalized candidate geometry and status counts.
    """
    if not CAD_FIXTURE_ID.fullmatch(fixture_id):
        raise DatasetError("fixture_id must be contract_example, cad_real_NNN, or cad_real_audit_NNN")
    if len(commitment_key) < 16:
        raise DatasetError("CAD fixture commitment key must be at least 16 bytes")
    cad_path = Path(str(project.get("cad_path") or "")).resolve()
    if not cad_path.is_file() or cad_path.suffix.lower() != ".dwg":
        raise DatasetError("project cad_path must reference the private original DWG")
    original_hash = sha256_file(cad_path)

    report = _read_pointer(
        project.get("parse_report"), expected_hash_key="report_sha256", description="CAD parse report"
    )
    report_source = Path(str(report.get("source_path") or "")).resolve()
    if not report_source.is_file() or report_source.suffix.lower() != ".dxf":
        raise DatasetError("parse report must reference the private ACadSharp DXF")
    converted_hash = sha256_file(report_source)
    if converted_hash != str(report.get("source_sha256") or "").lower():
        raise DatasetError("ACadSharp DXF hash disagrees with parse_report.source_sha256")

    draft_pointer = project.get("cad_space_draft_pointer")
    model = _read_pointer(
        draft_pointer, expected_hash_key="sha256", description="CAD space draft"
    ) if draft_pointer else (project.get("model") or {})
    if not isinstance(model, Mapping) or not model.get("walls"):
        raise DatasetError("project has no CAD space draft/model candidate walls")

    points = _collect_geometry_points(model, report)
    origin_x = min(point[0] for point in points)
    origin_z = min(point[1] for point in points)
    walls, wall_ids = _normalized_walls(model, origin_x, origin_z)
    assemblies = _normalized_assemblies(model, origin_x, origin_z)
    faces, _face_ids = _normalized_faces(report, origin_x, origin_z)
    openings = _normalized_openings(model, wall_ids)
    selected = str(report.get("selected_candidate_id") or "")
    candidates = report.get("candidate_plans") or []
    selected_ordinal = next(
        (index for index, candidate in enumerate(candidates, 1)
         if str(candidate.get("candidate_id") or "") == selected),
        0,
    )
    confirmation = model.get("space_confirmation") or {}
    fixture: dict[str, Any] = {
        "schema_version": CAD_REGRESSION_SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "data_class": "sanitized_real_cad_regression",
        "source_commitments": [
            _source_commitment(original_hash, commitment_key, "original_binary_cad"),
            _source_commitment(converted_hash, commitment_key, "converted_exchange_cad"),
        ],
        "conversion": {
            "adapter": "acadsharp",
            "input_family": "binary_cad",
            "output_family": "exchange_cad",
        },
        "normalization": {
            "coordinate_system": "metres_x_z",
            "origin_method": "selected_geometry_min_corner",
            "precision_decimals": 6,
            "source_unit_code": int(report.get("insunits") or 0),
            "unit_scale_to_m": _round_number(report.get("unit_scale_to_m") or 0),
            "translation_only": True,
        },
        "selection": {
            "candidate_count": len(candidates),
            "selected_candidate_ordinal": selected_ordinal,
            "structural_entity_count": int(report.get("structural_entity_count") or 0),
            "ignored_nonstructural_count": int(report.get("ignored_nonstructural_count") or 0),
        },
        "normalized_entities": {
            "walls": walls,
            "wall_assemblies": assemblies,
            "openings": openings,
            "face_candidates": faces,
        },
        "candidate_review": {
            "state": _safe_enum(
                confirmation.get("status"), {"accepted", "needs_review", "blocked"}, "needs_review"
            ),
            "physical_space_candidate_count": len(model.get("physical_spaces") or []),
            "semantic_zone_candidate_count": len(model.get("semantic_zones") or []),
            "unresolved_wall_assembly_count": sum(
                1 for row in assemblies if row["review_state"] == "review_required"
            ),
        },
        "ground_truth": {
            "schema_version": 1,
            "status": "annotation_required",
            "methodology": "independent_dual_view_manual_annotation",
            "walls": {"accepted_ids": [], "rejected_ids": [], "corrections": []},
            "openings": [],
            "physical_spaces": [],
            "excluded_face_ids": [],
            "review_checks": {
                "walls_complete": False,
                "openings_complete": False,
                "spaces_complete": False,
                "source_alignment_checked": False,
            },
            "missing_tasks": [
                "classify_all_walls", "annotate_all_openings",
                "partition_all_eligible_faces", "independent_source_alignment_review",
            ],
        },
    }
    fixture["fixture_hash"] = canonical_json_sha256(fixture)
    validate_cad_regression_fixture(fixture)
    return fixture


def _privacy_scan(value: Any, path: str = "$" ) -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in CAD_FORBIDDEN_KEY_PARTS):
                errors.append(f"{path}.{key}: forbidden private-data field")
            errors.extend(_privacy_scan(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_privacy_scan(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if not value.isascii() or any(ord(char) < 32 or ord(char) > 126 for char in value):
            errors.append(f"{path}: non-ASCII/free-form text is forbidden")
        if any(part in lowered for part in CAD_FORBIDDEN_STRING_PARTS):
            errors.append(f"{path}: path, URL, CAD filename, or image reference is forbidden")
        if len(value) > 128:
            errors.append(f"{path}: string exceeds the fixture privacy limit")
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(float(value)) or abs(float(value)) > 100000:
            errors.append(f"{path}: numeric value is non-finite or not locally normalized")
    return errors


def _require_keys(value: Mapping[str, Any], required: set[str], allowed: set[str], path: str) -> list[str]:
    errors = [f"{path}: missing {key}" for key in sorted(required - set(value))]
    errors.extend(f"{path}: unexpected field {key}" for key in sorted(set(value) - allowed))
    return errors


def validate_cad_regression_fixture(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate schema, references, hashes, manual truth coverage, and privacy."""
    errors = _privacy_scan(value)
    errors.extend(_require_keys(
        value,
        {"schema_version", "fixture_id", "data_class", "source_commitments", "conversion",
         "normalization", "selection", "normalized_entities", "candidate_review", "ground_truth",
         "fixture_hash"},
        {"schema_version", "fixture_id", "data_class", "source_commitments", "conversion",
         "normalization", "selection", "normalized_entities", "candidate_review", "ground_truth",
         "fixture_hash"},
        "$",
    ))
    if value.get("schema_version") != CAD_REGRESSION_SCHEMA_VERSION:
        errors.append("$.schema_version: unsupported schema")
    if not CAD_FIXTURE_ID.fullmatch(str(value.get("fixture_id") or "")):
        errors.append("$.fixture_id: invalid anonymous fixture ID")
    if value.get("data_class") != "sanitized_real_cad_regression":
        errors.append("$.data_class: unexpected data class")
    conversion = value.get("conversion")
    if not isinstance(conversion, Mapping):
        errors.append("$.conversion: must be an object")
    else:
        errors.extend(_require_keys(
            conversion, {"adapter", "input_family", "output_family"},
            {"adapter", "input_family", "output_family"}, "$.conversion",
        ))
        if conversion != {
            "adapter": "acadsharp", "input_family": "binary_cad", "output_family": "exchange_cad"
        }:
            errors.append("$.conversion: only the anonymous ACadSharp contract is allowed")
    normalization = value.get("normalization")
    if not isinstance(normalization, Mapping):
        errors.append("$.normalization: must be an object")
    else:
        normalization_keys = {
            "coordinate_system", "origin_method", "precision_decimals", "source_unit_code",
            "unit_scale_to_m", "translation_only",
        }
        errors.extend(_require_keys(
            normalization, normalization_keys, normalization_keys, "$.normalization"
        ))
        if normalization.get("coordinate_system") != "metres_x_z":
            errors.append("$.normalization.coordinate_system: invalid")
        if normalization.get("origin_method") != "selected_geometry_min_corner":
            errors.append("$.normalization.origin_method: absolute/source coordinates are forbidden")
        if normalization.get("precision_decimals") != 6 or normalization.get("translation_only") is not True:
            errors.append("$.normalization: six-decimal translation-only normalization is required")
    selection = value.get("selection")
    selection_keys = {
        "candidate_count", "selected_candidate_ordinal", "structural_entity_count",
        "ignored_nonstructural_count",
    }
    if not isinstance(selection, Mapping):
        errors.append("$.selection: must be an object")
    else:
        errors.extend(_require_keys(selection, selection_keys, selection_keys, "$.selection"))
        if any(not isinstance(selection.get(key), int) or selection.get(key) < 0 for key in selection_keys):
            errors.append("$.selection: counts and ordinal must be non-negative integers")
        if (selection.get("candidate_count", 0) == 0) != (selection.get("selected_candidate_ordinal", 0) == 0):
            errors.append("$.selection: selected ordinal must agree with candidate count")
        if selection.get("selected_candidate_ordinal", 0) > selection.get("candidate_count", 0):
            errors.append("$.selection.selected_candidate_ordinal: outside candidate list")
    commitments = value.get("source_commitments") or []
    if not isinstance(commitments, list) or {row.get("scope") for row in commitments if isinstance(row, Mapping)} != {
        "original_binary_cad", "converted_exchange_cad"
    }:
        errors.append("$.source_commitments: original and converted HMAC commitments are required")
    for index, row in enumerate(commitments if isinstance(commitments, list) else []):
        if not isinstance(row, Mapping):
            errors.append(f"$.source_commitments[{index}]: must be an object")
            continue
        errors.extend(_require_keys(
            row, {"scope", "algorithm", "key_id", "value"},
            {"scope", "algorithm", "key_id", "value"}, f"$.source_commitments[{index}]",
        ))
        if row.get("algorithm") != "hmac-sha256" or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("value") or "")):
            errors.append(f"$.source_commitments[{index}]: invalid HMAC commitment")
        if not re.fullmatch(r"[0-9a-f]{16}", str(row.get("key_id") or "")):
            errors.append(f"$.source_commitments[{index}]: invalid anonymous key ID")

    entities = value.get("normalized_entities")
    if not isinstance(entities, Mapping):
        errors.append("$.normalized_entities: must be an object")
        entities = {}
    else:
        errors.extend(_require_keys(
            entities, {"walls", "wall_assemblies", "openings", "face_candidates"},
            {"walls", "wall_assemblies", "openings", "face_candidates"},
            "$.normalized_entities",
        ))
    all_ids: set[str] = set()
    kind_ids: dict[str, set[str]] = {}
    for kind in ("walls", "wall_assemblies", "openings", "face_candidates"):
        rows = entities.get(kind, []) if isinstance(entities, Mapping) else []
        if not isinstance(rows, list):
            errors.append(f"$.normalized_entities.{kind}: must be a list")
            continue
        current: set[str] = set()
        for index, row in enumerate(rows):
            entity_id = str(row.get("id") if isinstance(row, Mapping) else "")
            expected_prefix = {
                "walls": "wall", "wall_assemblies": "assembly",
                "openings": "opening", "face_candidates": "face",
            }[kind]
            if not CAD_ENTITY_ID.fullmatch(entity_id) or not entity_id.startswith(expected_prefix + "_"):
                errors.append(f"$.normalized_entities.{kind}[{index}].id: invalid ID")
            if entity_id in all_ids:
                errors.append(f"$.normalized_entities.{kind}[{index}].id: duplicate ID")
            current.add(entity_id)
            all_ids.add(entity_id)
            if isinstance(row, Mapping):
                row_path = f"$.normalized_entities.{kind}[{index}]"
                if kind == "walls":
                    required = {
                        "id", "start", "end", "thickness_m", "height_m", "boundary_kind",
                        "source_kind", "nested_insert_depth", "entity_hash",
                    }
                    errors.extend(_require_keys(row, required, required, row_path))
                    if row.get("boundary_kind") not in {
                        "centerline", "paired_faces", "closed_footprint", "other"
                    }:
                        errors.append(f"{row_path}.boundary_kind: invalid")
                elif kind == "wall_assemblies":
                    required = {
                        "id", "representation", "review_state", "height_m", "source_kind",
                        "nested_insert_depth", "entity_hash",
                    }
                    allowed = required | {"footprint", "source_centerline", "thickness_m"}
                    errors.extend(_require_keys(row, required, allowed, row_path))
                    if ("footprint" in row) == ("source_centerline" in row):
                        errors.append(f"{row_path}: exactly one anonymous geometry form is required")
                    if row.get("representation") not in {
                        "centerline", "paired_faces", "closed_footprint", "ambiguous",
                        "redundant_evidence",
                    } or row.get("review_state") not in {"accepted", "review_required", "rejected"}:
                        errors.append(f"{row_path}: invalid representation or review state")
                elif kind == "openings":
                    required = {
                        "id", "wall_id", "kind", "offset_m", "width_m", "height_m",
                        "sill_height_m", "source_kind", "nested_insert_depth", "entity_hash",
                    }
                    errors.extend(_require_keys(row, required, required, row_path))
                    if row.get("kind") not in {"door", "window", "open_connection", "other"}:
                        errors.append(f"{row_path}.kind: invalid")
                else:
                    required = {
                        "id", "polygon", "holes", "area_m2", "manual_eligible", "entity_hash"
                    }
                    errors.extend(_require_keys(row, required, required, row_path))
                    if not isinstance(row.get("polygon"), list) or len(row.get("polygon")) < 3:
                        errors.append(f"{row_path}.polygon: at least three points are required")
                if row.get("source_kind", "other") not in {
                    "line", "polyline", "curve", "insert", "other"
                }:
                    errors.append(f"{row_path}.source_kind: invalid")
                expected_hash = row.get("entity_hash")
                candidate = dict(row)
                candidate.pop("entity_hash", None)
                if expected_hash != canonical_json_sha256(candidate):
                    errors.append(f"$.normalized_entities.{kind}[{index}].entity_hash: mismatch")
        kind_ids[kind] = current
    for index, opening in enumerate(entities.get("openings", []) if isinstance(entities, Mapping) else []):
        if isinstance(opening, Mapping) and opening.get("wall_id") not in kind_ids.get("walls", set()):
            errors.append(f"$.normalized_entities.openings[{index}].wall_id: unknown wall")

    candidate_review = value.get("candidate_review")
    candidate_review_keys = {
        "state", "physical_space_candidate_count", "semantic_zone_candidate_count",
        "unresolved_wall_assembly_count",
    }
    if not isinstance(candidate_review, Mapping):
        errors.append("$.candidate_review: must be an object")
    else:
        errors.extend(_require_keys(
            candidate_review, candidate_review_keys, candidate_review_keys, "$.candidate_review"
        ))
        if candidate_review.get("state") not in {"accepted", "needs_review", "blocked"}:
            errors.append("$.candidate_review.state: invalid")
        for key in candidate_review_keys - {"state"}:
            if not isinstance(candidate_review.get(key), int) or candidate_review.get(key) < 0:
                errors.append(f"$.candidate_review.{key}: must be a non-negative integer")
        if candidate_review.get("unresolved_wall_assembly_count") != sum(
            1 for row in entities.get("wall_assemblies", [])
            if isinstance(row, Mapping) and row.get("review_state") == "review_required"
        ):
            errors.append("$.candidate_review.unresolved_wall_assembly_count: mismatch")

    truth = value.get("ground_truth")
    if not isinstance(truth, Mapping):
        errors.append("$.ground_truth: must be an object")
        truth = {}
    truth_keys = {
        "schema_version", "status", "methodology", "walls", "openings", "physical_spaces",
        "excluded_face_ids", "review_checks", "missing_tasks",
    }
    errors.extend(_require_keys(truth, truth_keys, truth_keys, "$.ground_truth"))
    if truth.get("schema_version") != 1 or truth.get("methodology") != "independent_dual_view_manual_annotation":
        errors.append("$.ground_truth: schema or methodology is invalid")
    truth_status = truth.get("status")
    if truth_status not in {"annotation_required", "reviewed"}:
        errors.append("$.ground_truth.status: invalid status")
    wall_truth = truth.get("walls") if isinstance(truth.get("walls"), Mapping) else {}
    wall_truth_keys = {"accepted_ids", "rejected_ids", "corrections"}
    errors.extend(_require_keys(wall_truth, wall_truth_keys, wall_truth_keys, "$.ground_truth.walls"))
    accepted = set(wall_truth.get("accepted_ids") or [])
    rejected = set(wall_truth.get("rejected_ids") or [])
    corrected = {
        str(row.get("source_id")) for row in wall_truth.get("corrections") or []
        if isinstance(row, Mapping)
    }
    if not accepted.union(rejected).union(corrected).issubset(kind_ids.get("walls", set())):
        errors.append("$.ground_truth.walls: references unknown normalized wall IDs")
    spaces = truth.get("physical_spaces") or []
    if not isinstance(spaces, list):
        errors.append("$.ground_truth.physical_spaces: must be a list")
        spaces = []
    for index, space in enumerate(spaces):
        path = f"$.ground_truth.physical_spaces[{index}]"
        required = {"id", "polygon", "holes", "space_type", "source_face_ids"}
        if not isinstance(space, Mapping):
            errors.append(f"{path}: must be an object")
            continue
        errors.extend(_require_keys(space, required, required, path))
        if not CAD_ENTITY_ID.fullmatch(str(space.get("id") or "")) or not str(space.get("id")).startswith("space_"):
            errors.append(f"{path}.id: invalid")
        if space.get("space_type") not in {
            "enclosed_room", "open_plan", "circulation", "service", "outdoor", "other"
        }:
            errors.append(f"{path}.space_type: invalid")
    assigned_faces = {
        str(face_id) for space in spaces if isinstance(space, Mapping)
        for face_id in (space.get("source_face_ids") or [])
    }
    excluded_faces = set(truth.get("excluded_face_ids") or [])
    if not assigned_faces.union(excluded_faces).issubset(kind_ids.get("face_candidates", set())):
        errors.append("$.ground_truth: references unknown face IDs")
    checks = truth.get("review_checks") if isinstance(truth.get("review_checks"), Mapping) else {}
    if truth_status == "reviewed":
        if not all(checks.get(key) is True for key in (
            "walls_complete", "openings_complete", "spaces_complete", "source_alignment_checked"
        )):
            errors.append("$.ground_truth.review_checks: all checks must be true for reviewed truth")
        if accepted.union(rejected).union(corrected) != kind_ids.get("walls", set()):
            errors.append("$.ground_truth.walls: every wall requires a decision")
        eligible_faces = {
            row["id"] for row in entities.get("face_candidates", [])
            if isinstance(row, Mapping) and row.get("manual_eligible") is True
        }
        if assigned_faces.union(excluded_faces) != eligible_faces or assigned_faces.intersection(excluded_faces):
            errors.append("$.ground_truth: every eligible face must be assigned or excluded exactly once")
        if truth.get("missing_tasks"):
            errors.append("$.ground_truth.missing_tasks: reviewed truth cannot have missing tasks")
    else:
        if not truth.get("missing_tasks"):
            errors.append("$.ground_truth.missing_tasks: annotation gap must be explicit")

    fixture_hash = str(value.get("fixture_hash") or "")
    candidate = dict(value)
    candidate.pop("fixture_hash", None)
    if fixture_hash != canonical_json_sha256(candidate):
        errors.append("$.fixture_hash: mismatch")
    return {"ok": not errors, "errors": errors, "fixture_id": value.get("fixture_id"),
            "ground_truth_status": truth_status}


def export_sanitized_cad_regression(
    project_path: Path | str, destination: Path | str, *, fixture_id: str, commitment_key: bytes
) -> Mapping[str, Any]:
    source = Path(project_path).resolve()
    target = Path(destination).resolve()
    if target.suffix.lower() != ".json":
        raise DatasetError("sanitized CAD regression export must be a JSON file")
    try:
        project = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError("project JSON cannot be read") from exc
    if not isinstance(project, dict):
        raise DatasetError("project JSON must contain an object")
    fixture = build_sanitized_cad_regression_fixture(
        project, fixture_id=fixture_id, commitment_key=commitment_key
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    temporary.write_text(json.dumps(fixture, ensure_ascii=True, indent=2) + "\n", encoding="ascii")
    written = json.loads(temporary.read_text(encoding="ascii"))
    validation = validate_cad_regression_fixture(written)
    if not validation["ok"]:
        temporary.unlink(missing_ok=True)
        raise DatasetError("generated CAD regression fixture failed validation: " + "; ".join(validation["errors"]))
    os.replace(temporary, target)
    return {
        "ok": True,
        "fixture_id": fixture_id,
        "destination": str(target),
        "sha256": sha256_file(target),
        "fixture_hash": fixture["fixture_hash"],
        "ground_truth_status": fixture["ground_truth"]["status"],
        "counts": {key: len(rows) for key, rows in fixture["normalized_entities"].items()},
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path, default=CATALOG_ROOT)
    parser.add_argument("--data-root", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("audit", help="validate catalog, licenses, URLs, hashes, and split isolation")
    export = subparsers.add_parser(
        "export-cad-regression",
        help="export a commit-safe anonymous geometry stream from a private local CAD project",
    )
    export.add_argument("--project-json", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--fixture-id", required=True)
    export.add_argument(
        "--commitment-key-env", default="WHOLE_HOME_CAD_FIXTURE_HMAC_KEY",
        help="environment variable containing a private HMAC key; the key is never serialized",
    )
    validate = subparsers.add_parser(
        "validate-cad-regression", help="validate schema, privacy, hashes, references, and truth coverage"
    )
    validate.add_argument("--fixture", type=Path, required=True)
    for name in ("list", "download", "prepare", "inspect", "verify-checksums"):
        command = subparsers.add_parser(name)
        command.add_argument("--levels", action="append", help="L1-L5, repeat or comma-separate")
        command.add_argument("--splits", action="append", help="development/validation/sealed_holdout")
        command.add_argument("--case-id", action="append", dest="case_ids")
        if name == "download":
            command.add_argument("--force", action="store_true")
        if name == "verify-checksums":
            command.add_argument("--require-installed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export-cad-regression":
            secret = os.getenv(args.commitment_key_env, "")
            if len(secret.encode("utf-8")) < 16:
                raise DatasetError(
                    f"{args.commitment_key_env} must contain a private key of at least 16 bytes"
                )
            result = export_sanitized_cad_regression(
                args.project_json, args.output,
                fixture_id=args.fixture_id, commitment_key=secret.encode("utf-8"),
            )
            _print_json(result)
            return 0
        if args.command == "validate-cad-regression":
            try:
                fixture = json.loads(args.fixture.read_text(encoding="ascii"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise DatasetError("sanitized CAD regression fixture must be ASCII JSON") from exc
            if not isinstance(fixture, dict):
                raise DatasetError("sanitized CAD regression fixture must contain an object")
            result = validate_cad_regression_fixture(fixture)
            _print_json(result)
            return 0 if result["ok"] else 2
        catalog = load_catalog(args.catalog_root)
        if args.command == "audit":
            result = audit_catalog(catalog)
        else:
            cases = select_cases(
                catalog, levels=args.levels, splits=args.splits, case_ids=args.case_ids
            )
            if args.command == "list":
                result = {"cases": cases, "count": len(cases)}
            elif args.command == "download":
                result = download_cases(catalog, cases, data_root=args.data_root, force=args.force)
            elif args.command == "prepare":
                result = prepare_cases(catalog, cases, data_root=args.data_root)
            elif args.command == "inspect":
                result = inspect_cases(catalog, cases, data_root=args.data_root)
            elif args.command == "verify-checksums":
                result = verify_checksums(
                    catalog, cases, data_root=args.data_root,
                    require_installed=args.require_installed,
                )
            else:  # pragma: no cover - argparse guarantees this cannot happen.
                raise DatasetError(f"unsupported command: {args.command}")
        _print_json(result)
        return 0 if result.get("ok", True) else 2
    except DatasetError as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
