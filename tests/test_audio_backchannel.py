from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from podcaster import audio


def _completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout=stdout, stderr=stderr)


def test_backchannel_mix_item_validates():
    with pytest.raises(ValueError):
        audio.BackchannelMixItem(Path("c.wav"), -1.0)
    with pytest.raises(ValueError):
        audio.BackchannelMixItem(Path("c.wav"), 1.0, gain_db=3.0)
    with pytest.raises(ValueError):
        audio.BackchannelMixItem(Path("c.wav"), 1.0, max_duration_ms=0)


def test_build_backchannel_filter_complex_empty_passthrough():
    fc = audio.build_backchannel_filter_complex([])
    assert fc == "[0:a]aresample=44100,aformat=channel_layouts=mono[out]"


def test_build_backchannel_filter_complex_uses_two_input_amix_chain():
    items = [
        audio.BackchannelMixItem(Path("a.wav"), 5.0, gain_db=-16, max_duration_ms=600),
        audio.BackchannelMixItem(Path("b.wav"), 40.0, gain_db=-14, max_duration_ms=500),
    ]
    fc = audio.build_backchannel_filter_complex(items)

    # Base speech is input 0.
    assert "[0:a]aresample=44100,aformat=channel_layouts=mono[base]" in fc
    # Each clip is trimmed, gain-reduced with eval=frame, and delayed.
    assert "atrim=end=0.6" in fc
    assert "adelay=5000:all=1[bc0]" in fc
    assert "adelay=40000:all=1[bc1]" in fc
    assert ":eval=frame" in fc
    # Two-input amix chain (never an N-input amix) to avoid amplitude dilution.
    assert fc.count("amix=inputs=2:normalize=0:duration=first:weights='1 1'") == 2
    assert "inputs=3" not in fc
    assert "[base][bc0]amix=inputs=2:normalize=0:duration=first:weights='1 1'[mix0]" in fc
    assert "[mix0][bc1]amix=inputs=2:normalize=0:duration=first:weights='1 1'[out]" in fc


def test_backchannel_gain_db_window_maps_to_amplitude():
    # -14 dB ~= 0.1995, -18 dB ~= 0.1259 of full amplitude.
    item_quiet = audio.BackchannelMixItem(Path("a.wav"), 1.0, gain_db=-18)
    item_loud = audio.BackchannelMixItem(Path("a.wav"), 1.0, gain_db=-14)
    assert "0.125893" in audio._backchannel_volume_expression(item_quiet)
    assert "0.199526" in audio._backchannel_volume_expression(item_loud)


def test_mix_backchannels_builds_ffmpeg_command(tmp_path):
    speech = tmp_path / "speech.wav"
    speech.write_bytes(b"RIFFspeech")
    clip = tmp_path / "right.wav"
    clip.write_bytes(b"RIFFright")
    out = tmp_path / "mixed.wav"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed()

    result = audio.mix_backchannels(
        speech,
        [audio.BackchannelMixItem(clip, 12.0, gain_db=-16, max_duration_ms=600)],
        out,
        runner=runner,
    )
    assert result == out
    cmd = " ".join(calls[0])
    assert cmd.count("-i ") == 2  # speech + one clip
    assert "adelay=12000:all=1[bc0]" in cmd
    assert "-map [out]" in cmd


def test_render_distribution_audio_overlays_backchannels(tmp_path):
    wav = tmp_path / "episode.wav"
    mp3 = tmp_path / "episode.mp3"
    clip = tmp_path / "right.wav"
    clip.write_bytes(b"RIFFright")
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        joined = " ".join(command)
        if "print_format=json" in joined:
            return _completed(stderr=measure_json)
        if command[-1] == str(mp3):
            mp3.write_bytes(b"ID3-final")
        return _completed()

    audio.render_distribution_audio(
        [b"seg-a", b"seg-b"],
        wav,
        mp3,
        runner=runner,
        gap_seconds=0.3,
        backchannels=[audio.BackchannelMixItem(clip, 1.0, gain_db=-16, max_duration_ms=600)],
    )

    joined_all = [" ".join(c) for c in calls]
    # One of the ffmpeg passes must overlay the backchannel clip.
    assert any("adelay=1000:all=1[bc0]" in j for j in joined_all)


def test_render_distribution_audio_without_backchannels_unchanged(tmp_path):
    wav = tmp_path / "episode.wav"
    mp3 = tmp_path / "episode.mp3"
    measure_json = (
        '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
        '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "print_format=json" in " ".join(command):
            return _completed(stderr=measure_json)
        if command[-1] == str(mp3):
            mp3.write_bytes(b"ID3-final")
        return _completed()

    audio.render_distribution_audio([b"seg-a", b"seg-b"], wav, mp3, runner=runner)

    joined_all = [" ".join(c) for c in calls]
    assert not any("[bc0]" in j for j in joined_all)
    # Concat + loudnorm measure + loudnorm apply + mp3 encode = 4 passes.
    assert len(calls) == 4


def test_render_distribution_audio_reuses_precomputed_segment_durations(tmp_path):
    wav = tmp_path / "episode.wav"
    mp3 = tmp_path / "episode.mp3"
    durations: list[float] = []
    calls: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[0] == "ffprobe":
            pytest.fail("precomputed durations should avoid ffprobe")
        if "print_format=json" in " ".join(command):
            return _completed(
                stderr=(
                    '{ "input_i" : "-17.4", "input_tp" : "-1.0", "input_lra" : "4.3", '
                    '"input_thresh" : "-27.6", "target_offset" : "0.2" }'
                )
            )
        if command[-1] == str(mp3):
            mp3.write_bytes(b"ID3-final")
        return _completed()

    audio.render_distribution_audio(
        [b"seg-a", b"seg-b"],
        wav,
        mp3,
        runner=runner,
        segment_durations_out=durations,
        precomputed_segment_durations=[1.25, 2.5],
    )

    assert durations == [1.25, 2.5]
