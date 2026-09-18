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

VERSION = "0.3.2"
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PROJECT_NAME = f"AutoGenBook-OpenWebUI-Full-Installer-Project-v{VERSION}"
FIXED_EPOCH = 946684800  # 2000-01-01, deterministic archive metadata

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
                path.chmod(0o755 if path.suffix in {".sh", ".command", ".cmd"} else 0o644)
            except OSError:
                pass


def inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    return rows


def write_zip(root: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = (Path(PROJECT_NAME) / path.relative_to(root)).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2000, 1, 1, 0, 0, 0))
            mode = 0o755 if path.suffix in {".sh", ".command", ".cmd"} else 0o644
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


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
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                unsafe.append(member.name)
    if unsafe:
        raise RuntimeError(f"Unsafe TAR members: {unsafe[:10]}")
    return {"entries": count, "safe_paths": "passed"}


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob(f"{PROJECT_NAME}*"):
        if old.is_file():
            old.unlink()
        elif old.is_dir():
            shutil.rmtree(old)

    with tempfile.TemporaryDirectory(prefix="autogenbook-v032-build-") as temp:
        temp_root = Path(temp)
        stage = temp_root / PROJECT_NAME
        shutil.copytree(ROOT, stage, ignore=ignore)

        upstream_readme = stage / "README.md"
        if upstream_readme.exists():
            upstream_readme.rename(stage / "README-AUTOGENBOOK-UPSTREAM.md")
        shutil.copy2(
            stage / "docs" / "openwebui-authenticated-downloads-v0.3.2.md",
            stage / "README.md",
        )
        (stage / "VERSION.txt").write_text(VERSION + "\n", encoding="utf-8")
        (stage / "BUILD-PROVENANCE.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "source_commit": os.environ.get("GITHUB_SHA", "local"),
                    "source_branch": os.environ.get("GITHUB_REF_NAME", "local"),
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "python": sys.version,
                    "purpose": "Authenticated durable Open WebUI output downloads",
                    "installer_entry_point": "Install-AutoGenBook.cmd",
                    "migration_source": "AutoGenBook Open WebUI Function 0.3.1",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        compile_targets = [
            stage / "tools" / "apply_openwebui_authenticated_downloads_v032.py",
            stage / "tools" / "install_authenticated_downloads_v032.py",
            stage / "tools" / "build_openwebui_authenticated_downloads_project_v032.py",
        ]
        for target in compile_targets:
            subprocess.run([sys.executable, "-m", "py_compile", str(target)], check=True)
        subprocess.run(
            [sys.executable, str(stage / "tools" / "install_authenticated_downloads_v032.py"), "--self-test"],
            check=True,
            cwd=stage,
        )

        audit = stage / "AUDIT-v0.3.2.md"
        audit.write_text(
            "# Audit AutoGenBook Open WebUI 0.3.2\n\n"
            "## Potvrzené příčiny\n\n"
            "- Přímá navigace na `/api/v1/files/<id>/content` neobsahuje bearer hlavičku SPA.\n"
            "- Záznam výstupu s `process=false` neměl `data.content`, takže modal zobrazil `No content`.\n"
            "- Historický instalační integrační bundle je tar.gz a obsahuje Pipe 0.1.0; updater 0.3.2 jej proto nepoužívá jako autoritativní Function.\n\n"
            "## Opravený kontrakt\n\n"
            "- HMAC capability route na stejném Open WebUI origin.\n"
            "- Kontrola vlastníka, souborového záznamu a fyzických dat.\n"
            "- GET, HEAD, Range, 206, 416, ETag a blokové streamování.\n"
            "- Absolutní URL v souborové kartě a neprázdné `data.content`.\n"
            "- Route je vložena před `spa-static-files`.\n"
            "- Aktualizace probíhá nad Function skutečně uloženou v Open WebUI.\n\n"
            "## Omezení auditu\n\n"
            "Build runner nemá přístup k uživatelově lokální instanci Open WebUI. Produkční instalátor proto před zápisem načte skutečný zdroj Function 0.3.1, aplikuje deterministickou migraci, provede AST a kontraktní validaci a až poté použije admin API.\n",
            encoding="utf-8",
        )

        rows = inventory(stage)
        manifest_path = stage / "PROJECT-MANIFEST.json"
        manifest_path.write_text(
            json.dumps({"version": VERSION, "files": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        checksums = "\n".join(f"{row['sha256']}  {row['path']}" for row in rows) + "\n"
        (stage / "PROJECT-SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
        normalize_tree(stage)

        zip_path = DIST / f"{PROJECT_NAME}.zip"
        tar_path = DIST / f"{PROJECT_NAME}.tar.gz"
        write_zip(stage, zip_path)
        write_tar_gz(stage, tar_path)

    verification = {
        "version": VERSION,
        "zip": {"file": zip_path.name, "size": zip_path.stat().st_size, "sha256": sha256(zip_path), **verify_zip(zip_path)},
        "tar_gz": {"file": tar_path.name, "size": tar_path.stat().st_size, "sha256": sha256(tar_path), **verify_tar(tar_path)},
        "python_compile": "passed",
        "updater_self_test": "passed",
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
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
