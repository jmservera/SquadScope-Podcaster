from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from podcaster import audio

_has_ffmpeg = shutil.which("ffmpeg") is not None


def _completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout=stdout, stderr=stderr)


def test_stitch_segments_rejects_empty_list(tmp_path):
    with pytest.raises(ValueError):
        audio.stitch_segments([], tmp_path / "out.mp3", runner=lambda cmd: _completed())


def test_stitch_segments_rejects_empty_bytes(tmp_path):
    with pytest.raises(ValueError):
        audio.stitch_segments([b""], tmp_path / "out.mp3", runner=lambda cmd: _completed())


def test_stitch_segments_runs_concat_then_two_pass_loudnorm(tmp_path):
    calls: list[list[str]] = []
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "output_i" : "-16.0", "output_tp" : "-1.5", '
        '"output_lra" : "4.0", "output_thresh" : "-26.2", "normalization_type" : "dynamic", '
        '"target_offset" : "0.2" }'
    )
    output_path = tmp_path / "episode.mp3"

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        # The loudnorm measurement pass writes JSON to stderr.
        if "print_format=json" in " ".join(command):
            return _completed(stderr=measure_json)
        # Emulate ffmpeg producing the final output file on the last pass.
        if command[-1] == str(output_path):
            output_path.write_bytes(b"ID3-final-mp3")
        return _completed()

    result = audio.stitch_segments([b"seg-a", b"seg-b", b"seg-c"], output_path, runner=runner, gap_seconds=0.3)

    assert result == output_path
    # Three passes: concat, loudnorm measure, loudnorm apply.
    assert len(calls) == 3
    concat_cmd = " ".join(calls[0])
    # Three real inputs, with two silent gaps between them, concatenated to 5 parts.
    assert concat_cmd.count("-i ") == 3
    assert "aevalsrc=0:d=0.3" in concat_cmd
    assert "concat=n=5:v=0:a=1[out]" in concat_cmd
    # Second pass measures, third pass applies the measured values.
    assert "print_format=json" in " ".join(calls[1])
    applied = " ".join(calls[2])
    assert "measured_I=-17.4" in applied
    assert "offset=0.2" in applied
    assert "libmp3lame" in applied


def test_parse_loudnorm_json_handles_missing_block():
    assert audio._parse_loudnorm_json("no json here") is None


def test_parse_loudnorm_json_extracts_measured_values():
    stderr = (
        "some ffmpeg log\n"
        '{ "input_i" : "-18.2", "input_tp" : "-2.0", "input_lra" : "5.1", '
        '"input_thresh" : "-28.0", "target_offset" : "-0.3" }\n'
    )
    parsed = audio._parse_loudnorm_json(stderr)
    assert parsed == {
        "input_i": "-18.2",
        "input_tp": "-2.0",
        "input_lra": "5.1",
        "input_thresh": "-28.0",
        "target_offset": "-0.3",
    }


def test_stitch_segments_wraps_speech_with_intro_and_outro_music(tmp_path):
    intro = tmp_path / "intro.mp3"
    outro = tmp_path / "outro.mp3"
    intro.write_bytes(b"INTRO-STINGER")
    outro.write_bytes(b"OUTRO-STINGER")
    output_path = tmp_path / "episode.mp3"
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
    )

    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "print_format=json" in " ".join(command):
            return _completed(stderr=measure_json)
        if command[-1] == str(output_path):
            output_path.write_bytes(b"ID3-final-mp3")
        return _completed()

    result = audio.stitch_segments(
        [b"seg-a", b"seg-b"],
        output_path,
        runner=runner,
        gap_seconds=0.3,
        intro_music=intro,
        outro_music=outro,
    )

    assert result == output_path
    concat_cmd = " ".join(calls[0])
    # intro + 2 speech + outro = 4 real inputs.
    assert concat_cmd.count("-i ") == 4
    # 4 inputs with 3 gaps between them concatenate to 7 parts.
    assert "concat=n=7:v=0:a=1[out]" in concat_cmd


def test_stitch_segments_with_mix_spec_keeps_backward_compatible_concat_when_none(tmp_path):
    intro = tmp_path / "intro.mp3"
    outro = tmp_path / "outro.mp3"
    intro.write_bytes(b"INTRO-STINGER")
    outro.write_bytes(b"OUTRO-STINGER")
    output_path = tmp_path / "episode.mp3"
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "print_format=json" in " ".join(command):
            return _completed(stderr=measure_json)
        if command[-1] == str(output_path):
            output_path.write_bytes(b"ID3-final-mp3")
        return _completed()

    audio.stitch_segments(
        [b"seg-a", b"seg-b"],
        output_path,
        runner=runner,
        gap_seconds=0.3,
        intro_music=intro,
        outro_music=outro,
        mix_spec=None,
    )

    assert [command[0] for command in calls] == ["ffmpeg", "ffmpeg", "ffmpeg"]
    concat_cmd = " ".join(calls[0])
    assert concat_cmd.count("-i ") == 4
    assert "concat=n=7:v=0:a=1[out]" in concat_cmd


def test_stitch_segments_builds_music_mix_filtergraph_when_mix_spec_is_provided(tmp_path):
    intro = tmp_path / "intro.mp3"
    outro = tmp_path / "outro.mp3"
    intro.write_bytes(b"INTRO-STINGER")
    outro.write_bytes(b"OUTRO-STINGER")
    output_path = tmp_path / "episode.mp3"
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        joined = " ".join(command)
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, "1.25\n", "")
        if "print_format=json" in joined:
            return _completed(stderr=measure_json)
        if command[-1] == str(output_path):
            output_path.write_bytes(b"ID3-final-mp3")
        return _completed()

    audio.stitch_segments(
        [b"seg-a", b"seg-b", b"seg-c"],
        output_path,
        runner=runner,
        gap_seconds=0.3,
        intro_music=intro,
        outro_music=outro,
        mix_spec=audio.MusicMixSpec(),
    )

    assert [command[0] for command in calls[:3]] == ["ffprobe", "ffprobe", "ffprobe"]
    # calls[3] = concat ffmpeg, calls[4] = outro ffprobe, calls[5] = mix ffmpeg
    mix_cmd = " ".join(calls[5])
    assert "adelay=10000:all=1" in mix_cmd
    assert "atrim=end=17.8" in mix_cmd
    assert "apad=whole_dur" not in mix_cmd
    assert "volume='if(lt(t,8)" in mix_cmd
    assert ":eval=frame" in mix_cmd
    assert "[speech][intro]amix=inputs=2:normalize=0:duration=first:weights='1 1'[speech_with_intro]" in mix_cmd
    # Outro offset clamped: min(75, max(0, 1.25-0.5))=0.75
    assert "atrim=start=0.75" in mix_cmd
    assert "volume='if(lt(t,2.8),0.1*t/2.8" in mix_cmd
    assert "[speech_with_intro][outro]amix=inputs=2:normalize=0:duration=longest:weights='1 1'[out]" in mix_cmd


def test_outro_volume_expression_holds_duck_then_ramps_to_full_volume():
    expression = audio._outro_volume_expression(2.8, audio.MusicMixSpec())

    # New envelope: fade from 0 to 0.1 over min(3.0, 2.8)=2.8s, then ramp to 1.0
    assert expression == "if(lt(t,2.8),0.1*t/2.8,if(lt(t,2.8),0.1,if(lt(t,7.8),0.1+(1-0.1)*(t-2.8)/5,1)))"


def test_outro_volume_expression_returns_full_volume_when_no_speech_overlap():
    assert audio._outro_volume_expression(0.0, audio.MusicMixSpec()) == "1"


@pytest.mark.skipif(not _has_ffmpeg, reason="ffmpeg not installed")
def test_stitch_segments_with_mix_spec_extends_program_duration(tmp_path):
    def make_mp3(path: Path, *, duration: float, frequency: int) -> bytes:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration={duration}",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "96k",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return path.read_bytes()

    def duration_seconds(path: Path) -> float:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    speech_a = make_mp3(tmp_path / "speech-a.mp3", duration=0.8, frequency=880)
    speech_b = make_mp3(tmp_path / "speech-b.mp3", duration=0.8, frequency=990)
    music = tmp_path / "music.mp3"
    make_mp3(music, duration=4.0, frequency=220)

    plain_output = tmp_path / "plain.mp3"
    mixed_output = tmp_path / "mixed.mp3"
    audio.stitch_segments([speech_a, speech_b], plain_output, gap_seconds=0.1)
    audio.stitch_segments(
        [speech_a, speech_b],
        mixed_output,
        gap_seconds=0.1,
        intro_music=music,
        outro_music=music,
        mix_spec=audio.MusicMixSpec(
            intro_full_volume_seconds=1.0,
            intro_fade_duration_seconds=0.5,
            intro_speech_segments_under_music=1,
            outro_start_offset_seconds=1.0,
            outro_fade_in_seconds=0.5,
            outro_speech_segments_with_music=1,
        ),
    )

    assert duration_seconds(mixed_output) > duration_seconds(plain_output) + 1.0


def test_stitch_segments_rejects_invalid_music_mix_specs(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        audio.MusicMixSpec(outro_fade_in_seconds=0)

    intro = tmp_path / "intro.mp3"
    intro.write_bytes(b"INTRO-STINGER")
    with pytest.raises(ValueError, match="cannot exceed the speech segment count"):
        audio.stitch_segments(
            [b"seg-a"],
            tmp_path / "episode.mp3",
            runner=lambda command: _completed(),
            intro_music=intro,
            mix_spec=audio.MusicMixSpec(intro_speech_segments_under_music=2),
        )
