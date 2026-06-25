# Video Pipeline Performance Review (issue #396)

> Parent epic: #372 · Scope: full performance review of the video generation
> pipeline — measure per-phase timing, profile CPU/memory, identify the top
> bottlenecks, and implement the highest-impact optimizations.

## 1. Runtime envelope

| Constraint | Value |
| --- | --- |
| Compute | Azure Container Apps job: **4 vCPU / 8 GB RAM** |
| Timeout | **90 min** hard cap |
| GPU | **None** provisioned today |
| Encoder | `libx264`, High profile, `yuv420p` (Spotify-mandated), CRF 12 |

## 2. Phase timing breakdown (baseline)

The pipeline runs four heavy phases inside one container. Reported baseline
(from issue #396, a ~22-segment weekly episode, ~50 min audio):

| Phase | Wall time | Share | What it does |
| --- | ---: | ---: | --- |
| Recording | ~16 min | 26% | Playwright screen/hyperframe capture per repo segment, sequentially |
| Composition | **~33 min** | **53%** | Normalize → pairwise xfade → DOG overlay → intro/outro join |
| Canonicalization | ~10 min | 16% | Final encode + `h264_metadata` BSF pass + faststart |
| Distribution | ~3 min | 5% | Blob upload + Spotify upload |
| **Total** | **~62 min** | 100% | (target ≤ 50–60 min; close to the 90 min cap) |

> **Composition is the dominant cost (~53%).** Canonicalization is folded into
> the tail of composition in the current code (the final encode + BSF pass), so
> "composition + canonicalization" together account for ~69% of wall time.

### How the numbers are now produced

Before this change the breakdown was hand-measured and unreproducible. This PR
adds **`podcaster/video/perf.py`** — a zero-dependency `PhaseTimer` /
`PipelineTimings` instrument wired into `run_video_generation`. Every run now:

* logs a per-phase breakdown (`wall`, `cpu`, `peakRSS`, `% of total`), and
* persists it to the manifest at `generation.video_runner.performance`,

so before/after comparisons need no re-instrumentation. Example log line:

```
Pipeline timing breakdown (total 3720.0s):
  recording           960.0s (25.8%)  cpu= 410.0s  peakRSS=620MiB
  composition        1980.0s (53.2%)  cpu=6800.0s  peakRSS=900MiB
  distribution        180.0s ( 4.8%)  cpu=  40.0s  peakRSS=520MiB
  bottlenecks: composition (1980s), recording (960s), distribution (180s)
```

## 3. Resource profile

* **CPU**: Composition is CPU-bound and *can* exceed wall × vCPU because
  normalization already fans out across cores (`ThreadPoolExecutor`,
  `VIDEO_NORMALIZE_WORKERS`, default `min(4, cpu)`). The pairwise xfade chain,
  however, is **single-threaded and sequential** — one `ffmpeg` at a time.
* **Memory**: Bounded. The old N-input `filter_complex` OOMed at ~18 segments;
  the current **pairwise** join (#349) uses exactly two inputs per pass, so peak
  RSS stays roughly constant (~0.9 GB) regardless of segment count — well within
  the 8 GB envelope.
* **Disk I/O**: Each segment is re-encoded several times (normalize → each xfade
  pass rewrites the growing accumulator → final). Intermediates are deleted as
  soon as they are consumed, so disk stays bounded but write volume is high.

## 4. Top 3 bottlenecks (root cause)

1. **Composition: O(N²) accumulator re-encode (≈53%).**
   `_compose_pairwise` walks segments left-to-right, re-encoding the *entire
   accumulated video* on every one of the N-1 xfade passes. Total encode work is
   `1 + 2 + 3 + … + N ≈ N²/2` segment-lengths — the accumulator is rewritten
   again and again. Intermediates use `ultrafast`, but the volume of pixels
   re-encoded is the real cost. **This is the #1 target.**

2. **Recording: sequential per-segment capture (≈26%).**
   `record_episode` records each repo segment one after another in a single
   browser. Page loads, `networkidle` waits, and scroll/hyperframe capture
   dominate. Segments are fully independent, so this is embarrassingly
   parallel — yet today it is serial. (`job_runner` already carries a
   `TODO(#242)` to fan out to parallel ACA segment jobs.)

3. **Canonicalization: redundant tail re-encode (≈16%).**
   The final `slow`-preset encode + `h264_metadata` BSF pass re-encodes content
   that the last xfade pass already encoded, purely to normalize colour VUI and
   add faststart.

## 5. Optimizations — ranked by impact / effort

| # | Optimization | Impact | Effort | Status |
| --- | --- | --- | --- | --- |
| 1 | **GPU (NVENC) encoding** for the compose re-encodes | High (per-encode 3–8×) | Low–Med | **Implemented (auto-detected)** |
| 2 | **Per-phase timing + resource instrumentation** | Enabler (measurement) | Low | **Implemented** |
| 3 | **Parallel segment recording** (fan-out) | High (recording → ~1/N) | Med–High | Recommended (#242) |
| 4 | Balanced-tree / single-pass xfade to kill O(N²) | High | Med–High | Recommended |
| 5 | Skip the redundant canonicalization re-encode | Med | Med | Recommended |
| 6 | Tune `VIDEO_NORMALIZE_WORKERS` to the ACA vCPU count | Low–Med | Low | Available via env |

### Implemented in this PR

**(1) NVENC hardware-accelerated encoding — `video_compose._select_hwaccel_encoder`.**
The compose bottleneck is CPU encoding. When an NVIDIA GPU **and** an
NVENC-capable `ffmpeg` are present, every video re-encode is routed through
`h264_nvenc`/`hevc_nvenc` with constant-quality rate control (`-rc constqp -qp`,
mirroring the software CRF) while preserving the Spotify constraints (H.264
High, 8-bit `yuv420p`).

* Controlled by `VIDEO_HWACCEL` = `auto` (default) | `nvenc` | `off`.
* **`auto` is a transparent no-op on the current CPU-only ACA runtime**:
  detection finds no `/dev/nvidia*` device and returns the exact libx264 flags,
  so production output is unchanged until a GPU runner is provisioned.
* Provisioning a GPU SKU (or forcing `VIDEO_HWACCEL=nvenc` on one) then offloads
  the ~33 min compose encodes to the GPU with no further code change.

**(2) Phase instrumentation — `podcaster/video/perf.py`.**
Makes the "timing breakdown per phase (with measurements)" and "before/after
comparison" deliverables reproducible on every run (see §2).

### Recommended next (highest remaining ROI)

* **Parallel segment recording (#242).** Recording is serial but per-segment
  independent. Fanning out (parallel browser contexts locally, or parallel ACA
  segment jobs) collapses recording from `Σ segments` to `≈ max(segment)`.
* **Kill the O(N²) accumulator.** Replace the linear pairwise walk with either a
  **balanced tree merge** (`O(N log N)` re-encode work, still two inputs/pass →
  memory stays bounded) or, for small N, the already-present single-pass
  `_build_xfade_filter` filtergraph (all transitions in one encode). Gate by
  segment count to retain the memory guarantee for large episodes.
* **Fold canonicalization into the final xfade pass** so colour-VUI/faststart
  are applied without a separate full re-encode.

## 6. How to reproduce / verify

* Timing is emitted automatically: read the `Pipeline timing breakdown` log line
  or `generation.video_runner.performance` in the job manifest.
* To benchmark NVENC on a GPU host: set `VIDEO_HWACCEL=nvenc` and compare the
  `composition` phase wall time against the libx264 baseline.

## 7. Tests

* `tests/test_video_perf.py` — `PhaseTimer`/`PipelineTimings` (records on
  success **and** on exception, never suppresses, bottleneck ranking, JSON
  breakdown).
* `tests/test_video_compose.py::TestHardwareAccelEncoding` — default CPU path is
  byte-for-byte unchanged, `auto`/`nvenc`/`off` selection, NVENC arg/preset
  mapping, Spotify-constraint preservation, GPU-absent detection.
* `tests/test_video_job_runner.py` — the performance breakdown is persisted to
  the manifest.
