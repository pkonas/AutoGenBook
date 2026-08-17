from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

PIPER_VOICE_VERSION = "v1.0.0"
PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    f"{PIPER_VOICE_VERSION}/cs/cs_CZ/jirka/medium"
)
PIPER_MODEL_NAME = "cs_CZ-jirka-medium.onnx"
PIPER_CONFIG_NAME = "cs_CZ-jirka-medium.onnx.json"
PIPER_CARD_NAME = "MODEL_CARD"
PIPER_MODEL_SHA256 = "cbd5c900acacc8e8cbecd64347abb8de39c00a9d3104bed06fee92e4f319efc8"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_python(runtime_root: Path) -> Path:
    candidates = [
        runtime_root / "python" / "python.exe",
        runtime_root / "python" / "bin" / "python3",
        runtime_root / "python" / "bin" / "python",
        runtime_root / "runtime" / "python" / "python.exe",
        runtime_root / "runtime" / "python" / "bin" / "python3",
        runtime_root / "runtime" / "python" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise RuntimeError(f"Bundled Python executable was not found below {runtime_root}")


def capture(command: list[str], *, env: dict[str, str] | None = None) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    return result.stdout.strip()


def copy_executable(source: Path, destination: Path) -> dict[str, Any]:
    if not source.exists():
        raise RuntimeError(f"Required executable does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    try:
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    return {
        "path": destination.as_posix(),
        "size": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def copy_tree(source: Path, destination: Path, *, platform_name: str) -> None:
    """Copy a deep runtime tree without losing files on Windows.

    TinyTeX contains deeply nested package names.  `shutil.copytree` can still
    encounter legacy MAX_PATH handling in subprocesses on hosted Windows runners,
    while Robocopy is designed for this exact workload and treats return codes
    0..7 as success.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if platform_name.startswith("windows"):
        robocopy = shutil.which("robocopy")
        if not robocopy:
            raise RuntimeError("Robocopy is required to vendor the Windows TinyTeX tree")
        command = [
            robocopy,
            str(source),
            str(destination),
            "/E",
            "/COPY:DAT",
            "/DCOPY:DAT",
            "/R:2",
            "/W:1",
            "/NP",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
        ]
        print("+", " ".join(command), flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode >= 8:
            raise RuntimeError(f"Robocopy failed with exit code {result.returncode}")
    else:
        shutil.copytree(
            source,
            destination,
            symlinks=False,
            ignore_dangling_symlinks=True,
            copy_function=shutil.copy2,
        )


def download(url: str, destination: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "AutoGenBook-offline-builder/0.2"})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    digest = sha256_file(destination)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {url}: expected {expected_sha256}, received {digest}"
        )
    return {
        "url": url,
        "path": destination.as_posix(),
        "size": destination.stat().st_size,
        "sha256": digest,
    }


def locate_portaudio(explicit: Path | None) -> Path | None:
    if explicit:
        candidate = explicit.expanduser().resolve()
        if not candidate.exists():
            raise RuntimeError(f"Explicit PortAudio library does not exist: {candidate}")
        return candidate
    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/libportaudio.so.2"),
        Path("/usr/lib/aarch64-linux-gnu/libportaudio.so.2"),
        Path("/usr/lib64/libportaudio.so.2"),
        Path("/usr/lib/libportaudio.so.2"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    try:
        output = subprocess.run(
            ["ldconfig", "-p"], check=False, capture_output=True, text=True
        ).stdout
        for line in output.splitlines():
            if "libportaudio.so.2" in line and "=>" in line:
                candidate = Path(line.split("=>", 1)[1].strip())
                if candidate.exists():
                    return candidate.resolve()
    except OSError:
        pass
    return None


def copy_source_archives(source_dir: Path | None, target_dir: Path) -> list[dict[str, Any]]:
    if source_dir is None:
        return []
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise RuntimeError(f"Third-party source directory does not exist: {source_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source in sorted(source_dir.iterdir()):
        if not source.is_file():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        rows.append(
            {
                "path": target.as_posix(),
                "size": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    if not rows:
        raise RuntimeError(f"No third-party source archives were found in {source_dir}")
    return rows


def write_hashes(stage: Path) -> None:
    excluded = {"SHA256SUMS.json", "SHA256SUMS.txt"}
    rows: list[dict[str, Any]] = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name not in excluded:
            rows.append(
                {
                    "path": path.relative_to(stage).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    (stage / "SHA256SUMS.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (stage / "SHA256SUMS.txt").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vendor every non-Python runtime dependency into an AutoGenBook installer stage."
    )
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--tinytex-root", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--portaudio", type=Path)
    parser.add_argument("--resolved-packages", type=Path)
    parser.add_argument("--third-party-source-dir", type=Path)
    args = parser.parse_args()

    stage = args.stage.resolve()
    runtime_root = args.runtime_root.resolve()
    tinytex_root = args.tinytex_root.expanduser().resolve()
    if not stage.is_dir():
        raise RuntimeError(f"Installer stage does not exist: {stage}")
    if not tinytex_root.is_dir():
        raise RuntimeError(f"TinyTeX root does not exist: {tinytex_root}")

    python = find_python(runtime_root)
    tools = stage / "tools"
    common_bin = tools / "bin"
    common_bin.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "profile": "complete-offline",
        "platform": args.platform,
        "python": str(python),
        "components": {},
    }

    pandoc_raw = capture(
        [str(python), "-c", "import pypandoc; print(pypandoc.get_pandoc_path())"]
    ).splitlines()[-1]
    pandoc_source = Path(pandoc_raw.strip()).resolve()
    pandoc_name = "pandoc.exe" if args.platform.startswith("windows") else "pandoc"
    pandoc_target = common_bin / pandoc_name
    metadata["components"]["pandoc"] = copy_executable(pandoc_source, pandoc_target)
    metadata["components"]["pandoc"]["version"] = capture(
        [str(pandoc_target), "--version"]
    ).splitlines()[0]

    ffmpeg_raw = capture(
        [str(python), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"]
    ).splitlines()[-1]
    ffmpeg_source = Path(ffmpeg_raw.strip()).resolve()
    ffmpeg_name = "ffmpeg.exe" if args.platform.startswith("windows") else "ffmpeg"
    ffmpeg_target = common_bin / ffmpeg_name
    metadata["components"]["ffmpeg"] = copy_executable(ffmpeg_source, ffmpeg_target)
    metadata["components"]["ffmpeg"]["version"] = capture(
        [str(ffmpeg_target), "-version"]
    ).splitlines()[0]

    tinytex_target = tools / "tinytex"
    copy_tree(tinytex_root, tinytex_target, platform_name=args.platform)
    metadata["components"]["tinytex"] = {
        "path": tinytex_target.as_posix(),
        "source": tinytex_root.as_posix(),
        "files": sum(1 for item in tinytex_target.rglob("*") if item.is_file()),
    }

    piper_dir = tools / "piper"
    piper_dir.mkdir(parents=True, exist_ok=True)
    model = download(
        f"{PIPER_VOICE_BASE}/{PIPER_MODEL_NAME}?download=true",
        piper_dir / PIPER_MODEL_NAME,
        expected_sha256=PIPER_MODEL_SHA256,
    )
    config = download(
        f"{PIPER_VOICE_BASE}/{PIPER_CONFIG_NAME}?download=true",
        piper_dir / PIPER_CONFIG_NAME,
    )
    card = download(
        f"{PIPER_VOICE_BASE}/{PIPER_CARD_NAME}?download=true",
        piper_dir / PIPER_CARD_NAME,
    )
    metadata["components"]["piper_voice_cs_CZ_jirka_medium"] = {
        "version": PIPER_VOICE_VERSION,
        "license": "see bundled MODEL_CARD",
        "model": model,
        "config": config,
        "model_card": card,
    }

    if args.platform.startswith("linux"):
        portaudio = locate_portaudio(args.portaudio)
        if portaudio is None:
            raise RuntimeError("libportaudio.so.2 is required for the complete Linux offline build")
        native_target = tools / "lib" / "libportaudio.so.2"
        native_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(portaudio, native_target)
        metadata["components"]["portaudio"] = {
            "path": native_target.as_posix(),
            "source": portaudio.as_posix(),
            "size": native_target.stat().st_size,
            "sha256": sha256_file(native_target),
        }

    if args.resolved_packages and args.resolved_packages.exists():
        target = tools / "resolved-packages.txt"
        shutil.copy2(args.resolved_packages, target)
        metadata["resolved_packages"] = {
            "path": target.as_posix(),
            "sha256": sha256_file(target),
        }

    source_rows = copy_source_archives(
        args.third_party_source_dir,
        tools / "third_party_sources",
    )
    if source_rows:
        metadata["corresponding_source_archives"] = source_rows

    notices = tools / "THIRD_PARTY_NOTICES.md"
    notices.write_text(
        "# Bundled third-party runtime components\n\n"
        "This dependency-complete distribution contains the following runtime components in addition "
        "to the Python packages listed in `resolved-packages.txt` and the bundled wheelhouse.\n\n"
        "- CPython standalone distribution — Python Software Foundation License.\n"
        "- Pandoc from `pypandoc_binary` — GPL-2.0-or-later.\n"
        "- FFmpeg from `imageio-ffmpeg` — the license depends on the recorded binary build configuration.\n"
        "- TinyTeX / TeX Live packages — individual TeX Live package licenses apply.\n"
        "- Piper runtime (`piper-tts` 1.6.0) — GPL-3.0-or-later; its corresponding source archive "
        "is bundled under `third_party_sources`.\n"
        "- Czech Piper voice `cs_CZ-jirka-medium` — see the bundled voice `MODEL_CARD`.\n"
        "- PortAudio shared library in Linux builds — MIT-style PortAudio license.\n\n"
        "The exact files, versions, source URLs and SHA-256 hashes are recorded in `toolchain.json`.\n",
        encoding="utf-8",
    )

    toolchain_path = tools / "toolchain.json"
    toolchain_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update(
        {
            "dependency_profile": "complete-offline",
            "network_required_for_installation": False,
            "bundled_toolchain_manifest": "tools/toolchain.json",
            "bundled_components": sorted(metadata["components"]),
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_hashes(stage)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
