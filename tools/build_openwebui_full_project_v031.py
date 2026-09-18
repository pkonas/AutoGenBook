#!/usr/bin/env python3
"""Build and verify the complete AutoGenBook Open WebUI v0.3.1 project archives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

VERSION = "0.3.1"
ROOT_NAME = f"AutoGenBook-OpenWebUI-Full-Installer-Project-v{VERSION}"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def unpack_bundle(project: Path, destination: Path) -> Path:
    parts = sorted((project / "integration").glob("openwebui_bundle.part*.b64"))
    if not parts:
        raise FileNotFoundError("No patched Open WebUI integration bundle parts were found")
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in parts)
    archive_path = destination / "openwebui_bundle-v0.3.1.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(base64.b64decode(encoded, validate=True))
    root = destination / "openwebui_bundle-v0.3.1"
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        resolved_root = root.resolve()
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if target != resolved_root and resolved_root not in target.parents:
                raise RuntimeError(f"Unsafe bundle member: {member.filename}")
        archive.extractall(root)
    return root


def find_pipes(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if "def _file_payload(" in text and "PERSIST_OUTPUTS_TO_OPENWEBUI" in text:
            candidates.append(path)
    return sorted(candidates)


def validate_pipe(path: Path) -> None:
    import ast
    import py_compile

    text = path.read_text(encoding="utf-8")
    required = (
        '"type": "file"',
        '"status": "uploaded"',
        'chat:message:files',
        '/api/v1/files/',
    )
    for marker in required:
        if marker not in text:
            raise AssertionError(f"{path}: missing {marker}")
    ast.parse(text, filename=str(path))
    py_compile.compile(str(path), doraise=True)


def write_docs(project: Path) -> None:
    (project / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    project_readme = """# AutoGenBook Open WebUI – complete installer project 0.3.1

This release fixes downloading generated AutoGenBook artifacts through the
native Open WebUI message interface.

## Root cause

Version 0.3.0 persisted file bytes and a database row, but the assistant event
object did not contain `type: "file"`. Open WebUI filters assistant message
attachments by this field, so the result was not a canonical native file card.

## Corrected contract

Each published output now contains:

- `type: "file"`;
- a persistent Open WebUI file `id`;
- `name`, `filename`, `size`, `content_type`, `status` and `meta`;
- a same-origin `/api/v1/files/<id>/content?attachment=true` URL;
- no Companion bearer token, signed temporary URL, or random local port.

The Pipe emits `chat:message:files`. The Open WebUI card and modal use the file
ID and the authenticated Open WebUI content endpoint.

## Update

1. Back up the existing `data` directory.
2. Extract the complete archive.
3. Run the packaged platform installer or `Install-AutoGenBook.cmd` when
   present.
4. Replace the existing Open WebUI Function with the bundled patched Pipe.
5. Restart Open WebUI Desktop.
6. For an older completed job, send `výstupy JOB_ID`. The original document is
   not regenerated; its artifacts are registered again as native Open WebUI
   files.

## Verification

The build executes the real `_file_payload` method with a representative PDF,
checks the native file-card schema, compiles the Pipe, verifies deterministic
bundle reconstruction, validates ZIP CRC and checks safe TAR paths.
"""
    (project / "FULL_INSTALLER_PROJECT.md").write_text(project_readme, encoding="utf-8")
    docs = project / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "INCIDENT_DURABLE_OUTPUT_DOWNLOADS_0.3.1.md").write_text(
        """# Incident: AutoGenBook outputs were not downloadable in Open WebUI

## Cause

The output was persisted but the response event lacked `type: "file"`.
Open WebUI therefore ignored it as a native assistant attachment. Manual
storage/database code also made the UI contract implicit rather than tested.

## Resolution

The payload adapter now emits a canonical Open WebUI file object and the build
runs an executable contract test. Existing job artifacts can be re-published
with `výstupy JOB_ID` after updating both the Companion installation and Pipe.
""",
        encoding="utf-8",
    )


def copy_tree(source: Path, destination: Path) -> None:
    excluded_roots = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not relative.parts:
            continue
        if relative.parts[0] in {"dist"}:
            continue
        if relative.parts[:2] == ("build", "full-project-stage"):
            continue
        if any(part in excluded_roots for part in relative.parts):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file() and path.suffix != ".pyc":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def promote_launchers(bundle: Path, stage: Path) -> None:
    names = {
        "Install-AutoGenBook.cmd",
        "Uninstall-AutoGenBook.cmd",
        "Configure-OpenWebUI-ApiKeyFile.cmd",
        "Clear-OpenWebUI-ApiKeyFile.cmd",
        "install.sh",
        "install.command",
    }
    for path in bundle.rglob("*"):
        if path.is_file() and path.name in names:
            shutil.copy2(path, stage / path.name)


def create_manifest(stage: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.name == "PROJECT-MANIFEST.json":
            continue
        rows.append(
            {
                "path": path.relative_to(stage).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    manifest = {"version": VERSION, "files": rows}
    (stage / "PROJECT-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def create_zip(stage: Path, path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            arcname = (Path(stage.name) / source.relative_to(stage)).as_posix()
            info = zipfile.ZipInfo(arcname, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, source.read_bytes())


def verify_archives(zip_path: Path, tar_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise AssertionError(f"ZIP CRC failure: {bad}")
        names = archive.namelist()
        if not any(name.endswith("FULL_INSTALLER_PROJECT.md") for name in names):
            raise AssertionError("ZIP lacks full project documentation")
        if not any(name.endswith("durable-output-patch-report-v0.3.1.json") for name in names):
            raise AssertionError("ZIP lacks patch audit report")
    with tarfile.open(tar_path) as archive:
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise AssertionError(f"Unsafe TAR member: {member.name}")
            if member.issym() or member.islnk():
                raise AssertionError(f"Link not allowed in TAR: {member.name}")
    return {
        "zip_crc": "passed",
        "tar_paths": "passed",
        "zip_sha256": digest(zip_path),
        "tar_gz_sha256": digest(tar_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    project = args.project_root.resolve()
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)

    patch_report = project / "build" / "durable-output-patch-report-v0.3.1.json"
    if not patch_report.is_file():
        raise FileNotFoundError("Run patch_openwebui_durable_outputs_v031.py first")
    report = json.loads(patch_report.read_text(encoding="utf-8"))
    if report.get("version") != VERSION or not report.get("payload_contracts"):
        raise AssertionError("Durable-output patch contract was not validated")

    write_docs(project)
    with tempfile.TemporaryDirectory(prefix="agb-v031-package-") as temporary:
        temp = Path(temporary)
        bundle = unpack_bundle(project, temp)
        pipes = find_pipes(bundle)
        if not pipes:
            raise AssertionError("No Pipe found in rebuilt bundle")
        for pipe in pipes:
            validate_pipe(pipe)

        stage = temp / ROOT_NAME
        stage.mkdir(parents=True)
        copy_tree(project, stage)
        packaged = stage / "packaged-integration"
        shutil.copytree(bundle, packaged, dirs_exist_ok=True)
        promote_launchers(bundle, stage)
        manifest = create_manifest(stage)

        zip_path = dist / f"{ROOT_NAME}.zip"
        tar_path = dist / f"{ROOT_NAME}.tar.gz"
        create_zip(stage, zip_path)
        with tarfile.open(tar_path, "w:gz", compresslevel=9) as archive:
            archive.add(stage, arcname=stage.name, recursive=True)

    archive_verification = verify_archives(zip_path, tar_path)
    verification = {
        "version": VERSION,
        "payload_contracts": len(report["payload_contracts"]),
        "patched_pipe_files": report["patched_files"],
        "bundle": report["bundle"],
        "project_manifest_files": len(manifest["files"]),
        "zip": {
            "name": zip_path.name,
            "size": zip_path.stat().st_size,
            "sha256": digest(zip_path),
        },
        "tar_gz": {
            "name": tar_path.name,
            "size": tar_path.stat().st_size,
            "sha256": digest(tar_path),
        },
        **archive_verification,
    }
    final = dist / "AutoGenBook-OpenWebUI-v0.3.1-FINAL-VERIFICATION.json"
    final.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    checksums = dist / f"{ROOT_NAME}-SHA256SUMS.txt"
    checksums.write_text(
        f"{digest(zip_path)}  {zip_path.name}\n{digest(tar_path)}  {tar_path.name}\n",
        encoding="ascii",
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
