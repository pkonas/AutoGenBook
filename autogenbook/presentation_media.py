from __future__ import annotations

import json
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from autogenbook.llm_usage import log_openrouter_event
from autogenbook.openrouter_usage import extract_cost_breakdown, extract_usage_breakdown

DEFAULT_IMAGE_MODEL = "openai/gpt-5.4-image-2"
DEFAULT_IMAGE_SIZE = "1024x1024"

_IMAGE_MODEL_CACHE: Optional[List[str]] = None
_UNAVAILABLE_IMAGE_MODELS: set[str] = set()
_IMAGE_MODEL_MODALITIES: Dict[str, List[str]] = {}


TARGET_SR = 22050
TARGET_SUBTYPE = "PCM_16"
TARGET_CHANNELS = 1
DEFAULT_OPENROUTER_TTS_MODEL = "openai/gpt-4o-mini-tts-2025-12-15"
DEFAULT_OPENROUTER_TTS_VOICE = "alloy"

_XTTS_MODEL = None
_VITS_MODEL = None


def chunk_text(text: str, max_len: int = 500) -> List[str]:
    text = re.sub(r"\s+", " ", text.strip())
    sentences = re.split(r"(?<=[.?!])\s+", text)
    chunks: List[str] = []

    for sent in sentences:
        if len(sent) <= max_len:
            chunks.append(sent.strip())
        else:
            parts = re.split(r"(?<=[,;:])\s+", sent)
            current = ""
            for part in parts:
                if len(current) + len(part) + 1 <= max_len:
                    current += (" " if current else "") + part
                else:
                    if current:
                        chunks.append(current.strip())
                    current = part
            if current:
                chunks.append(current.strip())

    final_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) > max_len:
            words = chunk.split()
            cur = ""
            for word in words:
                if len(cur) + len(word) + 1 <= max_len:
                    cur += (" " if cur else "") + word
                else:
                    final_chunks.append(cur.strip())
                    cur = word
            if cur:
                final_chunks.append(cur.strip())
        else:
            final_chunks.append(chunk)

    return final_chunks


def ensure_wav_compatible(in_path: str, out_dir: str = ".", prefix: str = "compat_") -> Optional[str]:
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
    except Exception as exc:
        raise RuntimeError(
            "Missing optional dependencies for audio processing. "
            "Install: pip install soundfile scipy numpy"
        ) from exc

    try:
        data, sr = sf.read(in_path, always_2d=True)
        data = data.astype("float32")

        if data.shape[1] > 1:
            data = data.mean(axis=1)
        else:
            data = data[:, 0]

        if sr != TARGET_SR:
            data = resample_poly(data, TARGET_SR, sr)

        data = data.clip(-1.0, 1.0)
        int16 = (data * 32767.0).astype("int16")

        base = os.path.basename(in_path)
        out_path = os.path.join(out_dir, f"{prefix}{base}")
        sf.write(out_path, int16, TARGET_SR, subtype=TARGET_SUBTYPE)
        return out_path
    except Exception:
        return None


def collect_compatible_refs(search_dir: str = ".") -> List[str]:
    try:
        import glob
        import soundfile as sf
    except Exception as exc:
        raise RuntimeError(
            "Missing optional dependencies for audio processing. "
            "Install: pip install soundfile"
        ) from exc

    refs: List[str] = []
    for path in glob.glob(os.path.join(search_dir, "*.wav")):
        try:
            info = sf.info(path)
            ok_sr = info.samplerate == TARGET_SR
            ok_mono = info.channels == TARGET_CHANNELS
            ok_16 = ("PCM_16" in info.subtype) if info.subtype else False
            if ok_sr and ok_mono and ok_16:
                refs.append(path)
            else:
                fixed = ensure_wav_compatible(path, out_dir=search_dir)
                if fixed:
                    refs.append(fixed)
        except Exception:
            continue
    return refs


def record_reference(filename: str = "reference.wav", duration: int = 6, sr: int = TARGET_SR) -> str:
    try:
        import sounddevice as sd
        import soundfile as sf
    except Exception as exc:
        raise RuntimeError(
            "Missing optional dependencies for audio recording. "
            "Install: pip install sounddevice soundfile"
        ) from exc

    audio = sd.rec(int(duration * sr), samplerate=sr, channels=1, dtype="int16")
    sd.wait()
    sf.write(filename, audio, sr, subtype=TARGET_SUBTYPE)
    return filename


def _get_xtts():
    global _XTTS_MODEL
    if _XTTS_MODEL is None:
        try:
            from TTS.api import TTS  # type: ignore
        except Exception as exc:
            raise RuntimeError("Missing optional dependency 'TTS'. Install: pip install TTS") from exc
        model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
        _XTTS_MODEL = TTS(model_name=model_name, gpu=False)
    return _XTTS_MODEL


def _get_vits():
    global _VITS_MODEL
    if _VITS_MODEL is None:
        try:
            from TTS.api import TTS  # type: ignore
        except Exception as exc:
            raise RuntimeError("Missing optional dependency 'TTS'. Install: pip install TTS") from exc
        model_name = "tts_models/cs/cv/vits"
        _VITS_MODEL = TTS(model_name=model_name, gpu=False)
    return _VITS_MODEL


def synthesize_speech_local_tts(
    text: str,
    out_path: str,
    *,
    xtts_chunk_len: int = 500,
    vits_chunk_len: int = 800,
    search_dir: str = ".",
) -> None:
    try:
        from pydub import AudioSegment  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing optional dependency 'pydub'. Install: pip install pydub") from exc

    references = collect_compatible_refs(search_dir)
    if not references:
        choice = input("No compatible .wav found. Record new reference now? (y/n): ").strip().lower()
        if choice == "y":
            record_reference(os.path.join(search_dir, "reference.wav"), duration=6, sr=TARGET_SR)
            references = collect_compatible_refs(search_dir)

    if references:
        tts = _get_xtts()
        chunks = chunk_text(text, max_len=xtts_chunk_len)
        combined = AudioSegment.silent(duration=0)
        for chunk in chunks:
            success = False
            for attempt_len in [xtts_chunk_len, 300, 100, 50, 30]:
                try:
                    if len(chunk) > attempt_len:
                        subchunks = chunk_text(chunk, max_len=attempt_len)
                    else:
                        subchunks = [chunk]
                    for sub in subchunks:
                        tmp_path = os.path.join(Path(out_path).parent, f"xtts_chunk_{os.getpid()}.wav")
                        tts.tts_to_file(
                            text=sub,
                            file_path=tmp_path,
                            speaker_wav=references,
                            language="cs",
                        )
                        seg = AudioSegment.from_file(tmp_path)
                        combined += seg
                    success = True
                    break
                except Exception:
                    continue
            if not success:
                continue
        combined.export(out_path, format="wav")
        return

    tts = _get_vits()
    speaker = None
    if hasattr(tts, "speakers") and tts.speakers:
        speaker = tts.speakers[0]

    chunks = chunk_text(text, max_len=vits_chunk_len)
    combined = AudioSegment.silent(duration=0)
    for chunk in chunks:
        tmp_path = os.path.join(Path(out_path).parent, f"vits_chunk_{os.getpid()}.wav")
        tts.tts_to_file(text=chunk, file_path=tmp_path, speaker=speaker)
        seg = AudioSegment.from_file(tmp_path)
        combined += seg
    combined.export(out_path, format="wav")


def synthesize_speech_openrouter_tts(
    text: str,
    out_path: str,
    *,
    model_name: str = DEFAULT_OPENROUTER_TTS_MODEL,
    out_dir: Optional[Path] = None,
) -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Please set OPENROUTER_API_KEY environment variable.")

    response_format = "mp3" if Path(out_path).suffix.lower() == ".mp3" else "pcm"
    voice = os.getenv("OPENROUTER_TTS_VOICE", DEFAULT_OPENROUTER_TTS_VOICE).strip() or DEFAULT_OPENROUTER_TTS_VOICE
    url = "https://openrouter.ai/api/v1/audio/speech"
    payload = {
        "model": model_name,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"TTS request failed: {resp.status_code} {resp.text}")

    usage_breakdown = extract_usage_breakdown(headers=dict(resp.headers))
    cost_breakdown = extract_cost_breakdown(headers=dict(resp.headers))
    if out_dir is not None:
        log_openrouter_event(
            Path(out_dir),
            label="presentation_tts",
            model=model_name,
            modality="audio",
            usage_breakdown=usage_breakdown,
            cost_breakdown=cost_breakdown,
            extra={"provider": "openrouter", "voice": voice, "response_format": response_format},
        )
    Path(out_path).write_bytes(resp.content)


def format_srt_timestamp(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    return str(td)[:-3].replace(".", ",")


class _ImageRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_message = error_message or ""


def _extract_modalities(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _extract_output_modalities(model: Dict[str, Any]) -> List[str]:
    output_modalities = _extract_modalities(model.get("output_modalities"))
    arch = model.get("architecture")
    if isinstance(arch, dict):
        output_modalities = output_modalities or _extract_modalities(arch.get("output_modalities"))
    return [m.lower() for m in output_modalities if m]


def _model_supports_image(model: Dict[str, Any]) -> bool:
    output_modalities = _extract_output_modalities(model)
    if output_modalities:
        return "image" in output_modalities
    modalities: List[str] = []
    for key in ("modalities", "modality", "input_modalities"):
        modalities.extend(_extract_modalities(model.get(key)))
    arch = model.get("architecture")
    if isinstance(arch, dict):
        for key in ("modalities", "modality", "input_modalities"):
            modalities.extend(_extract_modalities(arch.get(key)))
    modalities = [m.lower() for m in modalities if m]
    if "image" in modalities:
        return True
    model_id = str(model.get("id") or "").lower()
    keywords = ("image", "flux", "stable", "sd", "diffusion", "dalle", "ideogram", "kandinsky")
    return any(keyword in model_id for keyword in keywords)


def _discover_openrouter_image_models(api_key: str, base_url: str) -> List[str]:
    global _IMAGE_MODEL_CACHE
    if _IMAGE_MODEL_CACHE is not None:
        return list(_IMAGE_MODEL_CACHE)

    base_url = base_url.rstrip("/")
    if base_url.endswith("/api/v1"):
        models_url = f"{base_url}/models"
    else:
        models_url = f"{base_url}/api/v1/models"

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        resp = requests.get(models_url, headers=headers, timeout=30)
    except Exception:
        _IMAGE_MODEL_CACHE = []
        return []
    if resp.status_code >= 400:
        _IMAGE_MODEL_CACHE = []
        return []
    try:
        payload = resp.json()
    except Exception:
        _IMAGE_MODEL_CACHE = []
        return []

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        _IMAGE_MODEL_CACHE = []
        return []

    image_models: List[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id or not isinstance(model_id, str):
            continue
        output_modalities = _extract_output_modalities(item)
        if output_modalities:
            _IMAGE_MODEL_MODALITIES[model_id] = output_modalities
        if _model_supports_image(item):
            image_models.append(model_id)

    _IMAGE_MODEL_CACHE = image_models
    return list(image_models)


def _get_output_modalities_for_model(model_id: str, api_key: str, base_url: str) -> List[str]:
    if model_id in _IMAGE_MODEL_MODALITIES:
        return list(_IMAGE_MODEL_MODALITIES[model_id])
    _discover_openrouter_image_models(api_key, base_url)
    return list(_IMAGE_MODEL_MODALITIES.get(model_id, []))


def _is_model_unavailable(error: _ImageRequestError) -> bool:
    message = (error.error_message or str(error)).lower()
    if error.status_code in {400, 404}:
        tokens = ("not a valid model id", "no endpoints found", "model not found")
        return any(token in message for token in tokens)
    tokens = (
        "missing message.images",
        "missing choices",
        "no image_url",
        "output_modalities missing image",
        "does not advertise image output",
    )
    return any(token in message for token in tokens)


def _parse_model_fallbacks(env_value: str) -> List[str]:
    if not env_value:
        return []
    return [item.strip() for item in env_value.split(",") if item.strip()]


def _iter_fallback_models(
    primary_model: str,
    api_key: str,
    base_url: str,
) -> List[str]:
    fallbacks: List[str] = []
    fallbacks.extend(_parse_model_fallbacks(os.environ.get("OPENROUTER_IMAGE_MODEL_FALLBACKS", "")))
    discover = os.environ.get("OPENROUTER_IMAGE_DISCOVER", "").strip().lower()
    if discover not in {"0", "false", "no", "off"}:
        fallbacks.extend(_discover_openrouter_image_models(api_key, base_url))
    limit_raw = os.environ.get("OPENROUTER_IMAGE_FALLBACK_LIMIT", "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else 6

    seen: set[str] = set()
    ordered: List[str] = []
    for model in fallbacks:
        if not model or model == primary_model or model in seen:
            continue
        ordered.append(model)
        seen.add(model)
        if limit and len(ordered) >= limit:
            break
    return ordered


def render_video_from_pdf(
    pdf_path: Path,
    audio_items: List[Tuple[int, Path, float]],
    *,
    out_path: Path,
    temp_dir: Path,
    slide_index_map,
    dpi: int = 150,
    fps: int = 30,
) -> None:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing optional dependency 'PyMuPDF'. Install: pip install pymupdf") from exc
    try:
        from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips  # type: ignore
    except Exception as exc:
        try:
            from moviepy import AudioFileClip, ImageClip, concatenate_videoclips  # type: ignore
        except Exception:
            raise RuntimeError("Missing optional dependency 'moviepy'. Install: pip install moviepy") from exc

    temp_dir.mkdir(parents=True, exist_ok=True)
    pdf_doc = fitz.open(pdf_path)
    clips = []

    for slide_index, audio_path, duration_sec in audio_items:
        page_index = slide_index_map(slide_index)
        if page_index < 1 or page_index > len(pdf_doc):
            continue
        page = pdf_doc[page_index - 1]
        pix = page.get_pixmap(dpi=dpi)
        img_path = temp_dir / f"slide_{slide_index}.png"
        pix.save(str(img_path))

        img_clip = ImageClip(str(img_path))
        if hasattr(img_clip, "set_duration"):
            img_clip = img_clip.set_duration(duration_sec)
        else:
            img_clip = img_clip.with_duration(duration_sec)

        audio_clip = AudioFileClip(str(audio_path))
        if hasattr(img_clip, "set_audio"):
            img_clip = img_clip.set_audio(audio_clip)
        else:
            img_clip = img_clip.with_audio(audio_clip)
        clips.append(img_clip)

    if not clips:
        raise RuntimeError("No video clips generated. Check slide indices and audio inputs.")

    final_video = concatenate_videoclips(clips, method="compose")
    final_video.write_videofile(str(out_path), fps=fps, codec="libx264")


def generate_slide_image_openrouter(
    prompt: str,
    out_path: str,
    *,
    model_name: str = DEFAULT_IMAGE_MODEL,
    size: str = DEFAULT_IMAGE_SIZE,
    out_dir: Optional[Path] = None,
) -> None:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("AUTOGENBOOK_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY for image generation.")

    base_url = (
        os.environ.get("OPENROUTER_BASE_URL", "").strip()
        or os.environ.get("AUTOGENBOOK_LLM_BASE_URL", "").strip()
        or "https://openrouter.ai/api/v1"
    )
    base_url = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    if base_url.endswith("/api/v1"):
        endpoint = f"{base_url}/chat/completions"
    else:
        endpoint = f"{base_url}/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if os.environ.get("OPENROUTER_HTTP_REFERER"):
        headers["HTTP-Referer"] = os.environ["OPENROUTER_HTTP_REFERER"]
    if os.environ.get("OPENROUTER_X_TITLE"):
        headers["X-Title"] = os.environ["OPENROUTER_X_TITLE"]

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }

    image_config: Dict[str, Any] = {}
    image_config_json = os.environ.get("OPENROUTER_IMAGE_CONFIG_JSON", "").strip()
    if image_config_json:
        try:
            parsed = json.loads(image_config_json)
            if isinstance(parsed, dict):
                image_config.update(parsed)
        except json.JSONDecodeError:
            pass

    aspect_ratio = os.environ.get("OPENROUTER_IMAGE_ASPECT_RATIO", "").strip()
    if aspect_ratio:
        image_config["aspect_ratio"] = aspect_ratio

    size_override = os.environ.get("OPENROUTER_IMAGE_SIZE", "").strip()
    include_size = os.environ.get("OPENROUTER_IMAGE_INCLUDE_SIZE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if size_override:
        image_config["image_size"] = size_override
    elif include_size and size:
        image_config["image_size"] = size

    if image_config:
        payload["image_config"] = image_config

    def _request_image(selected_model: str) -> None:
        payload["model"] = selected_model
        output_modalities = _get_output_modalities_for_model(selected_model, api_key, base_url)
        if output_modalities:
            if "image" not in output_modalities:
                raise _ImageRequestError(
                    f"Model {selected_model} does not advertise image output.",
                    error_message="output_modalities missing image",
                )
            if "text" in output_modalities:
                payload["modalities"] = ["image", "text"]
            else:
                payload["modalities"] = ["image"]
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        if resp.status_code >= 400:
            error_message = ""
            try:
                error_payload = resp.json()
                if isinstance(error_payload, dict):
                    error_message = str(error_payload.get("error") or "")
            except Exception:
                error_message = resp.text[:600] if resp.text else ""
            raise _ImageRequestError(
                f"OpenRouter image request failed ({resp.status_code}) at {endpoint}: {error_message}",
                status_code=resp.status_code,
                error_message=error_message,
            )

        try:
            data = resp.json()
        except Exception as exc:
            snippet = resp.text[:600] if resp.text else ""
            raise _ImageRequestError(
                "OpenRouter image endpoint returned non-JSON response "
                f"at {endpoint}: {snippet}"
            ) from exc

        if isinstance(data, dict) and data.get("error"):
            raise _ImageRequestError(
                f"OpenRouter image error: {data['error']}",
                status_code=resp.status_code,
                error_message=str(data.get("error")),
            )

        usage_breakdown = extract_usage_breakdown(data, dict(resp.headers))
        cost_breakdown = extract_cost_breakdown(data, dict(resp.headers))
        if out_dir is not None:
            log_openrouter_event(
                Path(out_dir),
                label="presentation_image",
                model=selected_model,
                modality="image",
                usage_breakdown=usage_breakdown,
                cost_breakdown=cost_breakdown,
                extra={"size": size},
            )

        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise _ImageRequestError("OpenRouter image response missing choices.")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        images = message.get("images") if isinstance(message, dict) else None
        if not images:
            raise _ImageRequestError("OpenRouter image response missing message.images.")

        first = images[0]
        image_url = None
        if isinstance(first, dict):
            image_url = (
                first.get("image_url", {}).get("url")
                if isinstance(first.get("image_url"), dict)
                else None
            )
            if image_url is None:
                image_url = (
                    first.get("imageUrl", {}).get("url")
                    if isinstance(first.get("imageUrl"), dict)
                    else None
                )
            if image_url is None:
                image_url = first.get("url")
        if not image_url:
            raise _ImageRequestError("OpenRouter image response had no image_url.")

        if image_url.startswith("data:"):
            b64_data = image_url.split(",", 1)[-1]
            Path(out_path).write_bytes(base64.b64decode(b64_data))
            return

        image_resp = requests.get(image_url, timeout=120)
        image_resp.raise_for_status()
        Path(out_path).write_bytes(image_resp.content)

    primary_model = model_name.strip() if model_name else ""
    last_error: Optional[Exception] = None
    if primary_model and primary_model not in _UNAVAILABLE_IMAGE_MODELS:
        try:
            _request_image(primary_model)
            return
        except _ImageRequestError as exc:
            last_error = exc
            if _is_model_unavailable(exc):
                _UNAVAILABLE_IMAGE_MODELS.add(primary_model)
            else:
                raise

    fallback_models = _iter_fallback_models(primary_model, api_key, base_url)
    for fallback in fallback_models:
        if fallback in _UNAVAILABLE_IMAGE_MODELS:
            continue
        try:
            _request_image(fallback)
            return
        except _ImageRequestError as exc:
            last_error = exc
            if _is_model_unavailable(exc):
                _UNAVAILABLE_IMAGE_MODELS.add(fallback)
            else:
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(
        "No image-capable model available. Choose a model that lists "
        "\"image\" in output_modalities (see /models), then set "
        "--presentation-image-model or OPENROUTER_IMAGE_MODEL_FALLBACKS."
    )
