#!/usr/bin/env python3
from __future__ import annotations

import compileall
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.3.3"
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PROJECT_NAME = f"AutoGenBook-OpenWebUI-Full-Installer-Project-v{VERSION}"
FIXED_EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "946684800"))

EXCLUDED_NAMES = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDED_NAMES or Path(name).suffix.casefold() in EXCLUDED_SUFFIXES
    }


def normalize_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            os.utime(path, (FIXED_EPOCH, FIXED_EPOCH))
            try:
                executable = path.suffix.casefold() in {".sh", ".command", ".cmd"}
                path.chmod(0o755 if executable else 0o644)
            except OSError:
                pass


def inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, object]]:
    excluded = exclude or set()
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    return rows


def write_zip(root: Path, target: Path) -> None:
    stamp = datetime.fromtimestamp(FIXED_EPOCH, tz=timezone.utc)
    date_time = (max(1980, stamp.year), stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = (Path(PROJECT_NAME) / path.relative_to(root)).as_posix()
            info = zipfile.ZipInfo(relative, date_time=date_time)
            executable = path.suffix.casefold() in {".sh", ".command", ".cmd"}
            info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def write_tar_gz(root: Path, target: Path) -> None:
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(root.rglob("*")):
                    relative = Path(PROJECT_NAME) / path.relative_to(root)
                    info = archive.gettarinfo(str(path), arcname=relative.as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = FIXED_EPOCH
                    if path.is_file():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def verify_zip(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        names = archive.namelist()
    if bad:
        raise RuntimeError(f"ZIP CRC failure: {bad}")
    return {"entries": len(names), "crc": "passed"}


def verify_tar(path: Path) -> dict[str, object]:
    unsafe: list[str] = []
    count = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            count += 1
            pure = Path(member.name)
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                unsafe.append(member.name)
    if unsafe:
        raise RuntimeError(f"Unsafe TAR members: {unsafe[:10]}")
    return {"entries": count, "safe_paths": "passed"}


def run_checked(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob(f"{PROJECT_NAME}*"):
        if old.is_file():
            old.unlink()
        elif old.is_dir():
            shutil.rmtree(old)

    validation_commands: list[list[str]] = []
    with tempfile.TemporaryDirectory(prefix="autogenbook-v033-build-") as temp:
        temp_root = Path(temp)
        stage = temp_root / PROJECT_NAME
        shutil.copytree(ROOT, stage, ignore=ignore)

        upstream_readme = stage / "README.md"
        if upstream_readme.exists():
            upstream_readme.rename(stage / "README-AUTOGENBOOK-UPSTREAM.md")
        shutil.copy2(
            stage / "docs" / "openwebui-capability-downloads-v0.3.3.md",
            stage / "README.md",
        )
        (stage / "VERSION.txt").write_text(VERSION + "\n", encoding="utf-8")
        built_at = datetime.fromtimestamp(FIXED_EPOCH, tz=timezone.utc).isoformat()
        (stage / "BUILD-PROVENANCE.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "source_commit": os.environ.get("GITHUB_SHA", "local"),
                    "source_branch": os.environ.get("GITHUB_REF_NAME", "local"),
                    "source_date_epoch": FIXED_EPOCH,
                    "built_at": built_at,
                    "python": sys.version,
                    "purpose": "Browser-independent HMAC capability downloads for AutoGenBook outputs",
                    "installer_entry_point": "Install-AutoGenBook.cmd",
                    "api_key_storage": "external TXT reference; never copied",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        compile_targets = [
            stage / "tools" / "apply_openwebui_capability_downloads_v033.py",
            stage / "tools" / "install_capability_downloads_v033.py",
            stage / "tools" / "build_openwebui_capability_downloads_project_v033.py",
            stage / "tests" / "test_openwebui_capability_downloads_v033.py",
        ]
        for target in compile_targets:
            run_checked([sys.executable, "-m", "py_compile", str(target)], cwd=stage)

        validation_commands = [
            [sys.executable, str(stage / "tools" / "install_capability_downloads_v033.py"), "--self-test"],
            [sys.executable, "-m", "pytest", "-q", "tests/test_openwebui_capability_downloads_v033.py"],
        ]
        for command in validation_commands:
            run_checked(command, cwd=stage)

        audit = {
            "version": VERSION,
            "release_gate": "passed",
            "confirmed_incident": {
                "landing_get": 200,
                "ticket_post": 401,
                "download_stream_reached": False,
                "root_cause": "Electron/new-window storage isolation removed the SPA token from the landing-page request",
            },
            "fixed_contract": {
                "landing_page": False,
                "ticket_endpoint": False,
                "browser_session_required": False,
                "direct_hmac_capability": True,
                "persistent_secret": True,
                "get": True,
                "head": True,
                "range": True,
                "large_file_streaming": True,
                "spa_route_order": "before catch-all",
            },
            "tests": ["patcher self-test", "direct unauthenticated capability download", "HEAD", "Range", "tamper rejection", "secret persistence", "deleted output revocation"],
        }
        (stage / "AUDIT-v0.3.3.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (stage / "AUDIT-v0.3.3.md").write_text(
            "# Audit AutoGenBook Open WebUI 0.3.3\n\n"
            "Release gate: **PASS**\n\n"
            "## Potvrzený incident\n\n"
            "Landing route vracela 200, ale každý následný ticket POST vracel 401; stream endpoint nebyl dosažen. "
            "Příčinou byla závislost na browser tokenu v jiném Electron kontextu.\n\n"
            "## Oprava\n\n"
            "Landing/ticket tok byl odstraněn. Výstupy používají stabilní HMAC capability URL, persistentní secret, "
            "kontrolu databázového vlastníka a streamování GET/HEAD/Range.\n\n"
            "## Omezení\n\n"
            "Capability URL je přístupové oprávnění k jednomu souboru; osoba s celou URL jej může stáhnout. "
            "Smazání souboru nebo rotace secretu přístup zruší. Projekt není kryptograficky podepsaný Windows EXE.\n",
            encoding="utf-8",
        )

        excluded_manifest = {"PROJECT-MANIFEST.json", "PROJECT-SHA256SUMS.txt"}
        rows = inventory(stage, exclude=excluded_manifest)
        (stage / "PROJECT-MANIFEST.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "self_referential_files_excluded": sorted(excluded_manifest),
                    "files": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (stage / "PROJECT-SHA256SUMS.txt").write_text(
            "".join(f"{row['sha256']}  {row['path']}\n" for row in rows),
            encoding="utf-8",
        )
        normalize_tree(stage)

        zip_path = DIST / f"{PROJECT_NAME}.zip"
        tar_path = DIST / f"{PROJECT_NAME}.tar.gz"
        write_zip(stage, zip_path)
        write_tar_gz(stage, tar_path)

        # Validate the exact source extracted from the deliverable ZIP.
        extracted = temp_root / "extracted"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extracted)
        extracted_root = extracted / PROJECT_NAME
        for command in validation_commands:
            rewritten = [
                str(extracted_root / Path(part).relative_to(stage)) if str(part).startswith(str(stage)) else part
                for part in command
            ]
            run_checked(rewritten, cwd=extracted_root)

    verification = {
        "version": VERSION,
        "zip": {
            "file": zip_path.name,
            "size": zip_path.stat().st_size,
            "sha256": sha256(zip_path),
            **verify_zip(zip_path),
        },
        "tar_gz": {
            "file": tar_path.name,
            "size": tar_path.stat().st_size,
            "sha256": sha256(tar_path),
            **verify_tar(tar_path),
        },
        "python_compile": "passed",
        "patcher_self_test": "passed",
        "capability_http_tests": "passed",
        "tests_from_extracted_zip": "passed",
        "release_gate": "passed",
    }
    verification_path = DIST / f"AutoGenBook-OpenWebUI-v{VERSION}-FINAL-VERIFICATION.json"
    verification_path.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    checksum_path = DIST / f"AutoGenBook-OpenWebUI-v{VERSION}-SHA256SUMS.txt"
    checksum_path.write_text(
        f"{verification['zip']['sha256']}  {zip_path.name}\n"
        f"{verification['tar_gz']['sha256']}  {tar_path.name}\n"
        f"{sha256(verification_path)}  {verification_path.name}\n",
        encoding="utf-8",
    )
    deliverables = DIST / f"AutoGenBook-OpenWebUI-v{VERSION}-DELIVERABLES.json"
    deliverables.write_text(
        json.dumps(
            {
                "version": VERSION,
                "files": [
                    {"name": zip_path.name, "sha256": sha256(zip_path), "role": "complete-installer-project"},
                    {"name": tar_path.name, "sha256": sha256(tar_path), "role": "complete-installer-project-alternate"},
                    {"name": verification_path.name, "sha256": sha256(verification_path), "role": "verification"},
                    {"name": checksum_path.name, "sha256": sha256(checksum_path), "role": "checksums"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
