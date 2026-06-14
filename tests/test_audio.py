from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from podcaster.audio import (
    AudioMetadata,
    MAX_FILE_SIZE_BYTES,
    compute_segment_timeline,
    normalize_audio,
    placeholder_audio_validation,
    probe_audio,
    validate_audio_metadata,
)


def test_valid_audio_metadata_passes() -> None:
    result = validate_audio_metadata(
        AudioMetadata(
            duration_seconds=599.0,
            loudness_lufs=-16.2,
            sample_rate_hz=44100,
            bitrate_bps=192000,
            channels=1,
            content_type="audio/mpeg",
            byte_length=9_000_000,
            sha256="a" * 64,
        )
    )

    assert result.ready is True
    assert result.status == "passed"
    assert result.errors == []
    assert result.to_manifest()["metadata"]["duration_seconds"] == 599.0


def test_overlong_oversized_audio_fails_without_override() -> None:
    result = validate_audio_metadata(
        AudioMetadata(
            duration_seconds=601.0,
            loudness_lufs=-16.0,
            sample_rate_hz=44100,
            bitrate_bps=64000,
            channels=1,
            content_type="audio/mpeg",
            byte_length=MAX_FILE_SIZE_BYTES + 1,
            sha256="b" * 64,
        )
    )

    assert result.ready is False
    assert result.status == "failed"
    assert "audio duration must not exceed 10 minutes without manual override" in result.errors
    assert "audio file size must be under 10 MB" in result.errors


def test_overlong_audio_with_override_records_warning() -> None:
    result = validate_audio_metadata(
        AudioMetadata(
            duration_seconds=601.0,
            loudness_lufs=-16.0,
            sample_rate_hz=44100,
            bitrate_bps=192000,
            channels=1,
            content_type="audio/mpeg",
            byte_length=1_000_000,
            sha256="c" * 64,
        ),
        manual_duration_override=True,
    )

    assert result.ready is True
    assert result.warnings == ["duration exceeds 10 minutes with manual override recorded"]


def test_invalid_audio_metadata_reports_all_constraint_failures() -> None:
    result = validate_audio_metadata(
        AudioMetadata(
            duration_seconds=120.0,
            loudness_lufs=-20.0,
            sample_rate_hz=48000,
            bitrate_bps=128000,
            channels=2,
            content_type="audio/wav",
            byte_length=1_000_000,
            sha256="d" * 64,
        )
    )

    assert result.ready is False
    assert "audio must be audio/mpeg" in result.errors
    assert "audio sample rate must be 44100 Hz" in result.errors
    assert "audio must be mono" in result.errors
    assert "audio bitrate must be 192000 bps" in result.errors
    assert "audio loudness must be near -16 LUFS" in result.errors


def test_placeholder_audio_is_blocked_from_publication() -> None:
    result = placeholder_audio_validation(byte_length=42, sha256="e" * 64)
    manifest = result.to_manifest()

    assert result.ready is False
    assert result.status == "blocked"
    assert manifest["metadata"]["content_type"] == "audio/mpeg"
    assert "not publishable audio" in result.errors[0]


def test_normalize_audio_invokes_ffmpeg_with_mvp_constraints(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    normalize_audio(tmp_path / "input.wav", tmp_path / "out" / "episode.mp3", runner=runner)

    command = commands[0]
    assert command[:4] == ["ffmpeg", "-hide_banner", "-y", "-i"]
    assert "-ac" in command
    assert command[command.index("-ac") + 1] == "1"
    assert "-ar" in command
    assert command[command.index("-ar") + 1] == "44100"
    assert "-b:a" in command
    assert command[command.index("-b:a") + 1] == "192k"
    assert command[-1].endswith("episode.mp3")


def test_probe_audio_parses_ffprobe_and_loudness_output(tmp_path: Path) -> None:
    audio_file = tmp_path / "episode.mp3"
    audio_file.write_bytes(b"fake-mp3")

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(
                command,
                0,
                '{"streams":[{"codec_name":"mp3","sample_rate":"44100","channels":1,"bit_rate":"96000"}],"format":{"duration":"120.5","bit_rate":"96000"}}',
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "    I:         -16.1 LUFS\n")

    metadata = probe_audio(audio_file, sha256="f" * 64, runner=runner)

    assert metadata.duration_seconds == 120.5
    assert metadata.loudness_lufs == -16.1
    assert metadata.sample_rate_hz == 44100
    assert metadata.bitrate_bps == 96000
    assert metadata.channels == 1
    assert metadata.content_type == "audio/mpeg"
    assert metadata.byte_length == len(b"fake-mp3")


def test_probe_audio_falls_back_to_format_bitrate_when_stream_bitrate_missing(
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "episode.mp3"
    audio_file.write_bytes(b"fake-mp3")

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(
                command,
                0,
                '{"streams":[{"codec_name":"mp3","sample_rate":"44100","channels":1,"bit_rate":"N/A"}],"format":{"duration":"120.5","bit_rate":"96000"}}',
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "    I:         -16.1 LUFS\n")

    metadata = probe_audio(audio_file, sha256="f" * 64, runner=runner)

    assert metadata.bitrate_bps == 96000


def test_missing_invalid_ffprobe_output_fails_closed(tmp_path: Path) -> None:
    audio_file = tmp_path / "episode.mp3"
    audio_file.write_bytes(b"fake-mp3")

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, '{"streams":[]}', "")

    try:
        probe_audio(audio_file, sha256="f" * 64, runner=runner)
    except RuntimeError as exc:
        assert "did not include an audio stream" in str(exc)
    else:
        raise AssertionError("invalid ffprobe output should fail closed")


@pytest.mark.parametrize(
    ("durations", "gap_seconds"),
    [
        ([-0.1, 1.0], 0.35),
        ([1.0, 2.0], -0.35),
    ],
)
def test_compute_segment_timeline_rejects_negative_values(
    durations: list[float], gap_seconds: float
) -> None:
    with pytest.raises(ValueError):
        compute_segment_timeline(durations, gap_seconds)
