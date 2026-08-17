from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    if new in text:
        print(f"{label}: already applied")
        return
    if old not in text:
        raise RuntimeError(f"{label}: expected source fragment not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: applied")


def main() -> int:
    repo = Path.cwd()

    runner = repo / "companion" / "src" / "autogenbook_companion" / "runner.py"
    old = '        env = {str(k): str(v) for k, v in os.environ.items()}\n        package_root = str(Path(__file__).resolve().parents[1])\n'
    new = '''        env = {str(k): str(v) for k, v in os.environ.items()}
        bundle_root = self.settings.source_dir.parent
        tools_root = bundle_root / "tools"
        path_entries: list[str] = []
        common_bin = tools_root / "bin"
        if common_bin.is_dir():
            path_entries.append(str(common_bin))
        tinytex_bin = tools_root / "tinytex" / "bin"
        if tinytex_bin.is_dir():
            path_entries.extend(str(item) for item in sorted(tinytex_bin.iterdir()) if item.is_dir())
        runtime_scripts = [
            Path(sys.executable).parent,
            Path(sys.executable).parent / "Scripts",
            Path(sys.executable).parent / "bin",
        ]
        path_entries.extend(str(item) for item in runtime_scripts if item.is_dir())
        current_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*path_entries, current_path] if current_path else path_entries)
        pandoc = common_bin / ("pandoc.exe" if os.name == "nt" else "pandoc")
        ffmpeg = common_bin / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        piper_model = tools_root / "piper" / "cs_CZ-jirka-medium.onnx"
        piper_config = tools_root / "piper" / "cs_CZ-jirka-medium.onnx.json"
        if pandoc.exists():
            env["PYPANDOC_PANDOC"] = str(pandoc)
        if ffmpeg.exists():
            env["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)
            env["FFMPEG_BINARY"] = str(ffmpeg)
        if piper_model.exists() and piper_config.exists():
            env["AUTOGENBOOK_PIPER_MODEL"] = str(piper_model)
            env["AUTOGENBOOK_PIPER_CONFIG"] = str(piper_config)
        native_lib = tools_root / "lib"
        if native_lib.is_dir() and os.name != "nt":
            existing_ld = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = os.pathsep.join(
                [str(native_lib), existing_ld] if existing_ld else [str(native_lib)]
            )
        package_root = str(Path(__file__).resolve().parents[1])
'''
    replace_once(runner, old, new, "companion worker bundled toolchain environment")

    installer = repo / "installer" / "install.py"
    marker = 'def start_companion(install_dir: Path) -> dict[str, Any]:\n'
    helper = '''def bundled_environment(install_dir: Path) -> dict[str, str]:
    env = {str(k): str(v) for k, v in os.environ.items()}
    tools_root = install_dir / "tools"
    common_bin = tools_root / "bin"
    path_entries: list[str] = []
    if common_bin.is_dir():
        path_entries.append(str(common_bin))
    tinytex_bin = tools_root / "tinytex" / "bin"
    if tinytex_bin.is_dir():
        path_entries.extend(str(item) for item in sorted(tinytex_bin.iterdir()) if item.is_dir())
    python = bundled_python(install_dir)
    for item in (python.parent, python.parent / "Scripts", python.parent / "bin"):
        if item.is_dir():
            path_entries.append(str(item))
    current_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*path_entries, current_path] if current_path else path_entries)
    pandoc = common_bin / ("pandoc.exe" if os.name == "nt" else "pandoc")
    ffmpeg = common_bin / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    piper_model = tools_root / "piper" / "cs_CZ-jirka-medium.onnx"
    piper_config = tools_root / "piper" / "cs_CZ-jirka-medium.onnx.json"
    if pandoc.exists():
        env["PYPANDOC_PANDOC"] = str(pandoc)
    if ffmpeg.exists():
        env["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)
        env["FFMPEG_BINARY"] = str(ffmpeg)
    if piper_model.exists() and piper_config.exists():
        env["AUTOGENBOOK_PIPER_MODEL"] = str(piper_model)
        env["AUTOGENBOOK_PIPER_CONFIG"] = str(piper_config)
    native_lib = tools_root / "lib"
    if native_lib.is_dir() and os.name != "nt":
        existing_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [str(native_lib), existing_ld] if existing_ld else [str(native_lib)]
        )
    return env


'''
    text = installer.read_text(encoding="utf-8-sig")
    if helper not in text:
        if marker not in text:
            raise RuntimeError("installer bundled environment insertion point not found")
        text = text.replace(marker, helper + marker, 1)
        installer.write_text(text, encoding="utf-8")
        print("installer bundled toolchain environment: applied")
    else:
        print("installer bundled toolchain environment: already applied")
    replace_once(
        installer,
        '        "cwd": str(install_dir),\n    }\n',
        '        "cwd": str(install_dir),\n        "env": bundled_environment(install_dir),\n    }\n',
        "installer passes bundled environment to Companion",
    )

    media = repo / "autogenbook" / "presentation_media.py"
    old = '''def synthesize_speech_local_tts(
    text: str,
    out_path: str,
    *,
    xtts_chunk_len: int = 500,
    vits_chunk_len: int = 800,
    search_dir: str = ".",
) -> None:
    try:
        from pydub import AudioSegment  # type: ignore
'''
    new = '''def synthesize_speech_local_tts(
    text: str,
    out_path: str,
    *,
    xtts_chunk_len: int = 500,
    vits_chunk_len: int = 800,
    search_dir: str = ".",
) -> None:
    # Dependency-complete desktop builds bundle Piper and a Czech voice.
    piper_model = os.environ.get("AUTOGENBOOK_PIPER_MODEL", "").strip()
    piper_config = os.environ.get("AUTOGENBOOK_PIPER_CONFIG", "").strip()
    if piper_model and piper_config and Path(piper_model).exists() and Path(piper_config).exists():
        try:
            import wave
            from piper import PiperVoice  # type: ignore

            voice = PiperVoice.load(piper_model, piper_config)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
            return
        except Exception as exc:
            raise RuntimeError(f"Bundled Piper TTS failed: {exc}") from exc

    try:
        from pydub import AudioSegment  # type: ignore
'''
    replace_once(media, old, new, "bundled Czech Piper TTS fallback")

    requirements = repo / "requirements.txt"
    replace_once(
        requirements,
        "pypandoc>=1.13",
        "pypandoc_binary>=1.15,<2",
        "bundle Pandoc through pypandoc_binary",
    )

    # Make the build metadata explicitly state that the packaged runtime is dependency-complete.
    build_release = repo / "build" / "build_release.py"
    old = '        "protocol_version": 1,\n        "platform": platform,\n'
    new = '        "protocol_version": 1,\n        "dependency_profile": "complete-offline",\n        "platform": platform,\n'
    replace_once(build_release, old, new, "dependency-complete manifest marker")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
