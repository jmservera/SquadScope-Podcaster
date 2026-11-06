"""Finite offline comparison of canonical-video remux versus redundant encode."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from podcaster.video.process import run_owned_process
from podcaster.video.video_compose import (
    ENCODE_CRF,
    ENCODE_PIX_FMT,
    ENCODE_PRESET,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    _build_canonical_av_cmd,
    _encode_tail,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=12, choices=range(1, 61))
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise SystemExit("use a new empty evidence directory")
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
    results = {}

    def run(name, command, output):
        samples = []
        run_owned_process(
            command,
            timeout_seconds=240,
            output_paths=[output],
            check=True,
            on_metrics=samples.append,
        )
        results[name] = samples[-1]

    source = work / "canonical-source.mp4"
    run(
        "generate",
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:rate={OUTPUT_FPS}",
            "-t",
            str(args.seconds),
            *_encode_tail(ENCODE_PRESET),
            str(source),
        ],
        source,
    )
    outputs = {}
    for label, copy in (("legacy_encode", False), ("canonical_copy", True)):
        output = work / f"{label}.mp4"
        outputs[label] = output
        run(
            label,
            _build_canonical_av_cmd(source, output, has_audio=False, canonical_video=copy),
            output,
        )

    def packets(path):
        return run_owned_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            timeout_seconds=30,
            check=True,
        ).stdout.strip()

    assert packets(source) == packets(outputs["canonical_copy"])
    streams = json.loads(
        run_owned_process(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(outputs["canonical_copy"]),
            ],
            timeout_seconds=30,
            check=True,
        ).stdout
    )["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["width"], video["height"], video["r_frame_rate"], video["pix_fmt"]) == (
        OUTPUT_WIDTH,
        OUTPUT_HEIGHT,
        f"{OUTPUT_FPS}/1",
        ENCODE_PIX_FMT,
    )
    assert audio["codec_name"] == "aac" and audio["channels"] == 2
    for label, output in outputs.items():
        run(
            f"{label}_full_decode",
            [
                "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-err_detect",
                "explode",
                "-i",
                str(output),
                "-f",
                "null",
                "-",
            ],
            work / "no-output",
        )
    report = {
        "fixture_seconds": args.seconds,
        "source": "synthetic moving testsrc2, offline/no provider/no TTS",
        "resolution": [OUTPUT_WIDTH, OUTPUT_HEIGHT],
        "preset": ENCODE_PRESET,
        "crf": ENCODE_CRF,
        "copied_video_packet_hashes_identical": True,
        "full_decode_both_passed": True,
        "resources": results,
        "limits": (
            "CPU/read/write samples are process-local lower bounds, not exact I/O wait. "
            "This compares only two removed canonical encodes; it does not demonstrate "
            "complete 486s production composition within the unchanged render/epoch bounds."
        ),
    }
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
