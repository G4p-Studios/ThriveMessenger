"""Verified server-side installation for optional Thrive modules."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


class ModuleInstallError(RuntimeError):
    pass


def installed_module_ids(modules_dir: str | Path) -> set[str]:
    root = Path(modules_dir)
    if not root.is_dir():
        return set()
    return {path.parent.name for path in root.glob("*/module.json") if path.is_file()}


def fetch_catalogs(catalog_urls: list[str], allowed_hosts: set[str] | None = None) -> dict[str, dict]:
    modules: dict[str, dict] = {}
    for catalog_url in catalog_urls:
        _validate_url(catalog_url, allowed_hosts)
        with urllib.request.urlopen(catalog_url, timeout=20) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        for item in payload.get("modules", []):
            module_id = str(item.get("module_id", "")).strip()
            if module_id:
                modules[module_id] = item
    return modules


def install_from_catalog(
    module_id: str,
    catalog_urls: list[str],
    modules_dir: str | Path,
    allowed_hosts: set[str] | None = None,
) -> dict:
    _validate_module_id(module_id)
    entry = fetch_catalogs(catalog_urls, allowed_hosts).get(module_id)
    if not entry:
        raise ModuleInstallError("Module was not found in the configured catalogs.")
    download_url = str(entry.get("download_url", ""))
    expected_hash = str(entry.get("sha256", "")).lower()
    if len(expected_hash) != 64:
        raise ModuleInstallError("Catalog entry has no valid SHA-256 digest.")
    _validate_url(download_url, allowed_hosts)
    root = Path(modules_dir)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="thrive-module-") as temp_name:
        temp = Path(temp_name); archive = temp / "module.zip"; staging = temp / "unpacked"
        with urllib.request.urlopen(download_url, timeout=60) as response, archive.open("wb") as output:
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block: break
                total += len(block)
                if total > 250 * 1024 * 1024: raise ModuleInstallError("Module archive exceeds 250 MB.")
                output.write(block)
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ModuleInstallError("Module archive SHA-256 verification failed.")
        staging.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (staging / member.filename).resolve()
                if staging.resolve() not in target.parents and target != staging.resolve():
                    raise ModuleInstallError("Module archive contains an unsafe path.")
            bundle.extractall(staging)
        manifest_path = staging / "module.json"
        if not manifest_path.is_file(): raise ModuleInstallError("Module archive is missing module.json.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("module_id", "")) != module_id: raise ModuleInstallError("Module manifest identity does not match the catalog.")
        destination = root / module_id
        backup = root / f".{module_id}.previous"
        if backup.exists(): shutil.rmtree(backup)
        if destination.exists(): destination.replace(backup)
        shutil.copytree(staging, destination)
    return manifest


def _validate_url(url: str, allowed_hosts: set[str] | None) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ModuleInstallError("Module sources must use HTTPS.")
    if allowed_hosts and parsed.hostname.lower() not in {host.lower() for host in allowed_hosts}:
        raise ModuleInstallError("Module source host is not allowlisted.")


def _validate_module_id(module_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", module_id):
        raise ModuleInstallError("Module ID must be a lowercase server module slug.")
