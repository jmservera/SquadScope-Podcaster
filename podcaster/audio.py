from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4


TARGET_CONTENT_TYPE = "audio/mpeg"
TARGET_SAMPLE_RATE_HZ = 44_100
TARGET_CHANNELS = 1
MIN_BITRATE_BPS = 64_000
MAX_BITRATE_BPS = 96_000
TARGET_LOUDNESS_LUFS = -16.0
LOUDNESS_TOLERANCE_LUFS = 1.0
MAX_DURATION_SECONDS = 10 * 60
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
OUTRO_SPEECH_DUCK_GAIN = 0.15


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


@dataclass(frozen=True)
class MusicMixSpec:
    intro_full_volume_seconds: float = 10.0
    intro_duck_level_db: float = -18.0
    intro_fade_duration_seconds: float = 2.0
    intro_speech_segments_under_music: int = 2
    outro_start_offset_seconds: float = 75.0
    outro_fade_in_seconds: float = 5.0
    outro_speech_segments_with_music: int = 2

    def __post_init__(self) -> None:
        if self.intro_full_volume_seconds < 0:
            raise ValueError("intro_full_volume_seconds must be non-negative")
        if self.intro_duck_level_db > 0:
            raise ValueError("intro_duck_level_db must be 0 or a negative dB reduction")
        if self.intro_fade_duration_seconds <= 0:
            raise ValueError("intro_fade_duration_seconds must be positive")
        if self.intro_speech_segments_under_music <= 0:
            raise ValueError("intro_speech_segments_under_music must be positive")
        if self.outro_start_offset_seconds < 0:
            raise ValueError("outro_start_offset_seconds must be non-negative")
        if self.outro_fade_in_seconds <= 0:
            raise ValueError("outro_fade_in_seconds must be positive")
        if self.outro_speech_segments_with_music <= 0:
            raise ValueError("outro_speech_segments_with_music must be positive")


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


def stitch_segments(
    segments: list[bytes],
    output_path: Path,
    runner: CommandRunner | None = None,
    *,
    gap_seconds: float = 0.35,
    intro_music: Path | None = None,
    outro_music: Path | None = None,
    mix_spec: MusicMixSpec | None = None,
) -> Path:
    """Concatenate per-voice MP3 segments into one normalized episode MP3.

    Each ``segments`` entry is the MP3 bytes for a single synthesized turn (one
    host voice). They are concatenated in order with a short silent gap between
    turns, then re-encoded to the publication target format (mono, 44.1 kHz,
    96 kbps, loudness-normalized to -16 LUFS) so the output passes the audio
    validation gate. Returns ``output_path``.

    When ``mix_spec`` is not provided, any supplied ``intro_music`` and/or
    ``outro_music`` are concatenated before and after the speech body
    (intro music -> speech -> outro music), separated by the same gentle gap
    for backward compatibility.

    When ``mix_spec`` is provided, intro/outro music is mixed on a timeline:
    the intro can play ahead of speech, duck under the opening segments, and
    fade away; the outro can fade in under the closing segments and continue
    after speech finishes. The final program still goes through the same
    two-pass loudness normalization.
    """

    if not segments:
        raise ValueError("cannot stitch an empty list of audio segments")
    for segment in segments:
        if not isinstance(segment, bytes) or not segment:
            raise ValueError("each audio segment must be non-empty bytes")

    runner = runner or _run_command
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = output_path.parent / f".stitch-{output_path.stem}-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        segment_paths = _write_segments(tmp_dir, segments)

        if mix_spec is not None and (intro_music or outro_music):
            _validate_mix_spec_for_segments(mix_spec, len(segment_paths), intro_music=intro_music, outro_music=outro_music)
            segment_durations = [_probe_duration_seconds(path, runner) for path in segment_paths]
            speech_intermediate = tmp_dir / "speech.wav"
            _concat_audio_files(segment_paths, speech_intermediate, runner, gap_seconds=gap_seconds)
            mixed_intermediate = tmp_dir / "episode.wav"
            _mix_music_with_speech(
                speech_intermediate,
                segment_durations,
                mixed_intermediate,
                runner,
                gap_seconds=gap_seconds,
                intro_music=intro_music,
                outro_music=outro_music,
                mix_spec=mix_spec,
            )
            _two_pass_loudnorm(mixed_intermediate, output_path, runner)
            return output_path

        ordered_paths: list[Path] = []
        if intro_music:
            ordered_paths.append(Path(intro_music))
        ordered_paths.extend(segment_paths)
        if outro_music:
            ordered_paths.append(Path(outro_music))
        intermediate = tmp_dir / "episode.wav"
        _concat_audio_files(ordered_paths, intermediate, runner, gap_seconds=gap_seconds)
        _two_pass_loudnorm(intermediate, output_path, runner)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return output_path


_LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _two_pass_loudnorm(input_path: Path, output_path: Path, runner: CommandRunner) -> None:
    measure = runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(input_path),
            "-af",
            f"{_LOUDNORM_FILTER}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    measured = _parse_loudnorm_json(measure.stderr or "")
    second_filter = _LOUDNORM_FILTER
    if measured:
        second_filter = (
            f"{_LOUDNORM_FILTER}:linear=true"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
        )
    runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-af",
            second_filter,
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE_HZ),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            f"{MAX_BITRATE_BPS // 1000}k",
            str(output_path),
        ]
    )


def _write_segments(tmp_dir: Path, segments: list[bytes]) -> list[Path]:
    segment_paths: list[Path] = []
    for position, segment in enumerate(segments):
        segment_path = tmp_dir / f"segment-{position:03d}.mp3"
        segment_path.write_bytes(segment)
        segment_paths.append(segment_path)
    return segment_paths


def _concat_audio_files(
    input_paths: list[Path],
    output_path: Path,
    runner: CommandRunner,
    *,
    gap_seconds: float,
) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    concat_parts: list[str] = []
    for position, input_path in enumerate(input_paths):
        inputs.extend(["-i", str(input_path)])
        filters.append(f"[{position}:a]aresample=44100,aformat=channel_layouts=mono[a{position}]")
        concat_parts.append(f"[a{position}]")
        if gap_seconds > 0 and position < len(input_paths) - 1:
            filters.append(f"aevalsrc=0:d={_ffmpeg_number(gap_seconds)}:s=44100:c=mono[g{position}]")
            concat_parts.append(f"[g{position}]")

    filter_complex = ";".join(filters) + ";" + "".join(concat_parts) + f"concat=n={len(concat_parts)}:v=0:a=1[out]"
    runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE_HZ),
            str(output_path),
        ]
    )


def _mix_music_with_speech(
    speech_path: Path,
    segment_durations: list[float],
    output_path: Path,
    runner: CommandRunner,
    *,
    gap_seconds: float,
    intro_music: Path | None,
    outro_music: Path | None,
    mix_spec: MusicMixSpec,
) -> None:
    inputs = ["-i", str(speech_path)]
    filters = [f"[0:a]aresample=44100,aformat=channel_layouts=mono[speechsrc]"]
    speech_delay_seconds = mix_spec.intro_full_volume_seconds if intro_music else 0.0
    if speech_delay_seconds > 0:
        filters.append(
            f"[speechsrc]adelay={_ffmpeg_milliseconds(speech_delay_seconds)}:all=1[speech]"
        )
    else:
        filters.append("[speechsrc]anull[speech]")
    current_mix = "[speech]"

    segment_starts, segment_total_duration = _segment_timeline(segment_durations, gap_seconds)

    next_input_index = 1
    if intro_music:
        intro_end = _intro_music_end_seconds(segment_durations, gap_seconds, mix_spec)
        filters.append(
            f"[{next_input_index}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"atrim=end={_ffmpeg_number(intro_end)},asetpts=PTS-STARTPTS,"
            f"volume='{_intro_volume_expression(segment_durations, gap_seconds, mix_spec)}'[intro]"
        )
        filters.append(
            f"{current_mix}[intro]amix=inputs=2:normalize=0:duration=first:weights='1 1'[speech_with_intro]"
        )
        current_mix = "[speech_with_intro]"
        next_input_index += 1
        inputs.extend(["-i", str(intro_music)])

    if outro_music:
        outro_start_segment = len(segment_durations) - mix_spec.outro_speech_segments_with_music
        outro_delay_seconds = speech_delay_seconds + segment_starts[outro_start_segment]
        speech_end_seconds = speech_delay_seconds + segment_total_duration
        outro_speech_overlap_seconds = max(0.0, speech_end_seconds - outro_delay_seconds)
        filters.append(
            f"[{next_input_index}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"atrim=start={_ffmpeg_number(mix_spec.outro_start_offset_seconds)},asetpts=PTS-STARTPTS,"
            f"volume='{_outro_volume_expression(outro_speech_overlap_seconds, mix_spec)}',"
            f"adelay={_ffmpeg_milliseconds(outro_delay_seconds)}:all=1[outro]"
        )
        filters.append(
            f"{current_mix}[outro]amix=inputs=2:normalize=0:duration=longest:weights='1 1'[out]"
        )
        current_mix = "[out]"
        next_input_index += 1
        inputs.extend(["-i", str(outro_music)])

    if current_mix != "[out]":
        filters.append(f"{current_mix}anull[out]")
    filter_complex = ";".join(filters)
    runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE_HZ),
            str(output_path),
        ]
    )


def _validate_mix_spec_for_segments(
    mix_spec: MusicMixSpec,
    segment_count: int,
    *,
    intro_music: Path | None,
    outro_music: Path | None,
) -> None:
    if intro_music and mix_spec.intro_speech_segments_under_music > segment_count:
        raise ValueError("intro_speech_segments_under_music cannot exceed the speech segment count")
    if outro_music and mix_spec.outro_speech_segments_with_music > segment_count:
        raise ValueError("outro_speech_segments_with_music cannot exceed the speech segment count")


def _probe_duration_seconds(path: Path, runner: CommandRunner) -> float:
    probe = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return float(probe.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe did not return a numeric duration for {path}") from exc


def _segment_timeline(segment_durations: list[float], gap_seconds: float) -> tuple[list[float], float]:
    starts: list[float] = []
    current = 0.0
    for index, duration in enumerate(segment_durations):
        starts.append(current)
        current += duration
        if gap_seconds > 0 and index < len(segment_durations) - 1:
            current += gap_seconds
    return starts, current


def _intro_music_end_seconds(
    segment_durations: list[float],
    gap_seconds: float,
    mix_spec: MusicMixSpec,
) -> float:
    segment_starts, _ = _segment_timeline(segment_durations, gap_seconds)
    last_ducked_index = mix_spec.intro_speech_segments_under_music - 1
    duck_end = mix_spec.intro_full_volume_seconds + segment_starts[last_ducked_index] + segment_durations[last_ducked_index]
    return duck_end + (2 * mix_spec.intro_fade_duration_seconds)


def _intro_volume_expression(
    segment_durations: list[float],
    gap_seconds: float,
    mix_spec: MusicMixSpec,
) -> str:
    segment_starts, _ = _segment_timeline(segment_durations, gap_seconds)
    duck_start = mix_spec.intro_full_volume_seconds
    fade_duration = mix_spec.intro_fade_duration_seconds
    last_ducked_index = mix_spec.intro_speech_segments_under_music - 1
    duck_end = duck_start + segment_starts[last_ducked_index] + segment_durations[last_ducked_index]
    fade_down_end = duck_start + fade_duration
    fade_up_end = duck_end + fade_duration
    fade_out_end = duck_end + (2 * fade_duration)
    duck_gain = _db_to_gain(mix_spec.intro_duck_level_db)
    return (
        f"if(lt(t,{_ffmpeg_number(duck_start)}),1,"
        f"if(lt(t,{_ffmpeg_number(fade_down_end)}),"
        f"1-(1-{_ffmpeg_number(duck_gain)})*(t-{_ffmpeg_number(duck_start)})/{_ffmpeg_number(fade_duration)},"
        f"if(lt(t,{_ffmpeg_number(duck_end)}),{_ffmpeg_number(duck_gain)},"
        f"if(lt(t,{_ffmpeg_number(fade_up_end)}),"
        f"{_ffmpeg_number(duck_gain)}+(1-{_ffmpeg_number(duck_gain)})*(t-{_ffmpeg_number(duck_end)})/{_ffmpeg_number(fade_duration)},"
        f"if(lt(t,{_ffmpeg_number(fade_out_end)}),"
        f"1-(t-{_ffmpeg_number(fade_up_end)})/{_ffmpeg_number(fade_duration)},0)))))"
    )


def _db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def _outro_volume_expression(outro_speech_overlap_seconds: float, mix_spec: MusicMixSpec) -> str:
    if outro_speech_overlap_seconds <= 0:
        return "1"

    fade_start = _ffmpeg_number(outro_speech_overlap_seconds)
    fade_end = _ffmpeg_number(outro_speech_overlap_seconds + mix_spec.outro_fade_in_seconds)
    duck_gain = _ffmpeg_number(OUTRO_SPEECH_DUCK_GAIN)
    return (
        f"if(lt(t,{fade_start}),{duck_gain},"
        f"if(lt(t,{fade_end}),"
        f"{duck_gain}+(1-{duck_gain})*(t-{fade_start})/{_ffmpeg_number(mix_spec.outro_fade_in_seconds)},1))"
    )


def _ffmpeg_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _ffmpeg_milliseconds(value: float) -> str:
    return str(int(round(value * 1000)))


def _parse_loudnorm_json(stderr: str) -> dict[str, str] | None:
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(key in data for key in required):
        return None
    return {key: str(data[key]) for key in required}


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
    bitrate_bps = _optional_int_field(stream, "bit_rate")
    if bitrate_bps is None:
        bitrate_bps = _int_field(payload.get("format", {}), "bit_rate")
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


def _optional_int_field(payload: object, field: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(field)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


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
