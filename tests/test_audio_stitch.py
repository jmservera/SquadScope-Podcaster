from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from podcaster import audio


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
