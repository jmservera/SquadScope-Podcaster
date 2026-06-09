from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


TARGET_CONTENT_TYPE = "audio/mpeg"
TARGET_SAMPLE_RATE_HZ = 44_100
TARGET_CHANNELS = 1
MIN_BITRATE_BPS = 64_000
MAX_BITRATE_BPS = 96_000
TARGET_LOUDNESS_LUFS = -16.0
LOUDNESS_TOLERANCE_LUFS = 1.0
MAX_DURATION_SECONDS = 10 * 60
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


class CommandRunner(Protocol):
    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        ...


@dataclass(frozen=True)
class AudioMetadata:
    duration_seconds: float
    loudness_lufs: float | None
    sample_rate_hz: int
    bitrate_bps: int
    channels: int
    content_type: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class AudioValidationResult:
    status: str
    ready: bool
    errors: list[str]
    warnings: list[str]
    metadata: AudioMetadata | None
    constraints: dict[str, object]

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema_version": "squadscope-podcaster-audio-validation-v1",
            "status": self.status,
            "ready": self.ready,
            "errors": self.errors,
            "warnings": self.warnings,
            "constraints": self.constraints,
            "metadata": _metadata_to_manifest(self.metadata),
        }


def normalize_audio(input_path: Path, output_path: Path, runner: CommandRunner | None = None) -> None:
    runner = runner or _run_command
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE_HZ),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            f"{MAX_BITRATE_BPS // 1000}k",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            str(output_path),
        ]
    )


def probe_audio(path: Path, sha256: str, runner: CommandRunner | None = None) -> AudioMetadata:
    runner = runner or _run_command
    probe = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,sample_rate,channels,bit_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(probe.stdout)
    stream = _first_audio_stream(payload)
    file_size = path.stat().st_size
    duration_seconds = _float_field(payload.get("format", {}), "duration")
    bitrate_bps = _int_field(stream, "bit_rate") or _int_field(payload.get("format", {}), "bit_rate")
    sample_rate_hz = _int_field(stream, "sample_rate")
    channels = _int_field(stream, "channels")
    codec_name = stream.get("codec_name")
    content_type = TARGET_CONTENT_TYPE if codec_name == "mp3" else f"audio/{codec_name or 'unknown'}"
    return AudioMetadata(
        duration_seconds=duration_seconds,
        loudness_lufs=measure_loudness(path, runner=runner),
        sample_rate_hz=sample_rate_hz,
        bitrate_bps=bitrate_bps,
        channels=channels,
        content_type=content_type,
        byte_length=file_size,
        sha256=sha256,
    )


def measure_loudness(path: Path, runner: CommandRunner | None = None) -> float | None:
    runner = runner or _run_command
    result = runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
    )
    matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr)
    if not matches:
        return None
    return float(matches[-1])


def validate_audio_metadata(metadata: AudioMetadata, *, manual_duration_override: bool = False) -> AudioValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if metadata.content_type != TARGET_CONTENT_TYPE:
        errors.append("audio must be audio/mpeg")
    if metadata.sample_rate_hz != TARGET_SAMPLE_RATE_HZ:
        errors.append("audio sample rate must be 44100 Hz")
    if metadata.channels != TARGET_CHANNELS:
        errors.append("audio must be mono")
    if not (MIN_BITRATE_BPS <= metadata.bitrate_bps <= MAX_BITRATE_BPS):
        errors.append("audio bitrate must be between 64000 and 96000 bps")
    if metadata.duration_seconds > MAX_DURATION_SECONDS and not manual_duration_override:
        errors.append("audio duration must not exceed 10 minutes without manual override")
    if metadata.byte_length > MAX_FILE_SIZE_BYTES:
        errors.append("audio file size must be under 10 MB")
    if metadata.loudness_lufs is None:
        errors.append("audio loudness must be measured in LUFS")
    elif abs(metadata.loudness_lufs - TARGET_LOUDNESS_LUFS) > LOUDNESS_TOLERANCE_LUFS:
        errors.append("audio loudness must be near -16 LUFS")
    if metadata.duration_seconds > MAX_DURATION_SECONDS and manual_duration_override:
        warnings.append("duration exceeds 10 minutes with manual override recorded")

    return AudioValidationResult(
        status="passed" if not errors else "failed",
        ready=not errors,
        errors=errors,
        warnings=warnings,
        metadata=metadata,
        constraints=_constraints(),
    )


def placeholder_audio_validation(*, byte_length: int, sha256: str) -> AudioValidationResult:
    return AudioValidationResult(
        status="blocked",
        ready=False,
        errors=["audio artifact is a deterministic placeholder, not publishable audio"],
        warnings=["run ffmpeg normalization and validation after TTS synthesis is implemented"],
        metadata=AudioMetadata(
            duration_seconds=0.0,
            loudness_lufs=None,
            sample_rate_hz=0,
            bitrate_bps=0,
            channels=0,
            content_type=TARGET_CONTENT_TYPE,
            byte_length=byte_length,
            sha256=sha256,
        ),
        constraints=_constraints(),
    )


def invalid_ffmpeg_result(error: str) -> AudioValidationResult:
    return AudioValidationResult(
        status="failed",
        ready=False,
        errors=[error],
        warnings=[],
        metadata=None,
        constraints=_constraints(),
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{command[0]} is required for audio validation") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()[:500] if exc.stderr else ""
        raise RuntimeError(f"{command[0]} failed during audio validation: {stderr}") from exc


def _first_audio_stream(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe output was not a JSON object")
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise RuntimeError("ffprobe output did not include an audio stream")
    return streams[0]


def _int_field(payload: object, field: str) -> int:
    if not isinstance(payload, dict):
        raise RuntimeError(f"ffprobe output did not include {field}")
    value = payload.get(field)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise RuntimeError(f"ffprobe output did not include numeric {field}")


def _float_field(payload: object, field: str) -> float:
    if not isinstance(payload, dict):
        raise RuntimeError(f"ffprobe output did not include {field}")
    value = payload.get(field)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise RuntimeError(f"ffprobe output did not include numeric {field}")


def _constraints() -> dict[str, object]:
    return {
        "content_type": TARGET_CONTENT_TYPE,
        "sample_rate_hz": TARGET_SAMPLE_RATE_HZ,
        "channels": TARGET_CHANNELS,
        "bitrate_bps": {"min": MIN_BITRATE_BPS, "max": MAX_BITRATE_BPS},
        "loudness_lufs": {"target": TARGET_LOUDNESS_LUFS, "tolerance": LOUDNESS_TOLERANCE_LUFS},
        "max_duration_seconds": MAX_DURATION_SECONDS,
        "max_file_size_bytes": MAX_FILE_SIZE_BYTES,
    }


def _metadata_to_manifest(metadata: AudioMetadata | None) -> dict[str, object] | None:
    if metadata is None:
        return None
    return {
        "duration_seconds": metadata.duration_seconds,
        "loudness_lufs": metadata.loudness_lufs,
        "sample_rate_hz": metadata.sample_rate_hz,
        "bitrate_bps": metadata.bitrate_bps,
        "channels": metadata.channels,
        "content_type": metadata.content_type,
        "byte_length": metadata.byte_length,
        "sha256": metadata.sha256,
    }
