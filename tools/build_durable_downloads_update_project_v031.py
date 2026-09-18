#!/usr/bin/env python3
"""Build the complete AutoGenBook Open WebUI v0.3.1 update project."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

VERSION = "0.3.1"
ROOT_NAME = f"AutoGenBook-OpenWebUI-Full-Installer-Project-v{VERSION}"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


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


def write_readme(project: Path) -> None:
    (project / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    (project / "FULL_INSTALLER_PROJECT.md").write_text(
        """# AutoGenBook Open WebUI – full installer/update project 0.3.1

This complete project repairs native download of generated AutoGenBook outputs
in Open WebUI while preserving all existing jobs, inputs, checkpoints and
artifacts.

## Confirmed cause

The 0.3.0 Pipe persisted output bytes and an Open WebUI file record but emitted
an assistant attachment object without `type: "file"`. Current Open WebUI
renders response attachments only when their type is `file` or `image`.
Consequently, the output was not a native downloadable file card even though a
persistent file record could exist.

## Corrected contract

Version 0.3.1 normalizes every published artifact to include:

- `type: "file"`;
- the persistent Open WebUI file `id`;
- `name`, `filename`, `size`, `content_type` and `meta`;
- `status: "uploaded"`;
- the authenticated same-origin URL
  `/api/v1/files/<id>/content?attachment=true`.

The Pipe emits `chat:message:files`. No temporary Companion URL, bearer token,
random local port or expiring signature is shown to the user.

## Windows update

1. Back up `%LOCALAPPDATA%\\Programs\\AutoGenBook OpenWebUI\\data`.
2. Extract the whole archive.
3. Run `Apply-Durable-Downloads-v0.3.1.cmd`.
4. The script creates `dist\\AutoGenBook-OpenWebUI-Function-v0.3.1.py` and
   patches a discoverable installed copy, while retaining a `.v0.3.0.bak`
   backup.
5. In Open WebUI open **Admin Panel → Functions → autogenbook_companion**,
   replace the entire source with the generated file, save and enable it.
6. Restart Open WebUI Desktop.
7. For an older completed job, send `výstupy JOB_ID`. Its existing artifacts
   are republished as native Open WebUI files; content is not regenerated.

When the installed Function source cannot be found automatically, export or
copy it from Open WebUI and run:

```cmd
python tools\\apply_openwebui_durable_downloads_v031.py ^
  --function-file "C:\\path\\AutoGenBook-OpenWebUI-Function-v0.3.0.py" ^
  --output-dir dist
```

## Safety

The updater changes only Function source. It does not read or modify API-key
values, Open WebUI databases, AutoGenBook `data`, projects, jobs, KB1/KB2,
checkpoints or generated outputs.

## Build verification

The release workflow runs an executable contract test against the actual
replacement method, compiles every delivered Python source, scans for temporary
Companion download URLs, creates a SHA-256 file manifest, validates ZIP CRC and
rejects unsafe TAR members.
""",
        encoding="utf-8",
    )
    docs = project / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "INCIDENT_DURABLE_OUTPUT_DOWNLOADS_0.3.1.md").write_text(
        """# Incident: generated outputs were not downloadable through Open WebUI

## Root cause

The output publisher returned an Open WebUI file model with a URL, but omitted
`type: "file"`. The Open WebUI response component filters assistant
attachments to `image` and `file`, so the object was ignored as a native file
card.

## Resolution

The payload is now normalized to the complete assistant-file schema and emitted
through `chat:message:files`. Stable links use Open WebUI's authenticated file
endpoint, never the Companion endpoint. Existing completed jobs are migrated by
`výstupy JOB_ID` without another LLM run.
""",
        encoding="utf-8",
    )


def create_manifest(root: Path) -> dict[str, object]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "PROJECT-MANIFEST.json":
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest = {"version": VERSION, "files": files}
    (root / "PROJECT-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def deterministic_zip(root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            name = (Path(root.name) / path.relative_to(root)).as_posix()
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())


def validate_sources(root: Path) -> int:
    compiled = 0
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "site-packages", "__pycache__"} for part in path.parts):
            continue
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        compiled += 1
    return compiled


def verify_archives(zip_path: Path, tar_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise AssertionError(f"ZIP CRC failure: {bad}")
        names = archive.namelist()
        required = (
            "FULL_INSTALLER_PROJECT.md",
            "Apply-Durable-Downloads-v0.3.1.cmd",
            "tools/apply_openwebui_durable_downloads_v031.py",
            "PROJECT-MANIFEST.json",
        )
        for suffix in required:
            if not any(name.endswith(suffix) for name in names):
                raise AssertionError(f"ZIP is missing {suffix}")
    with tarfile.open(tar_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise AssertionError(f"Unsafe TAR path: {member.name}")
            if member.issym() or member.islnk():
                raise AssertionError(f"TAR links are forbidden: {member.name}")
    return {"zip_crc": "passed", "tar_paths": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    project = args.project_root.resolve()
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)

    self_test = project / "build" / "durable-downloads-v0.3.1-self-test.json"
    subprocess.run(
        [
            sys.executable,
            str(project / "tools" / "apply_openwebui_durable_downloads_v031.py"),
            "--self-test",
            "--report",
            str(self_test),
        ],
        check=True,
    )
    contract = json.loads(self_test.read_text(encoding="utf-8"))["self_test"]
    if contract.get("type") != "file" or contract.get("status") != "uploaded":
        raise AssertionError("Canonical Open WebUI file payload contract failed")

    write_readme(project)
    with tempfile.TemporaryDirectory(prefix="agb-durable-v031-") as temporary:
        stage = Path(temporary) / ROOT_NAME
        stage.mkdir(parents=True)
        copy_project(project, stage)
        manifest = create_manifest(stage)
        compiled = validate_sources(stage)
        zip_path = dist / f"{ROOT_NAME}.zip"
        tar_path = dist / f"{ROOT_NAME}.tar.gz"
        deterministic_zip(stage, zip_path)
        with tarfile.open(tar_path, mode="w:gz", compresslevel=9) as archive:
            archive.add(stage, arcname=stage.name, recursive=True)

    archive_checks = verify_archives(zip_path, tar_path)
    verification = {
        "version": VERSION,
        "contract": contract,
        "compiled_python_files": compiled,
        "project_manifest_files": len(manifest["files"]),
        "zip": {
            "name": zip_path.name,
            "size": zip_path.stat().st_size,
            "sha256": sha256(zip_path),
        },
        "tar_gz": {
            "name": tar_path.name,
            "size": tar_path.stat().st_size,
            "sha256": sha256(tar_path),
        },
        **archive_checks,
    }
    final = dist / "AutoGenBook-OpenWebUI-v0.3.1-FINAL-VERIFICATION.json"
    final.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    checksums = dist / f"{ROOT_NAME}-SHA256SUMS.txt"
    checksums.write_text(
        f"{sha256(zip_path)}  {zip_path.name}\n{sha256(tar_path)}  {tar_path.name}\n",
        encoding="ascii",
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
