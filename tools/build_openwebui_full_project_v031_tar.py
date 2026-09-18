#!/usr/bin/env python3
"""Build and verify the complete v0.3.1 installer project from the tar bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

VERSION = "0.3.1"
ROOT_NAME = f"AutoGenBook-OpenWebUI-Full-Installer-Project-v{VERSION}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_bundle(project: Path, destination: Path) -> Path:
    parts = sorted((project / "integration").glob("openwebui_bundle.part*.b64"))
    if not parts:
        raise FileNotFoundError("Integration bundle parts are missing")
    payload = base64.b64decode(
        "".join(part.read_text(encoding="ascii").strip() for part in parts),
        validate=True,
    )
    root = destination / "openwebui_bundle-v0.3.1"
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            if target != resolved and resolved not in target.parents:
                raise RuntimeError(f"Unsafe integration member: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"Link not allowed: {member.name}")
        try:
            archive.extractall(root, filter="data")
        except TypeError:
            archive.extractall(root)
    return root


def find_pipes(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        if "def _file_payload(" in text and "PERSIST_OUTPUTS_TO_OPENWEBUI" in text:
            found.append(path)
    return sorted(found)


def validate_pipe(path: Path) -> None:
    import ast
    import py_compile

    text = path.read_text(encoding="utf-8")
    for marker in ('"type": "file"', '"status": "uploaded"', "chat:message:files", "/api/v1/files/"):
        if marker not in text:
            raise AssertionError(f"{path}: missing {marker}")
    ast.parse(text, filename=str(path))
    py_compile.compile(str(path), doraise=True)


def write_docs(project: Path) -> None:
    (project / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    (project / "FULL_INSTALLER_PROJECT.md").write_text(
        """# AutoGenBook Open WebUI – complete installer project 0.3.1

Version 0.3.1 fixes generated output downloads in the native Open WebUI
assistant-message interface.

## Root cause

The file bytes and database row could be present, but the emitted message object
lacked `type: "file"`. Open WebUI filters assistant attachments by this field,
so it did not render a native file card.

## Corrected contract

Published outputs now contain `type`, persistent `id`, `name`, `size`,
`content_type`, `status` and normalized `meta`. The URL is the same-origin,
authenticated `/api/v1/files/<id>/content?attachment=true` route. The Pipe emits
`chat:message:files`; no Companion bearer token, expiring signature or random
local port is exposed to the user.

## Update procedure

Back up the existing `data` directory, extract the archive, run the packaged
installer, replace the Open WebUI Function with the bundled patched Pipe and
restart Open WebUI Desktop. Enter `výstupy JOB_ID` to publish artifacts of an
older completed job without regenerating its content.

## Validation

The release build executes the actual payload adapter, compiles the Pipe,
checks the canonical file-card shape, verifies the reconstructed bundle,
validates ZIP CRC, rejects unsafe TAR paths and creates SHA-256 manifests.
""",
        encoding="utf-8",
    )
    docs = project / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "INCIDENT_DURABLE_OUTPUT_DOWNLOADS_0.3.1.md").write_text(
        """# Incident: AutoGenBook outputs were not downloadable

The Open WebUI file record could exist while the assistant event lacked
`type: "file"`. The response renderer therefore ignored the object as a native
attachment. Version 0.3.1 normalizes the payload and emits
`chat:message:files`. Existing Companion artifacts can be re-published using
`výstupy JOB_ID`.
""",
        encoding="utf-8",
    )


def copy_project(source: Path, destination: Path) -> None:
    ignored = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not relative.parts:
            continue
        if relative.parts[0] == "dist":
            continue
        if len(relative.parts) >= 2 and relative.parts[:2] == ("build", "full-project-stage"):
            continue
        if any(part in ignored for part in relative.parts):
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
    rows = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "PROJECT-MANIFEST.json":
            rows.append(
                {
                    "path": path.relative_to(stage).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest = {"version": VERSION, "files": rows}
    (stage / "PROJECT-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def write_zip(stage: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            name = (Path(stage.name) / source.relative_to(stage)).as_posix()
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, source.read_bytes())


def verify(zip_path: Path, tar_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise AssertionError(f"ZIP CRC failed: {bad}")
        names = archive.namelist()
        if not any(name.endswith("FULL_INSTALLER_PROJECT.md") for name in names):
            raise AssertionError("Project documentation is missing from ZIP")
        if not any(name.endswith("durable-output-patch-report-v0.3.1.json") for name in names):
            raise AssertionError("Patch report is missing from ZIP")
    with tarfile.open(tar_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            candidate = Path(member.name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise AssertionError(f"Unsafe TAR member: {member.name}")
            if member.issym() or member.islnk():
                raise AssertionError(f"TAR links are not allowed: {member.name}")
    return {"zip_crc": "passed", "tar_paths": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    project = args.project_root.resolve()
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)

    report_path = project / "build" / "durable-output-patch-report-v0.3.1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("version") != VERSION or not report.get("payload_contracts"):
        raise AssertionError("Executable payload contract did not pass")

    write_docs(project)
    with tempfile.TemporaryDirectory(prefix="agb-v031-project-") as temporary:
        temp = Path(temporary)
        bundle = unpack_bundle(project, temp)
        pipes = find_pipes(bundle)
        if not pipes:
            raise AssertionError("Patched Pipe not found in integration bundle")
        for pipe in pipes:
            validate_pipe(pipe)

        stage = temp / ROOT_NAME
        stage.mkdir(parents=True)
        copy_project(project, stage)
        shutil.copytree(bundle, stage / "packaged-integration", dirs_exist_ok=True)
        promote_launchers(bundle, stage)
        manifest = create_manifest(stage)

        zip_path = dist / f"{ROOT_NAME}.zip"
        tar_path = dist / f"{ROOT_NAME}.tar.gz"
        write_zip(stage, zip_path)
        with tarfile.open(tar_path, mode="w:gz", compresslevel=9) as archive:
            archive.add(stage, arcname=stage.name, recursive=True)

    checks = verify(zip_path, tar_path)
    verification = {
        "version": VERSION,
        "payload_contracts": len(report["payload_contracts"]),
        "patched_pipe_files": report["patched_files"],
        "bundle": report["bundle"],
        "project_manifest_files": len(manifest["files"]),
        "zip": {"name": zip_path.name, "size": zip_path.stat().st_size, "sha256": sha256(zip_path)},
        "tar_gz": {"name": tar_path.name, "size": tar_path.stat().st_size, "sha256": sha256(tar_path)},
        **checks,
    }
    (dist / "AutoGenBook-OpenWebUI-v0.3.1-FINAL-VERIFICATION.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dist / f"{ROOT_NAME}-SHA256SUMS.txt").write_text(
        f"{sha256(zip_path)}  {zip_path.name}\n{sha256(tar_path)}  {tar_path.name}\n",
        encoding="ascii",
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
